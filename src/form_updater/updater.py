"""Updater orchestration helpers: load data, resolve rows, build plans.

The ETL contract (``docs/api/v1`` or a freshly generated ``artifacts/contract``)
is the data source: ``latest.json`` for the current reading and
``indicators/<slug>/history.json`` for the previous reading used by
forward_fill. Reading the contract (not the sheet) is what keeps fallbacks
independent from which rows are active for writing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from form_updater.formatter import format_value
from form_updater.resolution import (
    Resolution,
    ResolutionFailure,
    VariableSnapshot,
    resolve_row,
)
from form_updater.sheet import IndicatorRow, read_snapshot


@dataclass(frozen=True)
class PlannedRow:
    row: IndicatorRow
    resolution: Resolution
    formatted: str


@dataclass
class Plan:
    planned: list[PlannedRow]
    failures: list[tuple[IndicatorRow, tuple[str, ...]]]

    @property
    def ok(self) -> bool:
        return not self.failures


def load_active_rows(csv_path: Path) -> list[IndicatorRow]:
    """Active rows only: inactive rows pause writing but stay in the snapshot."""

    rows, _ = read_snapshot(csv_path)
    return [row for row in rows if row.activo]


def load_variable_data(contract_dir: Path) -> dict[str, VariableSnapshot]:
    latest_path = contract_dir / "v1" / "latest.json"
    if not latest_path.exists():
        raise FileNotFoundError(f"latest.json not found under {contract_dir}")
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    indicators: dict[str, Any] = latest.get("indicators", {})
    data: dict[str, VariableSnapshot] = {}
    for slug, payload in indicators.items():
        if not isinstance(payload, dict) or payload.get("value") is None:
            continue
        previous_value: float | None = None
        previous_variation: float | None = None
        history_path = contract_dir / "v1" / "indicators" / slug / "history.json"
        if history_path.exists():
            readings = json.loads(history_path.read_text(encoding="utf-8")).get("readings", [])
            if len(readings) >= 2:
                previous = readings[-2]
                previous_value = previous.get("value")
                previous_variation = previous.get("variation_pct")
        data[slug] = VariableSnapshot(
            value=float(payload["value"]),
            variation_pct=payload.get("variation_pct"),
            status=str(payload.get("status", "ok")),
            previous_value=previous_value,
            previous_variation_pct=previous_variation,
        )
    return data


def freshness_error(contract_dir: Path, today_bogota: date) -> str | None:
    """latest.json must contain today's readings (America/Bogota)."""

    latest_path = contract_dir / "v1" / "latest.json"
    if not latest_path.exists():
        return f"latest.json not found under {contract_dir}"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    dates = [
        str(payload["date"])
        for payload in latest.get("indicators", {}).values()
        if isinstance(payload, dict) and payload.get("date")
    ]
    if not dates:
        return "latest.json has no readings"
    newest = max(dates)
    if newest != today_bogota.isoformat():
        return (
            f"latest.json is stale: newest reading {newest}, "
            f"expected {today_bogota.isoformat()} (America/Bogota)"
        )
    return None


def build_plan(
    rows: list[IndicatorRow],
    data: dict[str, VariableSnapshot],
    slugs: set[str] | None = None,
) -> Plan:
    planned: list[PlannedRow] = []
    failures: list[tuple[IndicatorRow, tuple[str, ...]]] = []
    for row in rows:
        if slugs is not None and row.indicador not in slugs and str(row.id_indicador) not in slugs:
            continue
        threshold = row.umbral_variacion_pct
        if threshold is None:
            failures.append((row, ("missing resolved umbral_variacion_pct (run sync)",)))
            continue
        result = resolve_row(row, data, threshold)
        if isinstance(result, ResolutionFailure):
            failures.append((row, result.reasons))
            continue
        planned.append(
            PlannedRow(
                row=row,
                resolution=result,
                formatted=format_value(row, result.value, result.variation_pct),
            )
        )
    return Plan(planned=planned, failures=failures)


def format_plan_lines(plan: Plan) -> list[str]:
    """Dry-run table with repr() so spaces and symbols are visible."""

    lines = ["id | indicador | source | value | variation | formatted (repr) | reason"]
    for item in plan.planned:
        resolution = item.resolution
        extras: list[str] = []
        if resolution.discarded:
            extras.append("discarded: " + "; ".join(resolution.discarded))
        if resolution.reason:
            extras.append(resolution.reason)
        lines.append(
            f"{item.row.id_indicador} | {item.row.indicador} | {resolution.source} | "
            f"{resolution.value!r} | {resolution.variation_pct!r} | "
            f"{item.formatted!r} | {' | '.join(extras)}"
        )
    for row, reasons in plan.failures:
        lines.append(
            f"{row.id_indicador} | {row.indicador} | OMITTED | - | - | - | {'; '.join(reasons)}"
        )
    return lines
