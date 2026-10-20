"""Updater orchestration helpers: load data, resolve rows, build plans.

The ETL contract (``docs/api/v1`` or a freshly generated ``artifacts/contract``)
is the data source: ``latest.json`` for the current reading and
``indicators/<slug>/history.json`` for the previous reading used by
forward_fill. Reading the contract (not the sheet) is what keeps fallbacks
independent from which rows are active for writing.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from form_updater.formatter import format_value
from form_updater.resolution import (
    Resolution,
    ResolutionFailure,
    VariableSnapshot,
    resolve_row,
)
from form_updater.sheet import IndicatorRow, read_snapshot


class StaleDataError(RuntimeError):
    """Raised when no fresh contract source is available (never write old data)."""


DEFAULT_REPO = "erdesarr/economic-indicators"
DEFAULT_PAGES_LATEST_URL = "https://erdesarr.github.io/economic-indicators/api/v1/latest.json"
PAGES_MAX_AGE_HOURS = 6.0


def default_raw_base() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
    return os.environ.get(
        "CONTRACT_RAW_BASE",
        f"https://raw.githubusercontent.com/{repo}/main/docs/api/v1",
    )


def pages_latest_url() -> str:
    return os.environ.get("PAGES_LATEST_URL", DEFAULT_PAGES_LATEST_URL)


def pages_fresh(
    latest: dict[str, Any],
    today_bogota: date,
    max_age_hours: float = PAGES_MAX_AGE_HOURS,
    now_utc: datetime | None = None,
) -> bool:
    """True when the deployed contract has today's readings and is recent.

    ``generated_at`` is UTC; at 21:00 Bogota it is already the next UTC day, so
    the check uses a max-age window plus the Bogota reading date instead of a
    naive date equality.
    """

    dates = [
        str(payload["date"])
        for payload in latest.get("indicators", {}).values()
        if isinstance(payload, dict) and payload.get("date")
    ]
    if not dates or max(dates) != today_bogota.isoformat():
        return False
    raw_generated = latest.get("generated_at")
    if not raw_generated:
        return False
    generated = datetime.fromisoformat(str(raw_generated).replace("Z", "+00:00"))
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
    reference = now_utc or datetime.now(UTC)
    return reference - generated <= timedelta(hours=max_age_hours)


def _fetch_pages_latest() -> dict[str, Any]:
    with httpx.Client() as http:
        response = http.get(
            pages_latest_url(),
            params={"cb": str(int(time.time()))},
            timeout=15.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise StaleDataError("Pages latest.json root is not an object")
    return payload


def wait_for_fresh_pages(
    today_bogota: date,
    timeout_seconds: float = 300.0,
    interval_seconds: float = 30.0,
    fetcher: Callable[[], dict[str, Any]] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Poll the deployed contract until it is fresh (or fail).

    The scheduler may trigger the updater on time (15-30 min after the ETL);
    Pages can still be deploying. Poll every ~30 s up to ~5 min before writing;
    if it is still stale, raise (the caller pings /fail).
    """

    load = fetcher or _fetch_pages_latest
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while True:
        try:
            latest = load()
            if pages_fresh(latest, today_bogota, now_utc=now_utc):
                return latest
            last_error = StaleDataError(
                "Pages latest.json is stale (no reading for today or generated_at too old)"
            )
        except Exception as exc:  # noqa: BLE001 - retry until the deadline
            last_error = exc
        if time.monotonic() >= deadline:
            raise StaleDataError(
                f"Pages latest.json did not become fresh within {timeout_seconds:g}s: {last_error}"
            ) from last_error
        time.sleep(interval_seconds)


@dataclass(frozen=True)
class DataSource:
    data: dict[str, VariableSnapshot]
    origin: str  # "local" | "raw.githubusercontent.com"


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


def _snapshots_from_latest(
    latest: dict[str, Any],
    history_loader: Any,
) -> dict[str, VariableSnapshot]:
    data: dict[str, VariableSnapshot] = {}
    for slug, payload in latest.get("indicators", {}).items():
        if not isinstance(payload, dict) or payload.get("value") is None:
            continue
        previous_value: float | None = None
        previous_variation: float | None = None
        readings = history_loader(slug)
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


def load_variable_data(contract_dir: Path) -> dict[str, VariableSnapshot]:
    latest = _read_json(contract_dir / "v1" / "latest.json")
    if latest is None:
        raise FileNotFoundError(f"latest.json not found under {contract_dir}")

    def history_loader(slug: str) -> list[dict[str, Any]]:
        history = _read_json(contract_dir / "v1" / "indicators" / slug / "history.json")
        return list(history.get("readings", [])) if history else []

    return _snapshots_from_latest(latest, history_loader)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    parsed = json.loads(path.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else None


def _newest_date(latest: dict[str, Any]) -> str | None:
    dates = [
        str(payload["date"])
        for payload in latest.get("indicators", {}).values()
        if isinstance(payload, dict) and payload.get("date")
    ]
    return max(dates) if dates else None


def freshness_error(contract_dir: Path, today_bogota: date) -> str | None:
    """latest.json must contain today's readings (America/Bogota)."""

    latest = _read_json(contract_dir / "v1" / "latest.json")
    if latest is None:
        return f"latest.json not found under {contract_dir}"
    newest = _newest_date(latest)
    if newest is None:
        return "latest.json has no readings"
    if newest != today_bogota.isoformat():
        return (
            f"latest.json is stale: newest reading {newest}, "
            f"expected {today_bogota.isoformat()} (America/Bogota)"
        )
    return None


def load_variable_data_from_url(
    base_url: str,
    today_bogota: date,
    timeout: float = 10.0,
    client: httpx.Client | None = None,
) -> dict[str, VariableSnapshot]:
    """Fetch latest+history from a static contract URL (raw.githubusercontent).

    Raises StaleDataError when the remote latest.json is not from today; stale
    data must never be written to the form.
    """

    owned = client is None
    http = client or httpx.Client()
    base = base_url.rstrip("/")
    try:
        latest_response = http.get(f"{base}/latest.json", timeout=timeout)
        latest_response.raise_for_status()
        latest = latest_response.json()
        newest = _newest_date(latest)
        if newest is None or newest != today_bogota.isoformat():
            raise StaleDataError(
                f"remote latest.json is stale (newest={newest}, "
                f"expected={today_bogota.isoformat()}); refusing to write old data"
            )

        def history_loader(slug: str) -> list[dict[str, Any]]:
            response = http.get(f"{base}/indicators/{slug}/history.json", timeout=timeout)
            response.raise_for_status()
            return list(response.json().get("readings", []))

        return _snapshots_from_latest(latest, history_loader)
    finally:
        if owned:
            http.close()


def resolve_variable_data(
    contract_dir: Path,
    today_bogota: date,
    raw_base_url: str | None = None,
    client: httpx.Client | None = None,
) -> DataSource:
    """Use the fresh local contract, else raw.githubusercontent, else fail."""

    local_error = freshness_error(contract_dir, today_bogota)
    if local_error is None:
        return DataSource(load_variable_data(contract_dir), "local")
    remote_base = raw_base_url or default_raw_base()
    try:
        remote = load_variable_data_from_url(remote_base, today_bogota, client=client)
    except Exception as exc:  # noqa: BLE001 - any failure means no fresh data
        raise StaleDataError(
            f"{local_error}; raw.githubusercontent fallback failed: {exc}. "
            "Refusing to write stale data."
        ) from exc
    return DataSource(remote, "raw.githubusercontent.com")


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
