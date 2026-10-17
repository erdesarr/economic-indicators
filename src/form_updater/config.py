"""Configuration for the form updater / sheet sync."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/form_updater.yaml")


class FormUpdaterConfigError(ValueError):
    """Raised when config/form_updater.yaml is invalid."""


@dataclass(frozen=True)
class GvizConfig:
    timeout_seconds: float
    retries: int


@dataclass(frozen=True)
class FormUpdaterConfig:
    sheet_id: str
    indicators_tab: str
    settings_tab: str
    gviz: GvizConfig
    csv_path: Path
    workflow_etl: str
    workflow_updater: str
    settings_defaults: dict[str, float]

    def gviz_url(self, tab: str) -> str:
        return (
            f"https://docs.google.com/spreadsheets/d/{self.sheet_id}"
            f"/gviz/tq?tqx=out:csv&sheet={tab}"
        )


def _require(data: dict[str, Any], key: str, context: str) -> Any:
    if key not in data:
        raise FormUpdaterConfigError(f"missing key '{key}' in {context}")
    return data[key]


def load_form_updater_config(
    path: Path | str = DEFAULT_CONFIG_PATH,
) -> FormUpdaterConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FormUpdaterConfigError(f"configuration file not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FormUpdaterConfigError("configuration root must be a mapping")
    tabs = _require(raw, "tabs", "root")
    gviz = _require(raw, "gviz", "root")
    scheduler = _require(raw, "scheduler", "root")
    defaults = raw.get("settings_defaults", {})
    return FormUpdaterConfig(
        sheet_id=str(_require(raw, "sheet_id", "root")),
        indicators_tab=str(_require(tabs, "indicators", "tabs")),
        settings_tab=str(_require(tabs, "settings", "tabs")),
        gviz=GvizConfig(
            timeout_seconds=float(gviz.get("timeout_seconds", 10)),
            retries=int(gviz.get("retries", 3)),
        ),
        csv_path=Path(str(raw.get("csv_path", "config/sheet_config.csv"))),
        workflow_etl=str(_require(scheduler, "workflow_etl", "scheduler")),
        workflow_updater=str(_require(scheduler, "workflow_updater", "scheduler")),
        settings_defaults={str(k): float(v) for k, v in defaults.items()},
    )
