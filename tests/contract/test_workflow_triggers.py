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


def test_scheduler_cron_is_active() -> None:
    scheduler = yaml.safe_load((WORKFLOWS / "scheduler.yml").read_text(encoding="utf-8"))
    trigger = _on(scheduler)
    assert "schedule" in trigger
    assert any(item.get("cron") == "*/5 * * * *" for item in trigger["schedule"])


def test_scheduler_reads_both_ajustes_keys_and_has_catchup() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    scheduler = config["scheduler"]
    assert float(scheduler.get("catchup_hours", 0)) > 0
    assert (REPO_ROOT / scheduler.get("state_path", "config/scheduler_state.json")).exists()

    snapshot = (REPO_ROOT / "config" / "sheet_config.csv").read_text(encoding="utf-8")
    assert "horario_etl" in snapshot
    assert "horario_updater" in snapshot
