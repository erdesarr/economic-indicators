"""Entry point for the daily ETL run (``make scrape``).

Writes ``artifacts/run_report.json`` so the Healthchecks notification step can
compose the /success or /fail ping body.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.config import load_config  # noqa: E402
from etl.pipeline import format_report, report_to_dict, run_etl  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily indicators ETL")
    parser.add_argument("--config", default="config/indicators.yaml")
    parser.add_argument("--db", default=None, help="override database path")
    parser.add_argument("--date", default=None, help="override run date (YYYY-MM-DD)")
    parser.add_argument(
        "--report", default="artifacts/run_report.json", help="run report JSON path"
    )
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

    report = run_etl(config, run_date=run_date, db_path=args.db)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report_to_dict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(format_report(report))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
