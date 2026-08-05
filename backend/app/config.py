"""Application settings, read once from the environment at import time."""

from __future__ import annotations

import base64
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "production"]

# Repo root, resolved from this file so it does not depend on the working
# directory: backend/app/config.py -> backend/app -> backend -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SQLITE_PATH = REPO_ROOT / "data" / "stocksync.db"

# Regenerated on every process start. Only ever used when jwt_secret is unset,
# which production forbids.
_EPHEMERAL_SECRET = secrets.token_urlsafe(48)

# Same contract for the Shopify token key. Fernet requires 32 url-safe base64
# encoded bytes, so this is generated to that shape rather than as a free string.
_EPHEMERAL_FERNET_KEY = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


class Settings(BaseSettings):
    """Every value the app reads from the environment.

    Names map to ``STOCKSYNC_*`` env vars. See ``.env.example`` at the repo root
    for the documented template.
    """

    model_config = SettingsConfigDict(
        env_prefix="STOCKSYNC_",
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = "development"
    debug: bool = False
    api_prefix: str = "/api"

    # SQLite by default. Nothing in the application layer is SQLite-specific:
    # dialect differences are confined to db/session.py (engine construction and
    # connection pragmas) and alembic/env.py (batch migrations). Pointing this at
    # postgresql+psycopg://... is intended to be the only change needed to move.
    database_url: str = f"sqlite+pysqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"

    # Ignored by SQLite, which is not a client/server database. Kept so the
    # setting exists unchanged if the URL is later pointed at a server.
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=50)

    # How long a writer waits for another writer's lock before giving up.
    # SQLite serialises writes; a sync job and a user save can collide.
    #
    # 30s rather than 10s because of the one write this application cannot make
    # short: swapping in a rebuilt rollup, measured at ~11s for half a million
    # rows. The rebuild itself no longer holds the lock (see
    # `metrics.refresh` — it stages into a temporary table first), but the swap
    # has to be one transaction or a reader would see a half-replaced rollup.
    # The timeout has to exceed it, or a token renewal arriving as the swap
    # begins gives up before it ends and returns a 500.
    db_busy_timeout_seconds: float = Field(default=30.0, ge=0.5, le=120.0)

    # NoDecode is load-bearing. Without it, pydantic-settings JSON-decodes any
    # complex-typed field coming from a dotenv file *before* validators run, so a
    # plain `STOCKSYNC_CORS_ORIGINS=http://localhost:5173` raises a parse error
    # and the app refuses to start. NoDecode hands the raw string to
    # `_split_origins` below instead.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # --- auth ---
    # No usable default in production: an unset secret would silently sign every
    # session with a value an attacker can read in the source. Validated below.
    jwt_secret: str = ""
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_minutes: int = Field(default=15, ge=1, le=1440)
    refresh_token_days: int = Field(default=30, ge=1, le=365)
    # "Keep me signed in" unchecked: the refresh cookie becomes a session cookie
    # and the refresh row still expires after this many hours.
    refresh_token_session_hours: int = Field(default=12, ge=1, le=168)
    cookie_domain: str | None = None
    cookie_secure: bool = False  # True in production; requires HTTPS
    #: Acknowledges, in writing, a production deployment that serves over plain
    #: HTTP. Without it `_require_secure_cookies_in_production` refuses to start.
    allow_insecure_cookies: bool = False

    # --- login throttling ---
    #: Failed attempts, per account and per client address, before the next one
    #: is refused outright. Argon2 costs ~95 ms, which is a price ceiling per
    #: connection rather than a control — concurrency defeats it.
    login_max_attempts: int = Field(default=8, ge=1, le=100)
    #: How long the failures are counted over. A window rather than a running
    #: total, so an honest user who mistyped twice last week is not one attempt
    #: from a lockout today.
    login_attempt_window_seconds: int = Field(default=300, ge=10, le=86_400)
    #: How long a lockout lasts once the threshold is crossed.
    login_lockout_seconds: int = Field(default=900, ge=10, le=86_400)

    # --- security headers ---
    #: `Strict-Transport-Security` max-age. Only sent when cookies are secure —
    #: sending HSTS from a plain-HTTP deployment would pin browsers to a scheme
    #: it does not serve. Zero disables the header entirely.
    hsts_max_age_seconds: int = Field(default=63_072_000, ge=0, le=63_072_000)

    # --- M2 ---
    # Fernet key encrypting the Shopify Admin API token at rest. Same rule as
    # jwt_secret: required in production, ephemeral elsewhere. An unset key in
    # production would mean tokens sit in the database under a value published
    # in this repo's history.
    encryption_key: str = ""

    # Design doc §8.2: "Supports .csv, .xlsx, .xls up to 25MB".
    max_upload_mb: int = Field(default=25, ge=1, le=200)

    # --- M3 ---
    # The Google Sheet export fetch. Shorter than the Shopify timeout: this one
    # is spent inside a request the user is watching, and a link that has not
    # answered in twenty seconds is a link that is not going to.
    import_url_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)

    # --- shopify ---
    # The one group of settings read from unprefixed names. These are Shopify's
    # own variable names, used verbatim in its documentation and in every
    # integration example, so a value can be pasted straight from the page that
    # generated it. `validation_alias` replaces the env_prefix outright — there
    # is exactly one spelling for each of these four.
    shopify_store_url: str = Field(default="", validation_alias="SHOPIFY_STORE_URL")
    shopify_admin_api_token: str = Field(default="", validation_alias="SHOPIFY_ADMIN_API_TOKEN")
    # Shopify's Admin API is versioned by date; pinning avoids a silent
    # behaviour change when Shopify promotes a new stable version. Shopify
    # supports a release for about a year, so this wants revisiting roughly
    # annually rather than being left to expire.
    shopify_api_version: str = Field(default="2026-07", validation_alias="SHOPIFY_API_VERSION")
    shopify_timeout_seconds: float = Field(
        default=15.0, ge=1.0, le=120.0, validation_alias="SHOPIFY_TIMEOUT_SECONDS"
    )

    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    @field_validator("shopify_api_version", "shopify_timeout_seconds", mode="before")
    @classmethod
    def _blank_means_default(cls, value: object, info: ValidationInfo) -> object:
        """Treat an empty value as absent rather than as a parse error.

        ``.env`` is edited by hand, and clearing a setting's value is the
        obvious way to say "use the default". Without this,
        ``SHOPIFY_TIMEOUT_SECONDS=`` stops the API booting with a float-parsing
        error that names pydantic rather than the line that caused it.

        The default is read back off the field so it is stated in exactly one
        place — the field declaration.
        """
        if isinstance(value, str) and not value.strip() and info.field_name:
            return cls.model_fields[info.field_name].get_default()
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string as well as a list.

        ``.env`` files carry strings, so ``STOCKSYNC_CORS_ORIGINS=a,b`` has to work.
        """
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _require_explicit_driver(cls, value: str) -> str:
        """Reject bare scheme URLs that resolve to a driver we do not install.

        ``postgresql://`` silently selects psycopg2, which is absent; the
        resulting ImportError points at the wrong problem.
        """
        if value.startswith("postgresql://"):
            raise ValueError(
                "STOCKSYNC_DATABASE_URL must name the driver explicitly: "
                "postgresql+psycopg://... (and psycopg must be installed)"
            )

        # Anchor a relative SQLite path to the repo root rather than the current
        # working directory. Otherwise `alembic upgrade head` run from backend/
        # and a server started from the repo root would quietly use two
        # different database files — the schema would look applied from one
        # directory and missing from the other.
        prefix, sep, location = value.partition(":///")
        if sep and prefix.split("+", 1)[0] == "sqlite":
            bare = location.split("?", 1)[0]
            if bare and bare != ":memory:" and not Path(bare).is_absolute():
                resolved = (REPO_ROOT / bare).resolve()
                query = location[len(bare) :]
                return f"{prefix}:///{resolved.as_posix()}{query}"

        return value

    @model_validator(mode="after")
    def _require_jwt_secret_in_production(self) -> Settings:
        if self.env == "production" and len(self.jwt_secret) < 32:
            raise ValueError(
                "STOCKSYNC_JWT_SECRET must be at least 32 characters in production. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self

    @model_validator(mode="after")
    def _require_encryption_key_in_production(self) -> Settings:
        if self.env == "production" and not self.encryption_key:
            raise ValueError(
                "STOCKSYNC_ENCRYPTION_KEY is required in production — it encrypts the "
                "Shopify Admin API token at rest. Generate one with: python -c "
                '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        return self

    @model_validator(mode="after")
    def _require_secure_cookies_in_production(self) -> Settings:
        """Refuse to start a production server that sends session cookies in clear.

        The two settings above are enforced; this one was not, which made it the
        one a deployment could get wrong by *omission* rather than by choice —
        and the cost is the whole session, readable by anyone on the path.

        The override exists because a production deployment behind a TLS-
        terminating proxy on a trusted network is a real configuration, not a
        mistake. What it is not allowed to be is the default: turning it off has
        to be a decision someone typed.
        """
        if self.env == "production" and not self.cookie_secure and not self.allow_insecure_cookies:
            raise ValueError(
                "STOCKSYNC_COOKIE_SECURE must be true in production — without it the "
                "session and refresh cookies travel unencrypted and anyone on the "
                "network path can take the session.\n"
                "Set STOCKSYNC_COOKIE_SECURE=true (requires HTTPS), or, if TLS really "
                "does terminate elsewhere and the hop is trusted, acknowledge it "
                "explicitly with STOCKSYNC_ALLOW_INSECURE_COOKIES=true."
            )
        return self

    @property
    def resolved_encryption_key(self) -> str:
        """The Fernet key, with a per-process fallback outside production.

        Restarting the dev server therefore makes previously stored Shopify
        tokens undecryptable. That is deliberate and mirrors ``resolved_jwt_secret``:
        a hard-coded fallback would be a published key. The service treats a
        decryption failure as "reconnect the store", which is the honest state.
        """
        return self.encryption_key or _EPHEMERAL_FERNET_KEY

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def has_env_shopify_credential(self) -> bool:
        """True when .env carries a complete store URL + token pair."""
        return bool(self.shopify_store_url.strip() and self.shopify_admin_api_token.strip())

    @property
    def env_shopify_credential_active(self) -> bool:
        """Whether that pair may actually be used.

        Development only, deliberately. A token in .env sits in plaintext on
        disk, which is a step down from the encrypted-at-rest storage the
        connection flow uses — acceptable when it saves re-entering a
        development credential after every database reset, not acceptable as
        the way a deployment holds its production secret. In production the
        pair is ignored and start-up says so rather than failing silently.
        """
        return self.has_env_shopify_credential and not self.is_production

    @property
    def resolved_jwt_secret(self) -> str:
        """The signing key, with a per-process fallback outside production.

        Development and test get a random secret rather than a hard-coded one, so
        a forgotten .env cannot ship a publicly known key. The cost is that
        restarting the dev server invalidates existing sessions, which is the
        correct trade.
        """
        return self.jwt_secret or _EPHEMERAL_SECRET

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def dialect(self) -> str:
        """Backend name from the URL: 'sqlite', 'postgresql', ..."""
        return self.database_url.split(":", 1)[0].split("+", 1)[0]

    @property
    def is_sqlite(self) -> bool:
        return self.dialect == "sqlite"

    def sqlite_path(self) -> Path | None:
        """Filesystem path of the SQLite database, or None for other dialects.

        Returns None for in-memory databases, which have no file to create.
        """
        if not self.is_sqlite:
            return None
        _, _, location = self.database_url.partition(":///")
        location = location.split("?", 1)[0]
        if not location or location == ":memory:":
            return None
        return Path(location)

    def safe_database_url(self) -> str:
        """The database URL with its password masked, for logs and health output."""
        url = self.database_url
        if "@" not in url or "//" not in url:
            return url
        scheme, _, rest = url.partition("//")
        credentials, _, host = rest.rpartition("@")
        if ":" not in credentials:
            return url
        user, _, _password = credentials.partition(":")
        return f"{scheme}//{user}:***@{host}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Call this rather than instantiating Settings."""
    return Settings()
