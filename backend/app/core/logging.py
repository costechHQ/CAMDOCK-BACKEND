"""Application logging that keeps secrets and personal data out of the log.

Rules (NFR-SEC-008): never log bodies, headers, query strings, credentials or tokens.
Exception messages can carry row values or SQL parameters, so unexpected errors are
logged by type and traceback frames only. A redaction filter is the safety net.
"""

import json
import logging
import re
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

HANDLER_MARK = "_camdock_handler"

REDACTIONS = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (
        re.compile(
            r"(?i)\b(password|passwd|token|secret|authorization|api[_-]?key)\b"
            r"(\s*[=:]\s*)[^\s,;&]+"
        ),
        r"\1\2[redacted]",
    ),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email redacted]"),
)


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def safe_traceback(exc: BaseException) -> str:
    """Type and frames (file, line, function) without the message or source lines."""
    frames = [
        f"  {frame.filename}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)
    ]
    return "\n".join([type(exc).__name__, *frames])


def log_unexpected(
    logger: logging.Logger, what: str, exc: BaseException, **fields: str
) -> None:
    """Log an unexpected exception without its message, which may hold data."""
    context = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.error("%s %s\n%s", what, context, safe_traceback(exc))


class ContextFilter(logging.Filter):
    """Stamps the current request id on each record (the record itself is not altered,
    so other handlers see exactly what the code logged)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _SafeExceptionMixin:
    def formatException(self, ei) -> str:
        return safe_traceback(ei[1])


class TextFormatter(_SafeExceptionMixin, logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
        )

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(_SafeExceptionMixin, logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            entry["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry)


def configure_logging(environment: str, level: str, stream=None) -> logging.Logger:
    """Attach one handler to the `app` logger; safe to call repeatedly."""
    logger = logging.getLogger("app")
    for handler in list(logger.handlers):
        if getattr(handler, HANDLER_MARK, False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    setattr(handler, HANDLER_MARK, True)
    handler.addFilter(ContextFilter())
    handler.setFormatter(
        JsonFormatter() if environment == "production" else TextFormatter()
    )
    logger.addHandler(handler)
    logger.setLevel(level)
    return logger
