"""Contract tests for the persisted reading model consumed by the API.

These tests pin the storage contract (schema + variation semantics) that the
FastAPI layer and the static Pages JSON will expose.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from etl.models import AppConfig, Reading
from etl.storage import connect, indicator_id, init_db, sync_indicators, upsert_reading
from etl.variation import compute_variation


@pytest.fixture
def conn(tmp_path: Path, config: AppConfig):  # type: ignore[no-untyped-def]
    connection = connect(tmp_path / "contract.db")
    init_db(connection)
    sync_indicators(connection, config.indicators)
    yield connection
    connection.close()


def insert(conn, iid: int, day: date, value: float, status: str = "ok") -> None:  # type: ignore[no-untyped-def]
    upsert_reading(
        conn,
        Reading(
            indicator_id=iid,
            date=day,
            value=value,
            value_published=None,
            unit_as_published="COP",
            raw_label="label",
            raw_text="raw",
            source_date=day,
            status=status,
            base_gap=False,
            reason=None,
            created_at="2026-10-08T00:00:00Z",
            run_id="contract",
        ),
    )


def test_latest_and_history_contract(conn) -> None:  # type: ignore[no-untyped-def]
    iid = indicator_id(conn, "uvr")
    insert(conn, iid, date(2026, 10, 7), 419.0)
    insert(conn, iid, date(2026, 10, 8), 419.29)
    latest = conn.execute(
        "SELECT * FROM reading WHERE indicator_id = ? ORDER BY date DESC LIMIT 1", (iid,)
    ).fetchone()
    assert latest["value"] == pytest.approx(419.29)
    history = conn.execute(
        "SELECT * FROM reading WHERE indicator_id = ? ORDER BY date ASC", (iid,)
    ).fetchall()
    assert [row["date"] for row in history] == ["2026-10-07", "2026-10-08"]
    variation = compute_variation(
        history[-1]["value"],
        date.fromisoformat(history[-1]["date"]),
        history[0]["value"],
        date.fromisoformat(history[0]["date"]),
    )
    assert variation.pct == pytest.approx((419.29 - 419.0) / 419.0 * 100)
    assert variation.base_gap is False


def test_forward_filled_row_contract(conn) -> None:  # type: ignore[no-untyped-def]
    iid = indicator_id(conn, "euro")
    insert(conn, iid, date(2026, 10, 7), 3625.6)
    insert(conn, iid, date(2026, 10, 8), 3625.6, status="forward_filled")
    row = conn.execute(
        "SELECT * FROM reading WHERE indicator_id = ? AND date = '2026-10-08'", (iid,)
    ).fetchone()
    assert row["status"] == "forward_filled"
    assert row["value"] == pytest.approx(3625.6)


def test_status_enum_contract(conn) -> None:  # type: ignore[no-untyped-def]
    iid = indicator_id(conn, "euro")
    for status in ("ok", "forward_filled", "manual"):
        insert(conn, iid, date(2026, 10, 8), 1.0, status=status)
        conn.execute("DELETE FROM reading WHERE indicator_id = ?", (iid,))
