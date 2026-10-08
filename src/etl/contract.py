"""Shared read-model for the API and the static Pages contract.

Every payload carries the full contract fields: value, value_published, unit,
unit_as_published, status, base_gap, source_date and variation_pct.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from etl.storage import latest_reading, previous_reading
from etl.variation import compute_variation


def _reading_payload(
    conn: sqlite3.Connection,
    indicator: sqlite3.Row,
    row: sqlite3.Row,
    max_gap_days: int,
) -> dict[str, Any]:
    reading_date = date.fromisoformat(str(row["date"]))
    previous = previous_reading(conn, int(indicator["id"]), reading_date, max_gap_days)
    variation = compute_variation(
        float(row["value"]),
        reading_date,
        float(previous["value"]) if previous else None,
        date.fromisoformat(str(previous["date"])) if previous else None,
        max_gap_days,
    )
    return {
        "slug": str(indicator["slug"]),
        "date": reading_date.isoformat(),
        "value": float(row["value"]),
        "value_published": (
            float(row["value_published"]) if row["value_published"] is not None else None
        ),
        "unit": str(indicator["unit"]),
        "unit_as_published": str(row["unit_as_published"]),
        "status": str(row["status"]),
        "base_gap": bool(row["base_gap"]),
        "source_date": str(row["source_date"]) if row["source_date"] else None,
        "variation_pct": variation.pct,
    }


def latest_payload(
    conn: sqlite3.Connection, indicator: sqlite3.Row, max_gap_days: int
) -> dict[str, Any] | None:
    row = latest_reading(conn, int(indicator["id"]))
    if row is None:
        return None
    return _reading_payload(conn, indicator, row, max_gap_days)


def catalog_entry(
    conn: sqlite3.Connection, indicator: sqlite3.Row, max_gap_days: int
) -> dict[str, Any]:
    return {
        "slug": str(indicator["slug"]),
        "source_url": str(indicator["source_url"]),
        "visible_label": str(indicator["visible_label"]),
        "unit": str(indicator["unit"]),
        "latest": latest_payload(conn, indicator, max_gap_days),
    }


def history_payload(
    conn: sqlite3.Connection,
    indicator: sqlite3.Row,
    max_gap_days: int,
    history_days: int,
    from_date: date | None = None,
    to_date: date | None = None,
) -> dict[str, Any]:
    end = to_date or date.today()
    start = from_date or (end - timedelta(days=history_days - 1))
    rows = conn.execute(
        """
        SELECT * FROM reading
        WHERE indicator_id = ? AND date >= ? AND date <= ?
        ORDER BY date ASC
        """,
        (int(indicator["id"]), start.isoformat(), end.isoformat()),
    ).fetchall()
    return {
        "slug": str(indicator["slug"]),
        "unit": str(indicator["unit"]),
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "readings": [_reading_payload(conn, indicator, row, max_gap_days) for row in rows],
    }


def variation_payload(
    conn: sqlite3.Connection, indicator: sqlite3.Row, max_gap_days: int
) -> dict[str, Any] | None:
    row = latest_reading(conn, int(indicator["id"]))
    if row is None:
        return None
    reading_date = date.fromisoformat(str(row["date"]))
    previous = previous_reading(conn, int(indicator["id"]), reading_date, max_gap_days)
    variation = compute_variation(
        float(row["value"]),
        reading_date,
        float(previous["value"]) if previous else None,
        date.fromisoformat(str(previous["date"])) if previous else None,
        max_gap_days,
    )
    return {
        "slug": str(indicator["slug"]),
        "date": reading_date.isoformat(),
        "value": float(row["value"]),
        "previous_date": variation.base_date.isoformat() if variation.base_date else None,
        "previous_value": float(previous["value"]) if previous else None,
        "variation_pct": variation.pct,
        "base_gap": variation.base_gap,
    }
