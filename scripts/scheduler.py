"""Scheduler entry point used by scheduler.yml.

Reads the committed sheet snapshot and triggers the workflows whose sheet cron
occurrence is due. Because GitHub throttles high-frequency crons, an occurrence
is due when it is at most ``catchup_hours`` old (config/form_updater.yaml) and
has not been triggered yet for that occurrence date (config/scheduler_state.json).

When both slots are due in the same delayed run, the ETL is triggered first and
the scheduler waits for that scrape run to complete BEFORE triggering the form
updater (never simultaneously: the updater must see freshly committed ETL data).

Test aids (workflow_dispatch inputs only):
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
    DEFAULT_REPO,
    ScheduleDecision,
    filter_pending,
    forced_decision,
    load_settings,
    load_state,
    plan,
    save_state,
    trigger_workflow,
    wait_for_workflow,
)


def _parse_now(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def _pending_updater(decisions: list[ScheduleDecision], index: int, state: dict[str, str]) -> bool:
    for later in decisions[index + 1 :]:
        if later.key != "updater":
            continue
        if state.get("updater") != later.occurrence.date().isoformat():
            return True
    return False


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
        state = load_state(config.state_path)
        if args.force:
            decisions = [
                forced_decision(args.force, config.workflow_etl, config.workflow_updater, now)
            ]
            warnings: list[str] = []
            critical: list[str] = []
        else:
            outcome = plan(
                settings,
                now,
                config.workflow_etl,
                config.workflow_updater,
                catchup_hours=config.catchup_hours,
            )
            decisions = outcome.triggers
            warnings = outcome.warnings
            critical = outcome.critical
            decisions, already_done = filter_pending(decisions, state)
            if already_done:
                print(f"scheduler: already triggered {already_done}")

        print(f"scheduler now={now_bogota.isoformat()} settings={settings}")
        print(f"scheduler state={state} catchup_hours={config.catchup_hours}")
        for warning in warnings:
            print(f"WARNING: {warning}")
        if critical and sync_check is not None:
            print(
                "healthchecks sync fail: "
                f"sent={sync_check.ping_fail('invalid schedules: ' + '; '.join(critical))}"
            )

        ref = os.environ.get("GITHUB_REF_NAME", "main")
        dispatched: list[str] = []
        skipped: list[str] = []
        for index, decision in enumerate(decisions):
            occurrence_date = decision.occurrence.date().isoformat()
            if not args.force and state.get(decision.key) == occurrence_date:
                print(f"skip {decision.key}: already triggered for occurrence {occurrence_date}")
                skipped.append(decision.key)
                continue
            if args.dry_run:
                print(
                    f"DRY-RUN would trigger {decision.workflow} "
                    f"(key={decision.key}, occurrence={decision.occurrence.isoformat()})"
                )
                continue
            inputs = {"mode": "write"} if decision.key == "updater" else None
            trigger_started = datetime.now(UTC)
            trigger_workflow(decision.workflow, ref=ref, inputs=inputs)
            if not args.force:
                state[decision.key] = occurrence_date
                save_state(config.state_path, state)
            dispatched.append(decision.workflow)
            print(
                f"triggered {decision.workflow} (key={decision.key}, "
                f"occurrence={decision.occurrence.isoformat()})"
            )
            if (
                decision.key == "etl"
                and decision.workflow == config.workflow_etl
                and _pending_updater(decisions, index, state)
            ):
                repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
                token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
                print("waiting for the scrape run to complete before the updater...")
                run = wait_for_workflow(
                    config.workflow_etl,
                    after_utc=trigger_started,
                    repo=repo,
                    token=token,
                )
                print(
                    f"scrape completed: status={run.get('status')} "
                    f"conclusion={run.get('conclusion')}"
                )

        if not decisions:
            print("scheduler: no due occurrence; nothing dispatched")
        summary = (
            f"scheduler summary: dispatched={dispatched} skipped={skipped} warnings={len(warnings)}"
        )
        print(summary)
        if scheduler_check is not None:
            body = summary
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
