"""Structured logging with request-ID propagation.

Every log line carries the request ID, so a failed Celery job traces back to the
HTTP request that started it (NFR-4). The ID lives in a ContextVar, which means
it follows the logical task across await points without being threaded through
every function signature.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import uuid4

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)  # fmt: skip


class SafeLogger(logging.Logger):
    """A Logger whose ``extra`` can never crash the request that used it.

    ``Logger.makeRecord`` raises ``KeyError`` if ``extra`` contains a reserved
    attribute name -- ``message``, ``args``, ``exc_info`` and friends. That
    turns a logging call into a 500, and the most natural key to reach for when
    logging an error is exactly the reserved one (``message``).

    Worse, the failure is invisible to the test suite: pytest's log capture
    suppresses it, so a handler that 500s in production still returns 401 under
    test. Rather than rely on catching each instance, colliding keys are
    renamed with a ``ctx_`` prefix so the data survives and the request does not
    fail.
    """

    def makeRecord(  # noqa: N802 — overriding a stdlib method; the name is fixed
        self,
        name: str,
        level: int,
        fn: str,
        lno: int,
        msg: object,
        args: Any,
        exc_info: Any,
        func: str | None = None,
        extra: Mapping[str, object] | None = None,
        sinfo: str | None = None,
    ) -> logging.LogRecord:
        safe = (
            {(f"ctx_{k}" if k in _RESERVED else k): v for k, v in extra.items()} if extra else None
        )
        return super().makeRecord(name, level, fn, lno, msg, args, exc_info, func, safe, sinfo)


logging.setLoggerClass(SafeLogger)


def new_request_id() -> str:
    return uuid4().hex


def bind_request_id(request_id: str | None = None) -> str:
    """Set the request ID for the current context, generating one if absent."""
    resolved = request_id or new_request_id()
    request_id_var.set(resolved)
    return resolved


def bind_user_id(user_id: str | None) -> None:
    user_id_var.set(user_id)


def get_request_id() -> str | None:
    return request_id_var.get()


class JsonFormatter(logging.Formatter):
    """Renders records as single-line JSON for CloudWatch metric filters."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if request_id := request_id_var.get():
            payload["request_id"] = request_id
        if user_id := user_id_var.get():
            payload["user_id"] = user_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything passed via logger.info(..., extra={...}).
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})

        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable output for local development."""

    COLORS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        time = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        rid = request_id_var.get()
        suffix = f" \033[90m[{rid[:8]}]{self.RESET}" if rid else ""
        base = f"{color}{record.levelname:<8}{self.RESET} {time} {record.name} — {record.getMessage()}{suffix}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the root handler. Call once, at application startup."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn duplicates access logs through its own handlers; route them here.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # SQLAlchemy's engine logger is noisy at INFO and says nothing useful.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
