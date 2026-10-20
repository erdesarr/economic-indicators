"""Silent-failure watchdog for the form updater.

The updater records every real write in ``config/form_update_state.json``.
This watchdog (run from the hourly sync workflow) raises an explicit
form-updater ``/fail`` when:
  * the last write is older than ``max_age_hours`` (26 h), or
  * the last ``zero_write_days`` runs wrote 0 values (e.g. every row was
    skipped), which would otherwise look "alive" while doing nothing.

The Healthchecks check period/grace remains the primary "no ping in N hours"
alarm; this adds an explicit fail ping with a human-readable reason.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_PATH = Path("config/form_update_state.json")
DEFAULT_MAX_AGE_HOURS = 26.0
DEFAULT_ZERO_WRITE_DAYS = 2
MAX_ENTRIES = 10


def load_entries(path: Path | str = STATE_PATH) -> list[dict[str, Any]]:
    state_path = Path(path)
    if not state_path.exists():
        return []
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _recorded_at(entry: dict[str, Any]) -> datetime:
    raw = str(entry.get("recorded_at", ""))
    moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def evaluate_entries(
    entries: list[dict[str, Any]],
    now_utc: datetime,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    zero_write_days: int = DEFAULT_ZERO_WRITE_DAYS,
) -> list[str]:
    """Return human-readable problems (empty = healthy).

    Only NATURAL writes count. A forced/test write must never reset the clock
    or mask a silent natural updater. Legacy entries without ``origin`` are
    treated as forced.
    """

    natural = [entry for entry in entries if entry.get("origin") == "natural"]
    if not natural:
        return ["form updater has no NATURAL write recorded (forced writes do not count)"]
    latest = max(natural, key=_recorded_at)
    age = now_utc - _recorded_at(latest)
    problems: list[str] = []
    if age > timedelta(hours=max_age_hours):
        problems.append(
            f"form updater has not written naturally for {age.total_seconds() / 3600:.1f} h "
            f"(max {max_age_hours:g} h; last run {latest.get('run_id')})"
        )
    recent = sorted(natural, key=_recorded_at)[-zero_write_days:]
    if len(recent) >= zero_write_days and all(
        int(entry.get("written", 0)) == 0 for entry in recent
    ):
        problems.append(f"form updater wrote 0 values in the last {zero_write_days} NATURAL runs")
    return problems


def record_write(
    path: Path | str,
    *,
    day: str,
    written: int,
    failed: int,
    omitted: int,
    run_id: str,
    origin: str = "forced",
    recorded_at: datetime | None = None,
) -> None:
    """Append/update today's write record (keeps the last 10)."""

    state_path = Path(path)
    entries = load_entries(state_path)
    entry = {
        "date": day,
        "written": written,
        "failed": failed,
        "omitted": omitted,
        "recorded_at": (recorded_at or datetime.now(UTC)).isoformat(),
        "run_id": run_id,
        "origin": origin,
    }
    entries = [
        item for item in entries if not (item.get("date") == day and item.get("origin") == origin)
    ] + [entry]
    entries = sorted(entries, key=_recorded_at)[-MAX_ENTRIES:]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
