"""Fetching a stock sheet from a URL.

This module does one thing: turn a URL into bytes, or into an error that says
what went wrong. It deliberately knows nothing about parsing, validation or
inventory — the bytes go to ``imports.run_file_import``, the same function an
uploaded file goes through.

**Its caller is ``services.google_sheets``.** The generic CSV-URL import this was
originally written for (design doc §8.3) is gone: the product settled on file
upload and Google Sheets, and an endpoint the UI could not reach was worth more
removed than kept. What remains is the part a sheet export needs — the address
guard, the size cap and the redirect handling below — which is why this is still
a module of its own rather than folded into the sheet code.

Fetching a user-supplied URL server-side is the part that needs care. The server
can reach things the browser cannot — the loopback interface, the private network
the container sits on, a cloud metadata endpoint on 169.254.169.254 — so an
unguarded fetcher turns an import form into a request proxy. Every address is
therefore checked before a connection is opened, on the original URL and on each
redirect hop.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Final
from urllib.parse import unquote, urlparse

import httpx

from app.config import Settings
from app.core.errors import AppError

log = logging.getLogger(__name__)

#: Redirects are followed manually so each hop can be re-validated. Three is
#: enough for the http->https and bare-domain->www hops real links use.
MAX_REDIRECTS: Final = 3

#: Read in chunks so an oversized or endless response is abandoned rather than
#: buffered. 64 KiB is a compromise between syscall count and overshoot.
CHUNK_BYTES: Final = 64 * 1024

#: A link that answers with a web page is nearly always a sign-in wall or a
#: "preview this file" viewer. Detected so the message can say so, instead of the
#: parser reporting a missing SKU column in what it thinks is a CSV.
_HTML_LEADERS: Final = (b"<!doctype html", b"<html", b"<?xml", b"<!DOCTYPE HTML")


class InvalidImportUrlError(AppError):
    code = "invalid_import_url"
    status_code = 422
    message = "That doesn't look like a URL StockSync Analytics can fetch."
    next_step = "Paste a full http:// or https:// link to a CSV file."


class BlockedImportUrlError(AppError):
    """The URL resolved to an address the server must not fetch on request."""

    code = "blocked_import_url"
    status_code = 422
    message = "That link points inside the server's own network, so it wasn't fetched."
    next_step = "Use a publicly reachable link, or upload the file directly instead."


class ImportUrlUnreachableError(AppError):
    code = "import_url_unreachable"
    status_code = 502
    message = "That link couldn't be reached."
    next_step = "Check the URL opens in a browser, then try again."


class ImportUrlHttpError(AppError):
    code = "import_url_http_error"
    status_code = 502
    message = "That link returned an error instead of a file."
    next_step = "Open the URL in a browser to see what it returns, then try again."


class ImportUrlNotAFileError(AppError):
    code = "import_url_not_a_file"
    status_code = 422
    message = "That link returned a web page, not a CSV file."
    next_step = (
        "Use the direct download link. For a Google Sheet, that is File → Share → "
        "Publish to web → CSV."
    )


class ImportUrlTooLargeError(AppError):
    code = "import_url_too_large"
    status_code = 413
    message = "The file at that link is larger than the upload limit."
    next_step = "Split it into smaller files, or raise STOCKSYNC_MAX_UPLOAD_MB."


class ImportUrlEmptyError(AppError):
    code = "import_url_empty"
    status_code = 422
    message = "That link returned nothing."
    next_step = "Check the URL points at a file that still exists, then try again."


def fetch_csv(settings: Settings, url: str) -> tuple[str, bytes]:
    """Download the CSV at ``url``. Returns ``(filename, raw)``.

    ``filename`` is for the parser and for Import History; it always ends
    ``.csv`` because this is the CSV endpoint and the parser dispatches on the
    extension. A published Google Sheet has no extension in its path, so one is
    supplied rather than the download being refused for it.
    """
    target = _validated(url)
    filename = _filename_for(target)

    for hop in range(MAX_REDIRECTS + 1):
        redirect, raw = _get(settings, target)
        if redirect is None:
            return filename, _checked(raw, limit=settings.max_upload_bytes)
        if hop == MAX_REDIRECTS:
            break
        # Each hop is validated in full: the guard is worth nothing if a public
        # host can redirect to 127.0.0.1.
        target = _validated(redirect, base=target)

    raise ImportUrlHttpError(
        "That link redirects more times than StockSync Analytics will follow.",
        next_step="Use the URL the redirects end at.",
        detail={"max_redirects": MAX_REDIRECTS},
    )


def _get(settings: Settings, url: str) -> tuple[str | None, bytes]:
    """One GET. Returns ``(redirect_target, body)`` — exactly one is meaningful."""
    try:
        with httpx.Client(follow_redirects=False, timeout=settings.import_url_timeout_seconds) as c:
            with c.stream("GET", url, headers={"Accept": "text/csv, */*"}) as response:
                if response.is_redirect:
                    location = response.headers.get("Location", "")
                    if not location:
                        raise ImportUrlHttpError(
                            "That link redirected without saying where to.",
                            detail={"status": response.status_code},
                        )
                    return location, b""

                _raise_for_status(response.status_code)
                _reject_declared_size(response, limit=settings.max_upload_bytes)
                return None, _read_capped(response, limit=settings.max_upload_bytes)
    except httpx.HTTPError as exc:
        # DNS, TLS, connect and read timeouts are one problem from here: the link
        # did not answer. The exception type is logged, not returned — it can
        # carry internal hostnames.
        log.warning("import url fetch failed: %s", type(exc).__name__)
        raise ImportUrlUnreachableError from exc


def _raise_for_status(status: int) -> None:
    if status in (401, 403):
        raise ImportUrlHttpError(
            "That link needs a sign-in, so the file couldn't be downloaded.",
            next_step="Make the file publicly readable, or upload it directly instead.",
            detail={"status": status},
        )
    if status == 404:
        raise ImportUrlHttpError(
            "There's no file at that link.",
            next_step="Check the URL, then try again.",
            detail={"status": status},
        )
    if status >= 400:
        raise ImportUrlHttpError(detail={"status": status})


def _reject_declared_size(response: httpx.Response, *, limit: int) -> None:
    """Refuse on Content-Length before downloading, when the server declares one."""
    declared = response.headers.get("Content-Length")
    if declared is None:
        return
    try:
        size = int(declared)
    except ValueError:
        return
    if size > limit:
        raise ImportUrlTooLargeError(detail={"size_bytes": size, "limit_bytes": limit})


def _read_capped(response: httpx.Response, *, limit: int) -> bytes:
    """Read the body, abandoning it the moment it exceeds the limit.

    A server that lies about (or omits) Content-Length would otherwise decide how
    much memory this process uses.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes(CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise ImportUrlTooLargeError(detail={"limit_bytes": limit})
        chunks.append(chunk)
    return b"".join(chunks)


def _checked(raw: bytes, *, limit: int) -> bytes:
    if not raw.strip():
        raise ImportUrlEmptyError
    if len(raw) > limit:  # pragma: no cover - _read_capped catches this first
        raise ImportUrlTooLargeError(detail={"size_bytes": len(raw), "limit_bytes": limit})
    leader = raw.lstrip()[:64].lower()
    if any(leader.startswith(marker.lower()) for marker in _HTML_LEADERS):
        raise ImportUrlNotAFileError
    return raw


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _validated(url: str, *, base: str | None = None) -> str:
    """Parse, check and normalise a URL, or raise.

    ``base`` is the URL a redirect came from, so a relative Location resolves
    the way a browser would.
    """
    text = (url or "").strip()
    if not text:
        raise InvalidImportUrlError

    if base is not None and "://" not in text:
        text = str(httpx.URL(base).join(text))

    try:
        parsed = urlparse(text)
    except ValueError as exc:
        raise InvalidImportUrlError from exc

    if parsed.scheme.lower() not in ("http", "https"):
        raise InvalidImportUrlError(
            "Only http:// and https:// links can be fetched.",
            detail={"scheme": parsed.scheme[:20]},
        )
    host = parsed.hostname
    if not host:
        raise InvalidImportUrlError("That URL has no host in it.", detail={"received": text[:200]})

    _reject_internal_host(host, port=parsed.port)
    return text


def _reject_internal_host(host: str, *, port: int | None) -> None:
    """Refuse hosts that resolve anywhere but the public internet.

    Resolve-then-check leaves a small window: httpx resolves again when it
    connects, and a hostile DNS server could answer differently the second time.
    Closing it properly means pinning the socket to the address checked here,
    which needs a custom transport; for an authenticated internal portal the
    check plus per-hop revalidation is the proportionate guard, and the residual
    risk is recorded here rather than left implied.
    """
    try:
        resolved = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ImportUrlUnreachableError(
            "That link's address couldn't be looked up.",
            next_step="Check the domain is spelled correctly, then try again.",
            detail={"host": host[:120]},
        ) from exc

    addresses = {str(info[4][0]) for info in resolved}
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if _is_public(ip):
            continue
        log.warning("refused import url pointing at %s (%s)", host, ip)
        raise BlockedImportUrlError(detail={"host": host[:120]})


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for an address on the public internet.

    ``is_global`` alone is close, but 169.254.169.254 — the cloud metadata
    address, the single most valuable target here — is link-local, and IPv4
    addresses mapped into IPv6 (``::ffff:127.0.0.1``) report as global. Both are
    handled explicitly.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _filename_for(url: str) -> str:
    """A name for Import History, always ending ``.csv``."""
    path = unquote(urlparse(url).path or "")
    name = path.rsplit("/", 1)[-1].strip()
    # Drop anything that would read as a directory traversal or a path in the
    # history table; this string is displayed, never opened.
    name = name.replace("\\", "").replace("..", "").strip(". ")
    if not name:
        name = "download"
    if not name.lower().endswith(".csv"):
        name = f"{name}.csv"
    return name[:255]
