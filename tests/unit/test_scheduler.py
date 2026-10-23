"""Scheduler tests: Bogota cron windows, decisions, forcing and gh dispatch."""

from __future__ import annotations

import dataclasses
import subprocess
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from form_updater.scheduler import (
    BOGOTA,
    ScheduleDecision,
    cron_window,
    due_occurrence,
    filter_pending,
    forced_decision,
    load_settings,
    load_state,
    plan,
    save_state,
    trigger_workflow,
    wait_for_workflow,
)

# Monday 2026-10-12 (2026-10-12 is a Monday).
MONDAY_2130 = datetime(2026, 10, 12, 21, 30, 10, tzinfo=BOGOTA)
TUESDAY_2130 = datetime(2026, 10, 13, 21, 30, 10, tzinfo=BOGOTA)


def test_cron_window_matches_exact_minute() -> None:
    occurrence = cron_window("30 21 * * *", MONDAY_2130)
    assert occurrence is not None
    assert occurrence.hour == 21
    assert occurrence.minute == 30


def test_cron_window_tolerates_queue_delay() -> None:
    delayed = MONDAY_2130 + timedelta(minutes=3)
    assert cron_window("30 21 * * *", delayed) is not None


def test_cron_window_too_late_does_not_match() -> None:
    late = MONDAY_2130 + timedelta(minutes=6)
    assert cron_window("30 21 * * *", late) is None
    assert cron_window("0 21 * * *", MONDAY_2130) is None  # prev 21:00, 30 min ago


def test_cron_window_day_of_week() -> None:
    # 1 = Monday. Monday matches; Tuesday's most recent Monday is a day old.
    assert cron_window("0 21 * * 1", datetime(2026, 10, 12, 21, 0, 5, tzinfo=BOGOTA)) is not None
    assert cron_window("0 21 * * 1", TUESDAY_2130) is None


def test_cron_window_invalid_expression() -> None:
    with pytest.raises(ValueError):
        cron_window("banana", MONDAY_2130)


def test_plan_at_etl_window_triggers_etl_only() -> None:
    settings = {"horario_etl": "0 21 * * *", "horario_updater": "30 21 * * *"}
    now = datetime(2026, 10, 12, 21, 0, 5, tzinfo=BOGOTA).astimezone(UTC)
    outcome = plan(settings, now, "scrape.yml", "form_update.yml")
    assert [decision.workflow for decision in outcome.triggers] == ["scrape.yml"]
    assert outcome.triggers[0].key == "etl"
    assert outcome.critical == []


def test_plan_catchup_triggers_both_on_delayed_run() -> None:
    # Run executed at 23:51 Bogota (GitHub delay); both occurrences are due
    # within the 8-hour catch-up horizon, in order: ETL first, updater second.
    settings = {"horario_etl": "0 21 * * *", "horario_updater": "30 21 * * *"}
    now = datetime(2026, 10, 12, 23, 51, 0, tzinfo=BOGOTA).astimezone(UTC)
    outcome = plan(settings, now, "scrape.yml", "form_update.yml", catchup_hours=8)
    assert [decision.key for decision in outcome.triggers] == ["etl", "updater"]


def test_plan_ignores_occurrences_outside_catchup() -> None:
    settings = {"horario_etl": "0 21 * * *", "horario_updater": "30 21 * * *"}
    now = datetime(2026, 10, 12, 20, 41, 0, tzinfo=BOGOTA).astimezone(UTC)
    outcome = plan(settings, now, "scrape.yml", "form_update.yml", catchup_hours=8)
    assert outcome.triggers == []


def test_plan_invalid_cron_is_critical_and_ignored() -> None:
    settings = {"horario_etl": "nope", "horario_updater": "30 21 * * *"}
    outcome = plan(settings, MONDAY_2130.astimezone(UTC), "scrape.yml", "form_update.yml")
    assert [decision.workflow for decision in outcome.triggers] == ["form_update.yml"]
    assert any("invalid" in message for message in outcome.critical)


