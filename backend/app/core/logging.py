"""Logging setup, with a redaction filter applied to every record.

StockSync Analytics holds Shopify admin tokens and Google refresh tokens. The rule from the
brief is *never log tokens*, and the reliable way to honour that is to scrub at
the logging layer rather than trusting every future call site to remember.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sys
from typing import Any

# Patterns are deliberately broad. A false positive costs an unreadable log
# line; a false negative costs a leaked credential.
_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Shopify: admin API tokens, storefront tokens, shared secrets.
    (re.compile(r"shp(at|ca|pa|ss)_[A-Za-z0-9]+"), "shp\\1_***"),
    # Bearer / Basic auth headers.
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), "\\1 ***"),
    # key=value and "key": "value" for anything that smells secret.
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?token"
            r"|refresh[_-]?token|authorization|client[_-]?secret)"
            r"(\"?\s*[:=]\s*\"?)([^\s,;\"'}]+)"
        ),
        "\\1\\2***",
    ),
    # Credentials embedded in a connection URL.
    (re.compile(r"://([^:/@\s]+):([^@/\s]+)@"), "://\\1:***@"),
)


def redact(text: str) -> str:
    """Return ``text`` with anything credential-shaped replaced by ``***``."""
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Scrubs credentials from the message and args of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._scrub(a) for a in record.args)
        return True

    @staticmethod
    def _scrub(value: Any) -> Any:
        return redact(value) if isinstance(value, str) else value


class JsonFormatter(logging.Formatter):
    """Single-line JSON, for when logs are shipped somewhere that parses them."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    """Install handlers on the root logger. Safe to call more than once."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    # The Windows console defaults to cp1252, which mangles the em-dashes and ₹
    # that run through this product's copy. Force UTF-8 on the stream.
    stream = sys.stdout
    if hasattr(stream, "reconfigure"):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")

    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s  %(levelname)-7s %(name)-28s %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; make them inherit ours so access logs
    # are redacted too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # SQLAlchemy echoes full statements at INFO, which can include bound values.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
