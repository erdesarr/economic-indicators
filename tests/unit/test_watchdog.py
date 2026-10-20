"""Watchdog tests: natural writes only; forced writes never reset the clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from form_updater.watchdog import evaluate_entries, load_entries, record_write

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)


def entry(
    hours_ago: float,
    written: int,
    run_id: str = "run",
    origin: str = "natural",
) -> dict:
    return {
        "date": "2026-10-20",
        "written": written,
        "failed": 0,
        "omitted": 0,
        "recorded_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "run_id": run_id,
        "origin": origin,
    }


def test_healthy_recent_natural_write() -> None:
    assert evaluate_entries([entry(1, 18)], NOW) == []


def test_stale_natural_write_triggers_problem() -> None:
    problems = evaluate_entries([entry(30, 18)], NOW, max_age_hours=26)
    assert len(problems) == 1
    assert "has not written naturally" in problems[0]


def test_forced_recent_write_does_not_mask_natural_silence() -> None:
    # Forced write 1h ago, last natural write 30h ago -> still alert.
    problems = evaluate_entries(
        [entry(1, 18, origin="forced"), entry(30, 18, origin="natural")],
        NOW,
        max_age_hours=26,
    )
    assert any("has not written naturally" in problem for problem in problems)


def test_only_forced_writes_is_a_problem() -> None:
    problems = evaluate_entries([entry(1, 18, origin="forced")], NOW)
    assert problems == ["form updater has no NATURAL write recorded (forced writes do not count)"]


def test_legacy_entries_without_origin_count_as_forced() -> None:
    legacy = entry(1, 18)
    legacy.pop("origin")
    problems = evaluate_entries([legacy], NOW)
    assert problems == ["form updater has no NATURAL write recorded (forced writes do not count)"]


def test_two_consecutive_natural_zero_write_runs() -> None:
    problems = evaluate_entries(
        [entry(9, 0, origin="natural"), entry(33, 0, origin="natural")],
        NOW,
        max_age_hours=48,
    )
    assert any("wrote 0 values" in problem for problem in problems)


def test_forced_zero_write_runs_are_ignored() -> None:
    problems = evaluate_entries(
        [
            entry(9, 0, origin="forced"),
            entry(33, 0, origin="forced"),
            entry(2, 18, origin="natural"),
        ],
        NOW,
        max_age_hours=48,
    )
    assert problems == []


def test_one_zero_natural_write_run_is_not_enough() -> None:
    problems = evaluate_entries(
        [entry(9, 0, origin="natural"), entry(33, 12, origin="natural")],
        NOW,
        max_age_hours=48,
    )
    assert not any("wrote 0 values" in problem for problem in problems)


def test_empty_state_is_a_problem() -> None:
    problems = evaluate_entries([], NOW)
    assert problems == ["form updater has no NATURAL write recorded (forced writes do not count)"]


def test_record_write_roundtrip_and_upsert(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    record_write(
        path,
        day="2026-10-20",
        written=18,
        failed=0,
        omitted=0,
        run_id="r1",
        origin="natural",
        recorded_at=NOW,
    )
    record_write(
        path,
        day="2026-10-20",
        written=17,
        failed=1,
        omitted=0,
        run_id="r2",
        origin="natural",
        recorded_at=NOW + timedelta(minutes=1),
    )
    entries = load_entries(path)
    assert len(entries) == 1  # same date is updated, not duplicated
    assert entries[0]["written"] == 17
    assert entries[0]["run_id"] == "r2"
    assert entries[0]["origin"] == "natural"
    assert evaluate_entries(entries, NOW + timedelta(minutes=2)) == []


def test_forced_and_natural_same_date_coexist(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    record_write(
        path,
        day="2026-10-20",
        written=17,
        failed=1,
        omitted=0,
        run_id="forced-run",
        origin="forced",
        recorded_at=NOW,
    )
    record_write(
        path,
        day="2026-10-20",
        written=18,
        failed=0,
        omitted=0,
        run_id="natural-run",
        origin="natural",
        recorded_at=NOW + timedelta(minutes=1),
    )
    entries = load_entries(path)
    assert len(entries) == 2
    assert evaluate_entries(entries, NOW + timedelta(minutes=2)) == []
