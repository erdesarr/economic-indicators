"""Shared pytest fixtures: declarative config and offline golden data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from etl.config import load_config
from etl.models import AppConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "golden"
CONFIG_PATH = REPO_ROOT / "config" / "indicators.yaml"


@pytest.fixture(scope="session")
def config() -> AppConfig:
    return load_config(CONFIG_PATH)


def load_expected(source: str) -> dict[str, Any]:
    return json.loads((GOLDEN / source / "expected.json").read_text(encoding="utf-8"))


def load_text(source: str, name: str) -> str:
    return (GOLDEN / source / name).read_text(encoding="utf-8", errors="replace")


def load_json(source: str, name: str) -> Any:
    return json.loads((GOLDEN / source / name).read_text(encoding="utf-8"))


def live_values(config: AppConfig) -> dict[str, float]:
    """Expected live values per slug from the approved golden fixtures."""

    values: dict[str, float] = {}
    for source in ("larepublica", "banrep", "bcb", "investing"):
        expected_path = GOLDEN / source / "expected.json"
        if not expected_path.exists():
            continue
        data = json.loads(expected_path.read_text(encoding="utf-8"))
        for slug, body in data["indicators"].items():
            if isinstance(body.get("value"), (int, float)):
                values[slug] = float(body["value"])
    for indicator in config.indicators:
        midpoint = sum(indicator.plausible_range) / 2
        values.setdefault(indicator.slug, midpoint)
    return values
