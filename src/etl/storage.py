"""SQLite storage: schema, upserts, queries and 31-day pruning.

The database file is committed by the workflow; git is the backup.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

from etl.models import IndicatorConfig, Reading

SCHEMA = """
CREATE TABLE IF NOT EXISTS indicator (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    visible_label TEXT NOT NULL,
    unit TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reading (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_id INTEGER NOT NULL REFERENCES indicator(id),
    date DATE NOT NULL,
    value REAL NOT NULL,
    value_published REAL,
    unit_as_published TEXT NOT NULL DEFAULT '',
    raw_label TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    source_date DATE,
    status TEXT NOT NULL CHECK (status IN ('ok', 'forward_filled', 'manual')),
    base_gap BOOLEAN NOT NULL DEFAULT 0,
    reason TEXT,
    created_at TIMESTAMP NOT NULL,
    run_id TEXT NOT NULL,
    UNIQUE (indicator_id, date)
);

CREATE INDEX IF NOT EXISTS idx_reading_indicator_date
    ON reading (indicator_id, date DESC);
"""


def connect(db_path: Path | str, read_only: bool = False) -> sqlite3.Connection:
    """Open the database, optionally in read-only URI mode (for the API)."""

    path = Path(db_path)
    if read_only:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def sync_indicators(conn: sqlite3.Connection, indicators: Iterable[IndicatorConfig]) -> None:
    """Upsert the indicator catalog from the declarative config."""

    for indicator in indicators:
        conn.execute(
            """
            INSERT INTO indicator (slug, source_url, visible_label, unit)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                source_url = excluded.source_url,
                visible_label = excluded.visible_label,
                unit = excluded.unit
            """,
            (indicator.slug, indicator.url, indicator.visible_label, indicator.unit),
        )
    conn.commit()


def indicator_id(conn: sqlite3.Connection, slug: str) -> int:
    row = conn.execute("SELECT id FROM indicator WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise KeyError(f"indicator not found in database: {slug}")
    return int(row["id"])


def indicator_slugs(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT id, slug FROM indicator").fetchall()
    return {int(row["id"]): str(row["slug"]) for row in rows}


def upsert_reading(conn: sqlite3.Connection, reading: Reading) -> None:
    conn.execute(
        """
        INSERT INTO reading (
            indicator_id, date, value, value_published, unit_as_published,
            raw_label, raw_text, source_date, status, base_gap, reason,
            created_at, run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(indicator_id, date) DO UPDATE SET
            value = excluded.value,
            value_published = excluded.value_published,
            unit_as_published = excluded.unit_as_published,
            raw_label = excluded.raw_label,
            raw_text = excluded.raw_text,
            source_date = excluded.source_date,
            status = excluded.status,
            base_gap = excluded.base_gap,
            reason = excluded.reason,
            created_at = excluded.created_at,
            run_id = excluded.run_id
        """,
        (
            reading.indicator_id,
            reading.date.isoformat(),
            reading.value,
            reading.value_published,
            reading.unit_as_published,
            reading.raw_label,
            reading.raw_text,
            reading.source_date.isoformat() if reading.source_date else None,
            reading.status,
            int(reading.base_gap),
            reading.reason,
            reading.created_at,
            reading.run_id,
        ),
    )
    conn.commit()


def previous_reading(
    conn: sqlite3.Connection, indicator_id_value: int, run_date: date, max_gap_days: int
) -> sqlite3.Row | None:
    """Most recent reading strictly before ``run_date`` within ``max_gap_days``."""

    earliest = run_date - timedelta(days=max_gap_days)
    row = conn.execute(
        """
        SELECT * FROM reading
        WHERE indicator_id = ? AND date < ? AND date >= ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (indicator_id_value, run_date.isoformat(), earliest.isoformat()),
    ).fetchone()
    return cast("sqlite3.Row | None", row)


def latest_reading(conn: sqlite3.Connection, indicator_id_value: int) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT * FROM reading
        WHERE indicator_id = ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (indicator_id_value,),
    ).fetchone()
    return cast("sqlite3.Row | None", row)


def reading_on(conn: sqlite3.Connection, indicator_id_value: int, day: date) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM reading WHERE indicator_id = ? AND date = ?",
        (indicator_id_value, day.isoformat()),
    ).fetchone()
    return cast("sqlite3.Row | None", row)


def history(
    conn: sqlite3.Connection,
    slug: str,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[sqlite3.Row]:
    query = """
        SELECT r.* FROM reading r
        JOIN indicator i ON i.id = r.indicator_id
        WHERE i.slug = ?
    """
    params: list[str] = [slug]
    if from_date:
        query += " AND r.date >= ?"
        params.append(from_date.isoformat())
    if to_date:
        query += " AND r.date <= ?"
        params.append(to_date.isoformat())
    query += " ORDER BY r.date ASC"
    return list(conn.execute(query, params).fetchall())


def prune(conn: sqlite3.Connection, history_days: int, today: date) -> int:
    """Delete readings outside the rolling window and VACUUM.

    Keeps ``history_days`` natural days including today.
    """

    cutoff = today - timedelta(days=history_days - 1)
    cursor = conn.execute("DELETE FROM reading WHERE date < ?", (cutoff.isoformat(),))
    deleted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    conn.commit()
    conn.execute("VACUUM")
    return deleted


def rows_to_dicts(rows: Sequence[sqlite3.Row]) -> list[dict[str, object]]:
    return [dict(row) for row in rows]


def now_utc_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
