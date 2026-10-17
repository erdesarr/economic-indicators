"""ETL output smoke test.

Default (used by scrape.yml): validate ``artifacts/run_report.json`` generated
by the same run. It does NOT re-scrape, avoiding false negatives when GitHub
delays the cron. ``--live`` keeps the old live re-scrape for manual use
(``make test-live``).

Exit 0 when all expected indicators are present with ok/forward_filled/manual
status (forward-fill is valid data; the ETL already alerted via Healthchecks).
Missing report/indicators or a failed indicator -> exit 1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.config import load_config  # noqa: E402
from etl.smoke import SmokeError, load_run_report, validate_run_report  # noqa: E402


def run_report_check(report_path: str) -> int:
    config = load_config("config/indicators.yaml")
    expected = {indicator.slug for indicator in config.indicators}
    try:
        report = load_run_report(report_path)
    except SmokeError as exc:
        print(f"smoke: ERROR {exc}")
        return 1
    errors = validate_run_report(report, expected)
    statuses = {result.get("status") for result in report.get("results", [])}
    print(
        f"smoke: {len(expected) - len(errors)}/{len(expected)} indicators present "
        f"in {report_path} (statuses={sorted(str(s) for s in statuses)})"
    )
    for error in errors:
        print(f"  ERROR {error}")
    return 0 if not errors else 1


def run_live_check() -> int:
    from etl.validate import collect_live

    config = load_config("config/indicators.yaml")
    results, _ = collect_live(config)
    errors: list[str] = []
    for indicator in config.indicators:
        result = results.get(indicator.slug)
        if result is None or not result.ok:
            detail = result.error if result else "no result"
            errors.append(f"{indicator.slug}: missing/error ({detail})")
            continue
        value = result.value or 0.0
        if not indicator.in_range(value):
            low, high = indicator.plausible_range
            errors.append(f"{indicator.slug}: value {value} outside [{low}, {high}]")
        if indicator.source == "banrep" and result.value_published is None:
            errors.append(f"{indicator.slug}: missing effective value_published")
        if (
            indicator.unit in {"USD/lb", "USD/bbl", "BRL por USD", "EUR/MWh", "USD/MMBtu"}
            and not result.unit_as_published
        ):
            errors.append(f"{indicator.slug}: missing unit_as_published")
    total = len(config.indicators)
    print(f"test-live: {total - len(errors)}/{total} indicadores OK")
    for error in errors:
        print(f"  ERROR {error}")
    return 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL output smoke test")
    parser.add_argument("--report", default="artifacts/run_report.json")
    parser.add_argument(
        "--live",
        action="store_true",
        help="re-scrape all sources live (manual use; not used in CI)",
    )
    args = parser.parse_args()
    if args.live:
        return run_live_check()
    return run_report_check(args.report)


if __name__ == "__main__":
    raise SystemExit(main())
