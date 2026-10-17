"""Form updater entry point (dry-run / write / capture-records).

Modes:
  dry-run          build the plan and print a repr table with values/sources.
                   Writes nothing; no browser needed.
  write            write the planned values to the REAL form (Playwright),
                   with the Nombre anti-wrong-record check and post-write
                   verification. Healthchecks pings enabled.
  capture          read Nombre for the active record ids and save the golden
                   ``records.json`` (owner approves before any write).

The ETL contract is rebuilt from ``data/indicadores.db`` for freshness; a stale
DB (newest reading != today America/Bogota) aborts the run.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from etl.alerts.healthchecks import HealthchecksClient  # noqa: E402
from form_updater.updater import (  # noqa: E402
    build_plan,
    format_plan_lines,
    freshness_error,
    load_active_rows,
    load_variable_data,
)
from form_updater.webform import (  # noqa: E402
    RECORDS_PATH,
    WebFormClient,
    WriteOutcome,
    load_records,
    save_records,
)
from scripts.build_pages import build as build_contract  # noqa: E402

BOGOTA = ZoneInfo("America/Bogota")


def _parse_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def _build_fresh_contract(contract_dir: Path) -> None:
    build_contract(
        Path("data/indicadores.db"),
        Path("config/indicators.yaml"),
        contract_dir,
    )


def _fail(message: str) -> int:
    print(f"ERROR: {message}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the web form from the ETL")
    parser.add_argument("--mode", choices=["dry-run", "write", "capture"], default="dry-run")
    parser.add_argument("--slugs", default=None, help="comma-separated record ids or slugs")
    parser.add_argument("--contract", default="artifacts/contract")
    parser.add_argument("--records", default=str(RECORDS_PATH))
    parser.add_argument("--csv", default="config/sheet_config.csv")
    parser.add_argument("--artifacts", default="artifacts")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    contract_dir = Path(args.contract)
    _build_fresh_contract(contract_dir)

    rows = load_active_rows(Path(args.csv))
    selected_ids = set(_parse_ids(args.slugs))
    selected_slugs = {value for value in (args.slugs or "").split(",") if value}
    if selected_ids or selected_slugs:
        rows_for_selection = [
            row
            for row in rows
            if row.id_indicador in selected_ids or row.indicador in selected_slugs
        ]
    else:
        rows_for_selection = rows

    form_url = os.environ.get("FORM_URL", "").strip()

    if args.mode == "capture":
        if not form_url:
            return _fail("FORM_URL is not configured")
        ids = [row.id_indicador for row in rows_for_selection]
        with WebFormClient(form_url, {}, artifacts_dir=Path(args.artifacts)) as client:
            records = client.capture_names(ids)
        path = save_records(args.records, records)
        print(f"captured {len(records)} records into {path}")
        for key, value in sorted(records.items(), key=lambda item: int(item[0])):
            print(f"  {key} -> {value!r}")
        return 0

    data = load_variable_data(contract_dir)
    today = datetime.now(BOGOTA).date()
    stale = freshness_error(contract_dir, today)
    if stale is not None:
        return _fail(stale)

    plan = build_plan(rows_for_selection, data, slugs=None)
    for line in format_plan_lines(plan):
        print(line)

    if args.mode == "dry-run":
        print(
            f"dry-run summary: planned={len(plan.planned)} "
            f"omitted={len(plan.failures)} (nothing written)"
        )
        return 0 if plan.ok else 1

    if not form_url:
        return _fail("FORM_URL is not configured")
    healthchecks = HealthchecksClient.from_env_for("form-updater")
    if healthchecks is not None:
        print(f"healthchecks form-updater start: sent={healthchecks.ping_start()}")

    try:
        records = load_records(args.records)
    except Exception as exc:  # noqa: BLE001 - keep the run controlled
        if healthchecks is not None:
            print(f"healthchecks form-updater fail: sent={healthchecks.ping_fail(str(exc))}")
        return _fail(str(exc))

    outcomes = []
    with WebFormClient(form_url, records, artifacts_dir=Path(args.artifacts)) as client:
        for item in plan.planned:
            try:
                outcome = client.write_record(item.row.id_indicador, item.formatted)
            except Exception as exc:  # noqa: BLE001 - one row must not kill the run
                outcome = WriteOutcome(
                    id_indicador=item.row.id_indicador,
                    nombre="",
                    previous_value="",
                    new_value="",
                    expected_value=item.formatted,
                    ok=False,
                    error=f"write crashed: {exc}",
                )
            outcomes.append(outcome)
            print(
                f"record {outcome.id_indicador} ({outcome.nombre}): "
                f"{outcome.previous_value!r} -> {outcome.new_value!r} "
                f"(expected {outcome.expected_value!r}) ok={outcome.ok} "
                f"{outcome.error or ''}"
            )

    write_failures = [outcome for outcome in outcomes if not outcome.ok]
    written_ok = [outcome for outcome in outcomes if outcome.ok]
    summary = (
        f"form-updater summary: written={len(written_ok)} "
        f"failed={len(write_failures)} omitted={len(plan.failures)}"
    )
    print(summary)
    if healthchecks is not None:
        if write_failures or plan.failures:
            body_lines = [summary]
            body_lines += [
                f"- record {outcome.id_indicador}: {outcome.error}" for outcome in write_failures
            ]
            body_lines += [
                f"- row {row.id_indicador} ({row.indicador}): {'; '.join(reasons)}"
                for row, reasons in plan.failures
            ]
            print(
                "healthchecks form-updater fail: "
                f"sent={healthchecks.ping_fail(chr(10).join(body_lines))}"
            )
        else:
            print(f"healthchecks form-updater success: sent={healthchecks.ping_success(summary)}")
    return 0 if not write_failures and not plan.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