def test_plan_missing_setting_warns() -> None:
    outcome = plan(
        {"horario_updater": "30 21 * * *"}, MONDAY_2130.astimezone(UTC), "s.yml", "u.yml"
    )
    assert any("missing" in warning for warning in outcome.warnings)
    assert outcome.triggers
    assert outcome.triggers[0].workflow == "u.yml"


def test_due_occurrence_accepts_delayed_run() -> None:
    # 23:51 Bogota is 2h51m after the 21:00 occurrence.
    now = datetime(2026, 10, 12, 23, 51, 0, tzinfo=BOGOTA)
    occurrence = due_occurrence("0 21 * * *", now, catchup_hours=8)
    assert occurrence is not None
    assert occurrence.hour == 21


def test_due_occurrence_crosses_midnight() -> None:
    # 01:56 next day is 4h56m after the previous day's 21:30 occurrence.
    now = datetime(2026, 10, 13, 1, 56, 0, tzinfo=BOGOTA)
    occurrence = due_occurrence("30 21 * * *", now, catchup_hours=8)
    assert occurrence is not None
    assert occurrence.date().isoformat() == "2026-10-12"
    assert occurrence.hour == 21


def test_due_occurrence_rejects_beyond_catchup() -> None:
    now = datetime(2026, 10, 13, 7, 0, 0, tzinfo=BOGOTA)
    assert due_occurrence("0 21 * * *", now, catchup_hours=8) is None


def test_filter_pending_dedup_by_origin_and_date() -> None:
    decision = ScheduleDecision(
        key="etl",
        workflow="scrape.yml",
        expression="0 21 * * *",
        occurrence=datetime(2026, 10, 12, 21, 0, 0, tzinfo=BOGOTA),
    )
    state = {"etl": {"natural": "2026-10-12"}}
    pending, skipped = filter_pending([decision], state, "natural")
    assert pending == []
    assert skipped == ["etl@2026-10-12(natural)"]
    # A natural trigger must NOT veto the forced/test run.
    pending, skipped = filter_pending([decision], state, "forced")
    assert pending == [decision]
    assert skipped == []


def test_forced_today_does_not_block_natural_today() -> None:
    decision = ScheduleDecision(
        key="updater",
        workflow="form_update.yml",
        expression="30 21 * * *",
        occurrence=datetime(2026, 10, 12, 21, 30, 0, tzinfo=BOGOTA),
    )
    state = {"updater": {"forced": "2026-10-12"}}
    pending, _ = filter_pending([decision], state, "natural")
    assert pending == [decision]


def test_natural_today_does_not_block_natural_tomorrow() -> None:
    today_decision = ScheduleDecision(
        key="etl",
        workflow="scrape.yml",
        expression="0 21 * * *",
        occurrence=datetime(2026, 10, 12, 21, 0, 0, tzinfo=BOGOTA),
    )
    tomorrow_decision = ScheduleDecision(
        key="etl",
        workflow="scrape.yml",
        expression="0 21 * * *",
        occurrence=datetime(2026, 10, 13, 21, 0, 0, tzinfo=BOGOTA),
    )
    state = {"etl": {"natural": "2026-10-12"}}
    pending, _ = filter_pending([today_decision, tomorrow_decision], state, "natural")
    assert pending == [tomorrow_decision]


def test_double_natural_same_day_is_deduped() -> None:
    decision = ScheduleDecision(
        key="etl",
        workflow="scrape.yml",
        expression="0 21 * * *",
        occurrence=datetime(2026, 10, 12, 21, 0, 0, tzinfo=BOGOTA),
    )
    state = {"etl": {"natural": "2026-10-12", "forced": "2026-10-12"}}
    pending, skipped = filter_pending([decision], state, "natural")
    assert pending == []
    assert skipped == ["etl@2026-10-12(natural)"]


