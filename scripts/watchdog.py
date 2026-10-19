"""Form updater watchdog entry point (run from sync_config.yml hourly).

Reads the committed write state and pings the form-updater Healthchecks check
with an explicit /fail when the updater stopped writing or wrote 0 values.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.alerts.healthchecks import HealthchecksClient  # noqa: E402
from form_updater.watchdog import (  # noqa: E402
    DEFAULT_MAX_AGE_HOURS,
    DEFAULT_ZERO_WRITE_DAYS,
    STATE_PATH,
    evaluate_entries,
    load_entries,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Form updater watchdog")
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    parser.add_argument("--zero-write-days", type=int, default=DEFAULT_ZERO_WRITE_DAYS)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    entries = load_entries(args.state)
    problems = evaluate_entries(
        entries,
        datetime.now(UTC),
        max_age_hours=args.max_age_hours,
        zero_write_days=args.zero_write_days,
    )
    if not problems:
        print(f"watchdog: form updater healthy ({len(entries)} write records)")
        return 0
    body = "form updater watchdog:\n" + "\n".join(f"- {problem}" for problem in problems)
    print(body)
    client = HealthchecksClient.from_env_for("form-updater")
    if client is not None:
        print(f"healthchecks form-updater fail: sent={client.ping_fail(body)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
