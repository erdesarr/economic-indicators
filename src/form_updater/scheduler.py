"""Dynamic scheduler: trigger workflows from sheet-defined cron windows.

The scheduler workflow runs every 5 minutes (fixed cron). Each run reads
``config/sheet_config.csv`` (the committed snapshot of the book) and checks
whether "now" in America/Bogota falls inside the window of ``horario_etl`` or
``horario_updater``. Matching workflows are dispatched with ``gh workflow run``.

An invalid cron produces a warning and a /fail ping on the SYNC check (per the
approved spec) and is ignored; the committed snapshot keeps the last valid one.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from croniter import croniter

from form_updater.sheet import read_snapshot

logger = logging.getLogger(__name__)

BOGOTA = ZoneInfo("America/Bogota")
POLL_INTERVAL = timedelta(minutes=5)


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


def cron_window(
    expression: str, now_bogota: datetime, window: timedelta = POLL_INTERVAL
) -> datetime | None:
    """Return the occurrence when ``now`` falls in its window, else None.

    The scheduler polls every 5 minutes; a schedule matches when its most
    recent occurrence is at most ``window`` old (tolerates queue delays).
    """

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


def plan(
    settings: dict[str, str],
    now_utc: datetime,
    workflow_etl: str,
    workflow_updater: str,
) -> SchedulerOutcome:
    """Decide which workflows (if any) must be triggered right now."""

    now_bogota = now_utc.astimezone(BOGOTA)
    triggers: list[ScheduleDecision] = []
    warnings: list[str] = []
    critical: list[str] = []
    pairs = (("etl", "horario_etl", workflow_etl), ("updater", "horario_updater", workflow_updater))
    for key, setting_name, workflow in pairs:
        expression = (settings.get(setting_name) or "").strip()
        if not expression:
            warnings.append(f"setting {setting_name!r} missing in snapshot; skipped")
            continue
        try:
            occurrence = cron_window(expression, now_bogota)
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
