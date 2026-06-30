"""Structured logging with secret redaction.

A logging filter scrubs anything that looks like a credential from rendered log
records, as a defence-in-depth backstop against accidental secret leakage. The
real protection is never passing secrets to log calls in the first place.
"""

from __future__ import annotations

import logging
import re

import structlog

# Patterns that should never reach a log sink. Matches common token shapes and
# any explicitly named secret keys.
_SECRET_KEY_RE = re.compile(
    r"(?i)(?P<k>api[_-]?key|token|secret|password|authorization|webhook)"
    r"(?P<sep>\s*[=:]\s*)(?P<v>\S+)"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")
_LONG_TOKEN_RE = re.compile(r"\b(?:gsk_|sk-|xoxb-)[A-Za-z0-9._\-]{8,}")

_REDACTED = "***REDACTED***"


def _scrub(text: str) -> str:
    text = _SECRET_KEY_RE.sub(lambda m: f"{m['k']}{m['sep']}{_REDACTED}", text)
    text = _BEARER_RE.sub(f"Bearer {_REDACTED}", text)
    text = _LONG_TOKEN_RE.sub(_REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _scrub(record.msg)
        if record.args:
            record.args = tuple(
                _scrub(a) if isinstance(a, str) else a for a in record.args
            )
        return True


def _redact_processor(_logger, _name, event_dict):
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            event_dict[key] = _scrub(value)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configure stdlib + structlog to emit redacted, structured JSON logs."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    logging.basicConfig(level=log_level, handlers=[handler], force=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
