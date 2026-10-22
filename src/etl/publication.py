"""Publication windows: expected versus unexpected forward-fill alerts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

DEFAULT_PUBLICATION_WINDOW = "00:00-23:59"

FAILURE_STATUSES = {"forward_filled", "failed"}


@dataclass(frozen=True)
class PublicationWindow:
    start: time
    end: time


def parse_publication_window(value: str) -> PublicationWindow:
    """Parse a ``HH:MM-HH:MM`` Bogota-local publication window."""

    text = value.strip()
    parts = text.split("-")
    if len(parts) != 2:
        raise ValueError(f"invalid publication_window {value!r}; expected HH:MM-HH:MM")
    try:
        start = time.fromisoformat(parts[0].strip())
        end = time.fromisoformat(parts[1].strip())
    except ValueError as exc:
        raise ValueError(f"invalid publication_window {value!r}; expected HH:MM-HH:MM") from exc
    return PublicationWindow(start=start, end=end)


def _minutes(moment: time) -> int:
    return moment.hour * 60 + moment.minute


def in_publication_window(moment: datetime, window: str) -> bool:
    """Whether ``moment`` falls inside the window (boundaries inclusive).

    ``moment`` must already be expressed in the indicator's local timezone
    (Bogota). Windows crossing midnight (``22:00-02:00``) are supported.
    """

    parsed = parse_publication_window(window)
    current = _minutes(moment.time())
    start = _minutes(parsed.start)
    end = _minutes(parsed.end)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def is_expected_forward_fill(status: str, publication_window: str, run_at: datetime) -> bool:
    """True when a forward-fill/failure is expected because the source is closed.

    Outside its publication window an indicator is not supposed to be on the
    page: forward-filling it is normal and must ping /success with a warning.
    Inside its window the same forward-fill means the source is down and must
    ping /fail.
    """

    if status not in FAILURE_STATUSES:
        return False
    return not in_publication_window(run_at, publication_window)
