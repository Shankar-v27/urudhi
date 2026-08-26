"""Lightweight observability: a structured logger, counters, secret redaction.

Deliberately small. One process-wide :class:`Counters` registry that the
loop, webhook receiver and brain increment; ``/health`` snapshots it. Logs are
``key=value`` lines so they grep cleanly, and every value passes through
:func:`redact` so an API key can never reach a log line by accident.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from typing import Any

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{6,}|rzp_(?:test|live)_[A-Za-z0-9]{6,}|whsec_[A-Za-z0-9_-]{4,})")


def redact(value: Any) -> str:
    """Mask anything that looks like an API key, Razorpay key or webhook secret."""
    return _SECRET_RE.sub(lambda m: m.group(0)[:7] + "…", str(value))


class Counters:
    """Thread-safe named counters. ``snapshot()`` is what /health returns."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, int] = {}

    def inc(self, name: str, by: int = 1) -> None:
        with self._lock:
            self._values[name] = self._values.get(name, 0) + by

    def get(self, name: str) -> int:
        with self._lock:
            return self._values.get(name, 0)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(sorted(self._values.items()))

    def reset(self) -> None:
        with self._lock:
            self._values.clear()


counters = Counters()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class _KVLogger(logging.LoggerAdapter):
    """``log.info("event", key=value, ...)`` → ``event key=value ...`` with redaction."""

    def log(self, level: int, msg: object, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        fields = {k: v for k, v in kwargs.items() if k not in ("exc_info", "stack_info", "extra")}
        for k in fields:
            kwargs.pop(k)
        if fields:
            msg = f"{msg} " + " ".join(f"{k}={redact(v)}" for k, v in fields.items())
        super().log(level, redact(msg), *args, **kwargs)


def get_logger(name: str) -> _KVLogger:
    return _KVLogger(logging.getLogger(name), {})


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]
