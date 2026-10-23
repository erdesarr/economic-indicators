"""Regression: scheduler target workflow filenames and triggers stay in sync."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CONFIG = REPO_ROOT / "config" / "form_updater.yaml"


def _on(data: dict) -> dict:
    return data.get(True) or data.get("on") or {}


def test_form_update_has_workflow_dispatch_and_matches_scheduler() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    scheduler = config["scheduler"]
    assert scheduler["workflow_updater"] == "form_update.yml"
    assert scheduler["workflow_etl"] == "scrape.yml"
    assert (WORKFLOWS / scheduler["workflow_updater"]).exists()
    assert (WORKFLOWS / scheduler["workflow_etl"]).exists()

    form_update = yaml.safe_load((WORKFLOWS / "form_update.yml").read_text(encoding="utf-8"))
    assert "workflow_dispatch" in _on(form_update)
    assert "workflow_call" in _on(form_update)

    scrape = yaml.safe_load((WORKFLOWS / "scrape.yml").read_text(encoding="utf-8"))
    assert "workflow_dispatch" in _on(scrape)


def test_scheduler_cron_is_six_hour_backup() -> None:
    scheduler = yaml.safe_load((WORKFLOWS / "scheduler.yml").read_text(encoding="utf-8"))
    trigger = _on(scheduler)
    assert "schedule" in trigger
    assert any(item.get("cron") == "0 */6 * * *" for item in trigger["schedule"])
    assert "workflow_dispatch" in trigger


def test_sync_cron_is_six_hour_backup() -> None:
    sync = yaml.safe_load((WORKFLOWS / "sync_config.yml").read_text(encoding="utf-8"))
    trigger = _on(sync)
    assert "schedule" in trigger
    assert any(item.get("cron") == "0 */6 * * *" for item in trigger["schedule"])
    assert "workflow_dispatch" in trigger


def test_config_refresh_runs_sync_then_write() -> None:
    refresh = yaml.safe_load((WORKFLOWS / "config_refresh.yml").read_text(encoding="utf-8"))
    trigger = _on(refresh)
    assert "workflow_dispatch" in trigger
    assert "schedule" not in trigger
    assert refresh["concurrency"]["group"] == "form-update"
    assert refresh["permissions"]["contents"] == "write"

    jobs = refresh["jobs"]
    assert list(jobs) == ["refresh"]
    steps = jobs["refresh"]["steps"]
    commands = "\n".join(str(step.get("run", "")) for step in steps)
    assert "scripts/sync_config.py" in commands
    assert "scripts/run_form_updater.py --mode write --origin forced" in commands
    sync_index = next(
        index for index, step in enumerate(steps) if "sync_config.py" in str(step.get("run", ""))
    )
    write_index = next(
        index
        for index, step in enumerate(steps)
        if "run_form_updater.py" in str(step.get("run", ""))
    )
    assert sync_index < write_index


def test_apps_script_clock_contract() -> None:
    path = REPO_ROOT / "apps_script" / "Code.gs"
    text = path.read_text(encoding="utf-8")
    for name in ("clockTick", "onSheetChange", "setupTriggers", "testDispatch"):
        assert f"function {name}(" in text
    for prop in (
        "GH_PAT",
        "SCHEDULER_DISPATCH_URL",
        "CONFIG_REFRESH_DISPATCH_URL",
        "APPSCRIPT_HEALTHCHECKS_URL",
        "SHEET_ID",
    ):
        assert prop in text
    assert "America/Bogota" in text
    assert "everyMinutes(5)" in text
    assert "config_refresh.yml" in text
    assert "_DISPATCHED_WINDOWS" in text
    assert "changeType" in text
    assert '"EDIT"' in text
    assert "github_pat_" not in text
    assert "ghp_" not in text


def test_scheduler_reads_both_ajustes_keys_and_has_catchup() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    scheduler = config["scheduler"]
    assert float(scheduler.get("catchup_hours", 0)) > 0
    assert (REPO_ROOT / scheduler.get("state_path", "config/scheduler_state.json")).exists()

    snapshot = (REPO_ROOT / "config" / "sheet_config.csv").read_text(encoding="utf-8")
    assert "horario_etl" in snapshot
    assert "horario_updater" in snapshot
