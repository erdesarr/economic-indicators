"""Dynamic scheduler: trigger workflows from sheet-defined cron windows.

GitHub throttles high-frequency crons (observed runs every 2-5 hours), so a
strict 5-minute window misses the 21:00/21:30 America/Bogota slots. The
scheduler therefore catches up: an occurrence that is at most ``catchup_hours``
old is still due. A committed state file deduplicates triggers per occurrence
date, and when both slots are due in the same delayed run the ETL is triggered
first, the scheduler waits for that run to finish, and only then the form
updater is triggered (never simultaneously: the updater's freshness guard must
see the freshly committed ETL data).

An invalid cron produces a warning and a /fail ping on the SYNC check (per the
approved spec) and is ignored; the committed snapshot keeps the last valid one.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx
from croniter import croniter

from form_updater.sheet import read_snapshot

logger = logging.getLogger(__name__)

BOGOTA = ZoneInfo("America/Bogota")
POLL_INTERVAL = timedelta(minutes=5)
DEFAULT_CATCHUP_HOURS = 8.0
DEFAULT_STATE_PATH = Path("config/scheduler_state.json")
DEFAULT_REPO = "erdesarr/economic-indicators"


@dataclass(frozen=True)
class ScheduleDecision:
    key: str  # "etl" | "updater"
    workflow: str
    expression: str
    occurrence: datetime


@dataclass
class SchedulerOutcome:
    triggers: list[ScheduleDecision]
    warnings: list[str]
    critical: list[str]


def load_settings(csv_path: Path) -> dict[str, str]:
    """Settings from the committed snapshot (last valid values)."""

    return read_snapshot(csv_path)[1]


def load_state(path: Path) -> dict[str, str]:
    """Occurrence-date (America/Bogota) already triggered per key."""

    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def save_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(state.items())), indent=2) + "\n", encoding="utf-8")


def cron_window(
    expression: str, now_bogota: datetime, window: timedelta = POLL_INTERVAL
) -> datetime | None:
    """Strict window (kept for the 5-minute polling semantics and tests)."""

    if not croniter.is_valid(expression):
        raise ValueError("croniter rejected the expression")
    previous = cast("datetime | None", croniter(expression, now_bogota).get_prev(datetime))
    if previous is None:
        return None
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=now_bogota.tzinfo)
    if now_bogota - previous <= window:
        return previous
    return None


def due_occurrence(
    expression: str,
    now_bogota: datetime,
    catchup_hours: float = DEFAULT_CATCHUP_HOURS,
) -> datetime | None:
    """Most recent occurrence if it is within the catch-up horizon."""

    if not croniter.is_valid(expression):
        raise ValueError("croniter rejected the expression")
    previous = cast("datetime | None", croniter(expression, now_bogota).get_prev(datetime))
    if previous is None:
        return None
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=now_bogota.tzinfo)
    if now_bogota - previous <= timedelta(hours=catchup_hours):
        return previous
    return None


def plan(
    settings: dict[str, str],
    now_utc: datetime,
    workflow_etl: str,
    workflow_updater: str,
    catchup_hours: float = DEFAULT_CATCHUP_HOURS,
) -> SchedulerOutcome:
    """Decide which workflows (if any) are due (order: etl, updater)."""

    now_bogota = now_utc.astimezone(BOGOTA)
    triggers: list[ScheduleDecision] = []
    warnings: list[str] = []
    critical: list[str] = []
    pairs = (
        ("etl", "horario_etl", workflow_etl),
        ("updater", "horario_updater", workflow_updater),
    )
    for key, setting_name, workflow in pairs:
        expression = (settings.get(setting_name) or "").strip()
        if not expression:
            warnings.append(f"setting {setting_name!r} missing in snapshot; skipped")
            continue
        try:
            occurrence = due_occurrence(expression, now_bogota, catchup_hours)
        except ValueError as exc:
            message = f"setting {setting_name!r} invalid ({exc}); ignored"
            warnings.append(message)
            critical.append(message)
            continue
        if occurrence is not None:
            triggers.append(
                ScheduleDecision(
                    key=key,
                    workflow=workflow,
                    expression=expression,
                    occurrence=occurrence,
                )
            )
    return SchedulerOutcome(triggers=triggers, warnings=warnings, critical=critical)


def forced_decision(
    key: str, workflow_etl: str, workflow_updater: str, now_utc: datetime
) -> ScheduleDecision:
    """Test aid: build a decision without reading the schedule windows."""

    if key not in {"etl", "updater"}:
        raise ValueError(f"unknown force key: {key!r}")
    workflow = workflow_etl if key == "etl" else workflow_updater
    return ScheduleDecision(
        key=key,
        workflow=workflow,
        expression="(forced)",
        occurrence=now_utc.astimezone(BOGOTA),
    )


def filter_pending(
    decisions: list[ScheduleDecision], state: dict[str, str]
) -> tuple[list[ScheduleDecision], list[str]]:
    """Drop decisions already triggered for their occurrence date."""

    pending: list[ScheduleDecision] = []
    skipped: list[str] = []
    for decision in decisions:
        occurrence_date = decision.occurrence.date().isoformat()
        if state.get(decision.key) == occurrence_date:
            skipped.append(f"{decision.key}@{occurrence_date}")
        else:
            pending.append(decision)
    return pending, skipped


def trigger_workflow(
    workflow: str,
    ref: str = "main",
    timeout: float = 60.0,
    inputs: dict[str, str] | None = None,
) -> None:
    """Dispatch a workflow with the gh CLI (GH_TOKEN required)."""

    env = os.environ.copy()
    token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
    if token:
        env["GH_TOKEN"] = token
    command = ["gh", "workflow", "run", workflow, "--ref", ref]
    for key, value in (inputs or {}).items():
        command += ["-f", f"{key}={value}"]
    subprocess.run(
        command,
        check=True,
        env=env,
        timeout=timeout,
        capture_output=True,
        text=True,
    )


def wait_for_workflow(
    workflow: str,
    after_utc: datetime,
    repo: str,
    token: str,
    timeout_seconds: float = 1800.0,
    poll_seconds: float = 15.0,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Wait for the workflow run dispatched after ``after_utc`` to complete."""

    owned = client is None
    http = client or httpx.Client()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "economic-indicators-scheduler",
    }
    deadline = time.monotonic() + timeout_seconds
    run: dict[str, Any] | None = None
    try:
        while time.monotonic() < deadline:
            response = http.get(
                f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs",
                params={"per_page": 5},
                headers=headers,
                timeout=20.0,
            )
            response.raise_for_status()
            runs = response.json().get("workflow_runs", [])
            candidates = [
                item
                for item in runs
                if datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00"))
                >= after_utc - timedelta(seconds=30)
            ]
            if candidates:
                run = max(
                    candidates,
                    key=lambda item: str(item["created_at"]),
                )
                if run.get("status") == "completed":
                    return run
            time.sleep(poll_seconds)
    finally:
        if owned:
            http.close()
    raise TimeoutError(
        f"workflow {workflow} did not complete within {timeout_seconds}s "
        f"(last status={run.get('status') if run else 'no run found'})"
    )
