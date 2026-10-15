"""
Structured JSON logging for Cloud Logging compatibility.

Cloud Run automatically ships stdout to Cloud Logging.  When records are
emitted as JSON with the `severity` field, Cloud Logging parses them natively
and enables log-level filtering, alerting, and trace correlation in the
Google Cloud Console.

In local development (non-Cloud Run environment) the formatter falls back to a
human-readable format so logs are readable without JSON parsing.
"""
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

# Fields that belong to the LogRecord internals — we skip them when forwarding
# extra fields to avoid cluttering the JSON payload.
_RESERVED_LOG_RECORD_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "taskName",
    }
)

_SEVERITY_MAP = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class CloudLoggingFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON compatible with Cloud Logging.

    Any keyword arguments passed to ``logger.info(..., extra={...})`` are
    promoted to top-level JSON fields, making them queryable in Log Explorer.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Ensure exc_text is populated if needed
        super().format(record)

        payload: dict[str, Any] = {
            "severity": _SEVERITY_MAP.get(record.levelno, "INFO"),
            "message": record.getMessage(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": record.name,
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Promote extra fields to the top-level JSON object
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str)


class HumanReadableFormatter(logging.Formatter):
    """Simple coloured formatter for local development."""

    _LEVEL_COLOURS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[35m",  # magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        colour = self._LEVEL_COLOURS.get(record.levelname, "")
        reset = self._RESET
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        base = (
            f"{ts} {colour}{record.levelname:<8}{reset} "
            f"[{record.name}] {record.getMessage()}"
        )
        # Append extra fields inline
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _RESERVED_LOG_RECORD_ATTRS and not k.startswith("_")
        }
        if extras:
            base += f" | {extras}"
        if record.exc_info:
            base += f"\n{self.formatException(record.exc_info)}"
        return base


def setup_logging(log_level: str = "INFO", use_json: bool = True) -> None:
    """Configure root logger.  Called once at application startup."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        CloudLoggingFormatter() if use_json else HumanReadableFormatter()
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = []
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
