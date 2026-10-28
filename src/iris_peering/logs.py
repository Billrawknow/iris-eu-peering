"""Structured logging; every record carries the upstream ``source``."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_EXTRA_KEYS = ("source", "entity", "source_id", "run_id", "reason", "url", "status", "count")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in _EXTRA_KEYS:
            v = getattr(record, k, None)
            if v is not None:
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(
            f"{k}={getattr(record, k)}" for k in _EXTRA_KEYS if getattr(record, k, None) is not None
        )
        base = f"{record.levelname:<7} [{getattr(record, 'source', record.name)}] {record.getMessage()}"
        return f"{base}  {extras}" if extras else base


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root = logging.getLogger("iris_peering")
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    root.propagate = False


def get_logger(source: str) -> logging.LoggerAdapter:
    class _Adapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            extra = dict(self.extra)
            extra.update(kwargs.get("extra") or {})
            kwargs["extra"] = extra
            return msg, kwargs

    return _Adapter(logging.getLogger(f"iris_peering.{source}"), {"source": source})
