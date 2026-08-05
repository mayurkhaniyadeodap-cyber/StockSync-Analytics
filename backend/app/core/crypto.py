"""Symmetric encryption for third-party credentials held at rest.

The Shopify Admin API token is not like a password. A password is only ever
*verified*, so it can be hashed one way and never recovered. This token has to
be replayed to Shopify on every call, so it must be recoverable — which means
encryption, not hashing, and a key that lives outside the database.

Fernet (AES-128-CBC + HMAC-SHA256, authenticated) is used rather than raw AES:
it refuses to decrypt tampered ciphertext instead of returning plausible
garbage, and it has no mode or IV for a caller to get wrong.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings
from app.core.errors import AppError


class CredentialUnreadableError(AppError):
    """The stored ciphertext will not decrypt under the current key.

    In practice this means STOCKSYNC_ENCRYPTION_KEY changed (or was never set,
    so a per-process key was used and the process restarted). There is no
    recovery other than reconnecting, and saying so is more useful than a 500.
    """

    code = "credential_unreadable"
    status_code = 409
    message = "The stored Shopify credential can't be read."
    next_step = "Disconnect the store and connect it again to store a fresh token."


def _cipher(settings: Settings) -> Fernet:
    try:
        return Fernet(settings.resolved_encryption_key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "STOCKSYNC_ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        ) from exc


def encrypt(settings: Settings, plaintext: str) -> str:
    """Encrypt a credential for storage. Returns url-safe base64 text."""
    return _cipher(settings).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(settings: Settings, ciphertext: str) -> str:
    """Recover a stored credential, or raise CredentialUnreadableError."""
    try:
        return _cipher(settings).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise CredentialUnreadableError from exc
