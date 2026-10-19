"""Watchdog tests: stale writes and consecutive zero-write runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from form_updater.watchdog import evaluate_entries, load_entries, record_write

NOW = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)


def entry(hours_ago: float, written: int, run_id: str = "run") -> dict:
    return {
        "date": "2026-10-19",
        "written": written,
        "failed": 0,
        "omitted": 0,
        "recorded_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "run_id": run_id,
    }


def test_healthy_recent_write() -> None:
    assert evaluate_entries([entry(1, 18)], NOW) == []


def test_stale_write_triggers_problem() -> None:
    problems = evaluate_entries([entry(30, 18)], NOW, max_age_hours=26)
    assert len(problems) == 1
    assert "has not written" in problems[0]


def test_two_consecutive_zero_write_runs() -> None:
    problems = evaluate_entries([entry(9, 0), entry(33, 0)], NOW, max_age_hours=48)
    assert any("wrote 0 values" in problem for problem in problems)


def test_one_zero_write_run_is_not_enough() -> None:
    problems = evaluate_entries([entry(9, 0), entry(33, 12)], NOW, max_age_hours=48)
    assert not any("wrote 0 values" in problem for problem in problems)


def test_empty_state_is_a_problem() -> None:
    problems = evaluate_entries([], NOW)
    assert problems == ["form updater has no write state (never wrote?)"]


def test_record_write_roundtrip_and_upsert(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    record_write(
        path, day="2026-10-19", written=18, failed=0, omitted=0, run_id="r1", recorded_at=NOW
    )
    record_write(
        path,
        day="2026-10-19",
        written=17,
        failed=1,
        omitted=0,
        run_id="r2",
        recorded_at=NOW + timedelta(minutes=1),
    )
    entries = load_entries(path)
    assert len(entries) == 1  # same date is updated, not duplicated
    assert entries[0]["written"] == 17
    assert entries[0]["run_id"] == "r2"
    assert evaluate_entries(entries, NOW + timedelta(minutes=2)) == []
