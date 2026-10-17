"""Sync both Google Sheet tabs into config/sheet_config.csv (sync_config.yml).

Reads the public book through gviz (no auth), normalizes/validates every cell
and writes the committed snapshot consumed by the scheduler and the updater.
Never rewrites workflow YAML. On gviz failure the last valid snapshot stays.

Healthchecks check: "sync" (HEALTHCHECKS_URL_SYNC). Missing secret disables
alerting gracefully.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.alerts.healthchecks import HealthchecksClient  # noqa: E402
from etl.config import load_config  # noqa: E402
from form_updater.config import load_form_updater_config  # noqa: E402
from form_updater.sheet import (  # noqa: E402
    SheetError,
    fetch_tab,
    finalize_settings,
    parse_indicators,
    parse_settings,
    read_snapshot,
    snapshot_text,
    write_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync the public Google Sheet config")
    parser.add_argument("--config", default="config/form_updater.yaml")
    parser.add_argument("--indicators", default="config/indicators.yaml")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    form_config = load_form_updater_config(args.config)
    catalog = {indicator.slug for indicator in load_config(args.indicators).indicators}
    healthchecks = HealthchecksClient.from_env_for("sync")
    if healthchecks is not None:
        healthchecks.ping_start()

    def fail(reason: str) -> int:
        print(f"ERROR: {reason}")
        if healthchecks is not None:
            healthchecks.ping_fail(f"sync failed: {reason}")
        return 1

    try:
        indicators_text = fetch_tab(
            form_config.gviz_url(form_config.indicators_tab),
            timeout=form_config.gviz.timeout_seconds,
            retries=form_config.gviz.retries,
        )
        settings_text = fetch_tab(
            form_config.gviz_url(form_config.settings_tab),
            timeout=form_config.gviz.timeout_seconds,
            retries=form_config.gviz.retries,
        )
    except SheetError as exc:
        return fail(str(exc))

    try:
        parsed_indicators = parse_indicators(indicators_text, catalog)
        parsed_settings = parse_settings(settings_text)
    except SheetError as exc:
        return fail(str(exc))

    previous_settings = read_snapshot(form_config.csv_path)[1]
    warnings = list(parsed_indicators.warnings) + list(parsed_settings.warnings)
    warnings += [
        f"unexpected column {name!r} in {form_config.indicators_tab}; ignored"
        for name in parsed_indicators.unknown_columns
    ]
    finalized = finalize_settings(
        parsed_settings.settings, form_config.settings_defaults, previous_settings, warnings
    )

    changed = write_snapshot(
        form_config.csv_path,
        parsed_indicators.rows,
        finalized.settings,
        form_config.indicators_tab,
        form_config.settings_tab,
    )

    print(
        snapshot_text(
            parsed_indicators.rows,
            finalized.settings,
            form_config.indicators_tab,
            form_config.settings_tab,
        )
    )
    rows = parsed_indicators.rows
    summary = (
        f"sync summary: rows={len(rows)} active={len(parsed_indicators.active_rows)} "
        f"inactive={len(rows) - len(parsed_indicators.active_rows)} "
        f"settings={finalized.settings} changed={changed} "
        f"warnings={len(warnings)} critical={len(finalized.critical)}"
    )
    print(summary)
    for warning in warnings:
        print(f"WARNING: {warning}")

    body = summary + ("\n" + "\n".join(warnings) if warnings else "")
    if healthchecks is not None:
        if finalized.critical:
            healthchecks.ping_fail(body)
        else:
            healthchecks.ping_success(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
