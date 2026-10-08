"""Healthchecks.io ping entry point used by scrape.yml.

Subcommands:
  start   -> ping /start at the beginning of the run
  report  -> read artifacts/run_report.json and ping /fail (failures > 0) or
             /success with the run summary as body
  crash   -> ping /fail with "crash en paso <step>" (used by `if: failure()`)

Alerting is best-effort: missing HEALTHCHECKS_URL or ping errors never change
the process exit code.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.alerts import notify_crash, notify_report, notify_start  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Healthchecks ping helper")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start")
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--report", default="artifacts/run_report.json")
    crash_parser = sub.add_parser("crash")
    crash_parser.add_argument("--step", default="unknown")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "start":
        sent = notify_start()
    elif args.command == "report":
        sent = notify_report(args.report)
    else:
        sent = notify_crash(args.step)
    print(f"healthchecks {args.command}: sent={sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
