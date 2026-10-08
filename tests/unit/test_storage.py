"""Storage tests: schema, upserts, previous lookup and 31-day pruning."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from etl.models import AppConfig, Reading
from etl.storage import (
    connect,
    indicator_id,
    init_db,
    previous_reading,
    prune,
    reading_on,
    sync_indicators,
    upsert_reading,
)


@pytest.fixture
def db(tmp_path: Path, config: AppConfig) -> sqlite3.Connection:
    conn = connect(tmp_path / "test.db")
    init_db(conn)
    sync_indicators(conn, config.indicators)
    yield conn
    conn.close()


def make_reading(iid: int, day: date, value: float, **overrides: object) -> Reading:
    defaults: dict[str, object] = {
        "indicator_id": iid,
        "date": day,
        "value": value,
        "value_published": value * 2,
        "unit_as_published": "unidad",
        "raw_label": "label",
        "raw_text": "raw",
        "source_date": day,
        "status": "ok",
        "base_gap": False,
        "reason": None,
        "created_at": "2026-10-08T00:00:00Z",
        "run_id": "test-run",
    }
    defaults.update(overrides)
    return Reading(**defaults)  # type: ignore[arg-type]


def test_schema_has_required_columns(db: sqlite3.Connection) -> None:
    columns = {row["name"] for row in db.execute("PRAGMA table_info(reading)")}
    required = {
        "id",
        "indicator_id",
        "date",
        "value",
        "value_published",
        "unit_as_published",
        "raw_label",
        "raw_text",
        "source_date",
        "status",
        "base_gap",
        "reason",
        "created_at",
        "run_id",
    }
    assert required <= columns
    indicator_columns = {row["name"] for row in db.execute("PRAGMA table_info(indicator)")}
    assert {"id", "slug", "source_url", "visible_label", "unit"} <= indicator_columns


def test_status_check_rejects_invalid(db: sqlite3.Connection) -> None:
    iid = indicator_id(db, "dolar_oficial_hoy")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO reading (indicator_id, date, value, status, created_at, run_id) "
            "VALUES (?, ?, ?, 'invented', 'now', 'r')",
            (iid, "2026-10-08", 1.0),
        )


def test_unique_indicator_date_upserts(db: sqlite3.Connection) -> None:
    iid = indicator_id(db, "dolar_oficial_hoy")
    day = date(2026, 10, 8)
    upsert_reading(db, make_reading(iid, day, 3200.0))
    upsert_reading(db, make_reading(iid, day, 3300.0))
    rows = db.execute("SELECT value FROM reading WHERE indicator_id = ?", (iid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["value"] == 3300.0


def test_previous_reading_respects_max_gap(db: sqlite3.Connection) -> None:
    iid = indicator_id(db, "uvr")
    upsert_reading(db, make_reading(iid, date(2026, 10, 1), 400.0))
    upsert_reading(db, make_reading(iid, date(2026, 10, 7), 410.0))
    previous = previous_reading(db, iid, date(2026, 10, 8), max_gap_days=7)
    assert previous is not None
    assert previous["date"] == "2026-10-07"
    missing = previous_reading(db, iid, date(2026, 10, 20), max_gap_days=7)
    assert missing is None


def test_prune_keeps_31_days(db: sqlite3.Connection) -> None:
    iid = indicator_id(db, "uvr")
    today = date(2026, 10, 8)
    for offset in range(0, 40):
        day = today - timedelta(days=offset)
        upsert_reading(db, make_reading(iid, day, 400.0 + offset))
    deleted = prune(db, history_days=31, today=today)
    assert deleted == 9  # days 31..39 fall outside the window
    remaining = db.execute("SELECT COUNT(*) AS n FROM reading").fetchone()["n"]
    assert remaining == 31
    oldest = db.execute("SELECT MIN(date) AS d FROM reading").fetchone()["d"]
    assert oldest == (today - timedelta(days=30)).isoformat()


def test_reading_on_and_indicator_id(db: sqlite3.Connection) -> None:
    iid = indicator_id(db, "uvr")
    upsert_reading(db, make_reading(iid, date(2026, 10, 8), 419.29))
    row = reading_on(db, iid, date(2026, 10, 8))
    assert row is not None
    assert row["value"] == pytest.approx(419.29)
    with pytest.raises(KeyError):
        indicator_id(db, "no_existe")


def test_read_only_connection_rejects_writes(tmp_path: Path, config: AppConfig) -> None:
    path = tmp_path / "ro.db"
    conn = connect(path)
    init_db(conn)
    conn.close()
    ro = connect(path, read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute(
                "INSERT INTO indicator (slug, source_url, visible_label, unit) "
                "VALUES ('x','y','z','w')"
            )
    finally:
        ro.close()
