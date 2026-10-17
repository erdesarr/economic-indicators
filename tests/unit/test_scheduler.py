"""Scheduler tests: Bogota cron windows, decisions, forcing and gh dispatch."""

from __future__ import annotations

import dataclasses
import subprocess
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from form_updater.scheduler import (
    BOGOTA,
    ScheduleDecision,
    cron_window,
    forced_decision,
    load_settings,
    plan,
    trigger_workflow,
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


def test_plan_triggers_only_matching_workflow() -> None:
    settings = {"horario_etl": "0 21 * * *", "horario_updater": "30 21 * * *"}
    now_utc = MONDAY_2130.astimezone(UTC)
    outcome = plan(settings, now_utc, "scrape.yml", "form_update.yml")
    assert [decision.workflow for decision in outcome.triggers] == ["form_update.yml"]
    assert outcome.triggers[0].key == "updater"
    assert outcome.critical == []


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