def test_punctual_clock_consumes_occurrence_and_backup_skips() -> None:
    settings = {"horario_etl": "0 19 * * *", "horario_updater": "0 20 * * *"}
    state: dict[str, dict[str, str]] = {}

    at_1900 = datetime(2026, 10, 22, 19, 0, 5, tzinfo=BOGOTA).astimezone(UTC)
    etl_outcome = plan(settings, at_1900, "scrape.yml", "form_update.yml")
    assert [decision.key for decision in etl_outcome.triggers] == ["etl"]
    pending, _ = filter_pending(etl_outcome.triggers, state, "natural")
    assert [decision.key for decision in pending] == ["etl"]
    state["etl"] = {"natural": "2026-10-22"}

    at_2000 = datetime(2026, 10, 22, 20, 0, 5, tzinfo=BOGOTA).astimezone(UTC)
    updater_outcome = plan(settings, at_2000, "scrape.yml", "form_update.yml")
    assert [decision.key for decision in updater_outcome.triggers] == ["etl", "updater"]
    pending, skipped = filter_pending(updater_outcome.triggers, state, "natural")
    assert [decision.key for decision in pending] == ["updater"]
    assert skipped == ["etl@2026-10-22(natural)"]
    state["updater"] = {"natural": "2026-10-22"}

    # The 6 h backup at 23:00 is still inside the 8 h catch-up, but both
    # natural occurrences were already consumed by the punctual clock.
    at_2300 = datetime(2026, 10, 22, 23, 0, 5, tzinfo=BOGOTA).astimezone(UTC)
    backup = plan(settings, at_2300, "scrape.yml", "form_update.yml", catchup_hours=8)
    assert [decision.key for decision in backup.triggers] == ["etl", "updater"]
    pending, skipped = filter_pending(backup.triggers, state, "natural")
    assert pending == []
    assert skipped == ["etl@2026-10-22(natural)", "updater@2026-10-22(natural)"]


