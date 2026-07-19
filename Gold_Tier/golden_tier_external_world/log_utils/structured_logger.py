from __future__ import annotations

from datetime import datetime, timezone
from logging import Formatter, LogRecord, Logger, getLogger, INFO
from threading import local
from typing import Any, Optional
import json
import logging
import sys


_STRUCTURED_LOGGER_INITIALIZED = False
_thread_local = local()


def get_correlation_id() -> Optional[str]:
    return getattr(_thread_local, "correlation_id", None)


def set_correlation_id(cid: Optional[str]) -> None:
    _thread_local.correlation_id = cid


class JsonFormatter(Formatter):
    def __init__(self, fmt: Optional[str] = None, **fmt_kwargs: Any) -> None:
        super().__init__(fmt, **fmt_kwargs)

    def format(self, record: LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        cid = get_correlation_id()
        if cid:
            log_entry["correlation_id"] = cid

        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
            }

        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            log_entry.update(record.extra_fields)

        return json.dumps(log_entry, default=str, ensure_ascii=False)


class StructuredLogger(Logger):
    def _log(
        self,
        level: int,
        msg: object,
        args: Any,
        exc_info: Any = None,
        extra: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        if extra is None:
            extra = {}
        if "extra_fields" in kwargs:
            extra["extra_fields"] = kwargs.pop("extra_fields")
        super()._log(level, msg, args, exc_info=exc_info, extra=extra)

    def info(self, msg: object, *args: Any, **kwargs: Any) -> None:
        extra_fields = kwargs.pop("extra_fields", None)
        if extra_fields:
            kwargs["extra"] = {"extra_fields": extra_fields}
        super().info(msg, *args, **kwargs)

    def warning(self, msg: object, *args: Any, **kwargs: Any) -> None:
        extra_fields = kwargs.pop("extra_fields", None)
        if extra_fields:
            kwargs["extra"] = {"extra_fields": extra_fields}
        super().warning(msg, *args, **kwargs)

    def error(self, msg: object, *args: Any, **kwargs: Any) -> None:
        extra_fields = kwargs.pop("extra_fields", None)
        if extra_fields:
            kwargs["extra"] = {"extra_fields": extra_fields}
        super().error(msg, *args, **kwargs)


def setup_logging(
    level: int = INFO,
    json_format: bool = True,
    log_file: Optional[str] = None,
) -> None:
    global _STRUCTURED_LOGGER_INITIALIZED
    _STRUCTURED_LOGGER_INITIALIZED = True

    root = logging.getLogger()
    root.setLevel(level)

    for handler in root.handlers[:]:
        root.removeHandler(handler)

    if json_format:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def get_logger(name: str) -> Logger:
    logger = logging.getLogger(name)
    return logger
