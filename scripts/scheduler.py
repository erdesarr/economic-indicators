"""Scheduler entry point used by scheduler.yml (every 5 minutes).

Reads the committed sheet snapshot, decides if any workflow window matches and
triggers it with `gh workflow run`. Test aids (workflow_dispatch inputs only):
  --force etl|updater  trigger that workflow regardless of the window
  --now ISO8601        override the current UTC time
  --dry-run            print decisions without triggering

Healthchecks: "scheduler" check (success on every run, /fail on crash) and the
"sync" check receives a /fail when an invalid cron is found in the snapshot.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.alerts.healthchecks import HealthchecksClient  # noqa: E402
from form_updater.config import load_form_updater_config  # noqa: E402
from form_updater.scheduler import (  # noqa: E402
    BOGOTA,
    forced_decision,
    load_settings,
    plan,
    trigger_workflow,
)


def _parse_now(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def main() -> int:
    parser = argparse.ArgumentParser(description="Dynamic workflow scheduler")
    parser.add_argument("--config", default="config/form_updater.yaml")
    parser.add_argument("--now", default=None, help="override current UTC time (ISO8601)")
    parser.add_argument("--force", choices=["etl", "updater"], default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_form_updater_config(args.config)
    scheduler_check = HealthchecksClient.from_env_for("scheduler")
    sync_check = HealthchecksClient.from_env_for("sync")
    if scheduler_check is not None:
        print(f"healthchecks scheduler start: sent={scheduler_check.ping_start()}")

    now = _parse_now(args.now)
    now_bogota = now.astimezone(BOGOTA)
    try:
        settings = load_settings(config.csv_path)
        if args.force:
            outcome_triggers = [
                forced_decision(args.force, config.workflow_etl, config.workflow_updater, now)
            ]
            warnings: list[str] = []
            critical: list[str] = []
        else:
            outcome = plan(settings, now, config.workflow_etl, config.workflow_updater)
            outcome_triggers = outcome.triggers
            warnings = outcome.warnings
            critical = outcome.critical

        print(f"scheduler now={now_bogota.isoformat()} settings={settings}")
        for warning in warnings:
            print(f"WARNING: {warning}")
        if not outcome_triggers:
            print("scheduler: no window match; nothing dispatched")
        body = (
            f"scheduler now={now_bogota.isoformat()} "
            f"triggers={[decision.workflow for decision in outcome_triggers]} "
            f"critical={len(critical)}"
        )
        if critical and sync_check is not None:
            print(
                "healthchecks sync fail: "
                f"sent={sync_check.ping_fail('invalid schedules: ' + '; '.join(critical))}"
            )

        ref = os.environ.get("GITHUB_REF_NAME", "main")
        dispatched: list[str] = []
        for decision in outcome_triggers:
            if args.dry_run:
                print(
                    f"DRY-RUN would trigger {decision.workflow} "
                    f"(key={decision.key}, occurrence={decision.occurrence.isoformat()})"
                )
                continue
            trigger_workflow(
                decision.workflow,
                ref=ref,
                inputs={"mode": "write"} if decision.key == "updater" else None,
            )
            dispatched.append(decision.workflow)
            print(
                f"triggered {decision.workflow} (key={decision.key}, "
                f"occurrence={decision.occurrence.isoformat()})"
            )
        summary = f"scheduler summary: dispatched={dispatched} warnings={len(warnings)}"
        print(summary)
        if scheduler_check is not None:
            print(f"healthchecks scheduler success: sent={scheduler_check.ping_success(body)}")
        return 0
    except Exception as exc:  # noqa: BLE001 - the check must report the crash
        logging.exception("scheduler crashed")
        if scheduler_check is not None:
            crash_body = f"scheduler crash: {exc}"
            print(f"healthchecks scheduler fail: sent={scheduler_check.ping_fail(crash_body)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