def test_state_roundtrip_and_legacy_migration(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "state.json"
    assert load_state(path) == {}
    save_state(path, {"etl": {"natural": "2026-10-12", "forced": "2026-10-11"}})
    assert load_state(path) == {"etl": {"natural": "2026-10-12", "forced": "2026-10-11"}}
    # Legacy string from the old forced/test path is treated as forced.
    path.write_text('{"etl": "2026-10-12"}', encoding="utf-8")
    assert load_state(path) == {"etl": {"forced": "2026-10-12"}}


def test_wait_for_workflow_polls_until_completed() -> None:
    responses = [
        {
            "workflow_runs": [
                {
                    "created_at": "2026-10-12T21:00:05Z",
                    "status": "in_progress",
                    "conclusion": None,
                }
            ]
        },
        {
            "workflow_runs": [
                {
                    "created_at": "2026-10-12T21:00:05Z",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        },
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    run = wait_for_workflow(
        "scrape.yml",
        after_utc=datetime(2026, 10, 12, 21, 0, 0, tzinfo=UTC),
        repo="org/repo",
        token="token",
        timeout_seconds=5,
        poll_seconds=0.01,
        client=client,
    )
    assert run["status"] == "completed"
    assert run["conclusion"] == "success"


def test_wait_for_workflow_times_out() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {
                        "created_at": "2026-10-12T21:00:05Z",
                        "status": "in_progress",
                        "conclusion": None,
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(TimeoutError):
        wait_for_workflow(
            "scrape.yml",
            after_utc=datetime(2026, 10, 12, 21, 0, 0, tzinfo=UTC),
            repo="org/repo",
            token="token",
            timeout_seconds=0.05,
            poll_seconds=0.01,
            client=client,
        )


def test_forced_decision() -> None:
    decision = forced_decision("etl", "scrape.yml", "form_update.yml", MONDAY_2130.astimezone(UTC))
    assert decision.workflow == "scrape.yml"
    assert decision.key == "etl"
    with pytest.raises(ValueError):
        forced_decision("nope", "s.yml", "u.yml", MONDAY_2130.astimezone(UTC))


def test_trigger_workflow_uses_gh_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setenv("GITHUB_TOKEN", "token-123")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    trigger_workflow("scrape.yml", ref="main")
    assert captured["command"] == ["gh", "workflow", "run", "scrape.yml", "--ref", "main"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "token-123"


def test_trigger_workflow_passes_write_mode_for_updater(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    trigger_workflow("form_update.yml", ref="main", inputs={"mode": "write"})
    assert captured["command"] == [
        "gh",
        "workflow",
        "run",
        "form_update.yml",
        "--ref",
        "main",
        "-f",
        "mode=write",
    ]


def test_load_settings_reads_snapshot(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from form_updater.sheet import write_snapshot

    path = tmp_path / "sheet_config.csv"
    write_snapshot(path, [], {"horario_etl": "0 21 * * *"}, "indicators_config", "ajustes")
    assert load_settings(path)["horario_etl"] == "0 21 * * *"


def test_schedule_decision_is_frozen() -> None:
    decision = ScheduleDecision(
        key="etl", workflow="scrape.yml", expression="0 21 * * *", occurrence=MONDAY_2130
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.key = "updater"  # type: ignore[misc]


def test_bogota_zone_is_utc_minus_five() -> None:
    assert ZoneInfo("America/Bogota").utcoffset(MONDAY_2130) == timedelta(hours=-5)


def test_forced_dedup_uses_exact_occurrence_timestamp() -> None:
    decision = ScheduleDecision(
        key="etl",
        workflow="scrape.yml",
        expression="35 11 * * *",
        occurrence=datetime(2026, 10, 20, 11, 35, 0, tzinfo=BOGOTA),
    )
    state = {"etl": {"forced": decision.occurrence.isoformat()}}
    pending, skipped = filter_pending([decision], state, "forced")
    assert pending == []
    assert skipped == [f"etl@{decision.occurrence.isoformat()}(forced)"]
    # An earlier ad-hoc forced write stored as a date does not block the 11:35 test.
    pending, _ = filter_pending([decision], {"etl": {"forced": "2026-10-20"}}, "forced")
    assert pending == [decision]


def test_natural_vetoes_are_explicit() -> None:
    from form_updater.scheduler import natural_vetoes

    skipped = ["etl@2026-10-20(natural)", "updater@2026-10-20(forced)"]
    assert natural_vetoes(skipped) == ["etl@2026-10-20(natural)"]
    assert natural_vetoes(["etl@2026-10-20(forced)"]) == []


def test_hourly_run_at_window_dispatches() -> None:
    settings = {"horario_etl": "0 21 * * *", "horario_updater": "30 21 * * *"}
    now = datetime(2026, 10, 12, 21, 0, 2, tzinfo=BOGOTA).astimezone(UTC)
    outcome = plan(settings, now, "scrape.yml", "form_update.yml")
    assert [decision.key for decision in outcome.triggers] == ["etl"]


def test_hourly_run_catches_non_hour_window() -> None:
    # The 21:30 updater window is not on the hour; the 22:00 hourly run catches
    # it (30 min late) and also the 21:00 ETL window (1 h late) via catch-up.
    settings = {"horario_etl": "0 21 * * *", "horario_updater": "30 21 * * *"}
    now = datetime(2026, 10, 12, 22, 0, 2, tzinfo=BOGOTA).astimezone(UTC)
    outcome = plan(settings, now, "scrape.yml", "form_update.yml")
    keys = [decision.key for decision in outcome.triggers]
    assert "updater" in keys
    updater = next(decision for decision in outcome.triggers if decision.key == "updater")
    assert updater.occurrence.hour == 21
    assert updater.occurrence.minute == 30


def test_hourly_run_with_no_due_window_does_nothing() -> None:
    settings = {"horario_etl": "0 21 * * *", "horario_updater": "30 21 * * *"}
    now = datetime(2026, 10, 12, 20, 0, 2, tzinfo=BOGOTA).astimezone(UTC)
    outcome = plan(settings, now, "scrape.yml", "form_update.yml")
    assert outcome.triggers == []


def test_consumed_non_hour_window_not_redispatched_next_hour() -> None:
    settings = {"horario_etl": "0 21 * * *", "horario_updater": "30 21 * * *"}
    now = datetime(2026, 10, 12, 23, 0, 2, tzinfo=BOGOTA).astimezone(UTC)
    outcome = plan(settings, now, "scrape.yml", "form_update.yml")
    pending, skipped = filter_pending(
        outcome.triggers, {"updater": {"natural": "2026-10-12"}}, "natural"
    )
    assert all(decision.key != "updater" for decision in pending)
    assert "updater@2026-10-12(natural)" in skipped
