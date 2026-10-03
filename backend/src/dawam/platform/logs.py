"""Structured JSON logging.

Every log line is one JSON object with ``timestamp``, ``level``, ``logger``,
``message``, the ``request_id`` of the request being handled (if any), every
``extra={...}`` field, and ``exc_info`` when an exception is attached.

Log with the standard library and put data in ``extra``, never in the message::

    logger.info("snapshot created", extra={"snapshot_id": snapshot.id})

Never log secrets, source data values or email bodies.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from dawam.platform.request_context import current_request_id

# Attributes every LogRecord has; anything else on a record came from ``extra``.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "taskName", "request_id"}
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            line["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                line[key] = value
        if record.exc_info:
            line["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            line["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(line, default=str, ensure_ascii=False)


_factory_installed = False


def install_request_id_on_records() -> None:
    """Stamp the current request id on every log record, whichever handler formats it."""
    global _factory_installed
    if _factory_installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id()
        return record

    logging.setLogRecordFactory(factory)
    _factory_installed = True


def configure_logging(level: str = "INFO") -> None:
    """Send all logs (including uvicorn's) to stdout as JSON. Call once per process."""
    install_request_id_on_records()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
