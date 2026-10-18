"""Low-overhead, content-free observability for the meeting pipeline."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Iterator, Mapping


PROMPT_VERSION = "architecture-agent-v1"
CORRECTION_PROMPT_VERSION = "transcript-correction-v1"
SCHEMA_VERSION = "meeting-insight-v1"

_SECRET_MARKERS = ("api_key", "authorization", "bearer", "token", "secret", "password")
_CONTENT_MARKERS = ("prompt", "transcript", "audio", "raw_response", "response_body")


def safe_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return log-safe metadata and reject keys likely to contain content/secrets."""
    cleaned: dict[str, Any] = {}
    for key, value in fields.items():
        lowered = key.lower()
        if any(marker in lowered for marker in (*_SECRET_MARKERS, *_CONTENT_MARKERS)):
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        elif isinstance(value, (list, tuple)) and all(isinstance(item, (str, int, float, bool)) for item in value):
            cleaned[key] = list(value)
        else:
            cleaned[key] = type(value).__name__
    return cleaned


def event_log(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit a stable event and metadata without logging meeting content."""
    metadata = safe_fields(fields)
    suffix = " ".join(f"{key}={metadata[key]}" for key in sorted(metadata))
    logger.log(level, "%s%s", event, f" {suffix}" if suffix else "", extra={"event_name": event, **metadata})


@dataclass(slots=True)
class StageTimer:
    event: str
    started_ns: int = field(default_factory=time.perf_counter_ns)

    @property
    def duration_ms(self) -> int:
        return max(0, (time.perf_counter_ns() - self.started_ns) // 1_000_000)


@contextmanager
def timed_event(logger: logging.Logger, event: str, **fields: Any) -> Iterator[StageTimer]:
    timer = StageTimer(event)
    event_log(logger, logging.DEBUG, f"{event}.started", **fields)
    try:
        yield timer
    except BaseException as exc:
        event_log(
            logger,
            logging.ERROR,
            f"{event}.failed",
            duration_ms=timer.duration_ms,
            error_class=type(exc).__name__,
            **fields,
        )
        raise
    else:
        event_log(logger, logging.INFO, f"{event}.succeeded", duration_ms=timer.duration_ms, **fields)
