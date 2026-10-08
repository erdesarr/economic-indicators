"""Entry point for the daily ETL run (``make scrape``)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.config import load_config  # noqa: E402
from etl.pipeline import format_report, run_etl  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily indicators ETL")
    parser.add_argument("--config", default="config/indicators.yaml")
    parser.add_argument("--db", default=None, help="override database path")
    parser.add_argument("--date", default=None, help="override run date (YYYY-MM-DD)")
    parser.add_argument("--no-email", action="store_true", help="skip alert emails")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config(args.config)
    run_date = None
    if args.date:
        from datetime import date

        run_date = date.fromisoformat(args.date)

    report = run_etl(
        config,
        run_date=run_date,
        db_path=args.db,
        run_url=os.environ.get("GITHUB_RUN_URL"),
        send_alerts=not args.no_email,
    )
    print(format_report(report))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
