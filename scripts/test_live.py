"""Live smoke test (``make test-live`` and post-scrape step in Actions).

Checks: 18/18 indicators present, value within plausible_range, unit coherence
and, for IBR, that both nominal (value) and effective (value_published) bars
are present. Out-of-range values mean a parse failure -> exit 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.config import load_config  # noqa: E402
from etl.validate import collect_live  # noqa: E402


def main() -> int:
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
    ok = total - len(errors)
    print(f"test-live: {ok}/{total} indicadores OK")
    for error in errors:
        print(f"  ERROR {error}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
