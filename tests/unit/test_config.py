"""Configuration loading tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from etl.config import ConfigError, load_config
from etl.models import AppConfig

REQUIRED_SLUGS = {
    "dolar_oficial_hoy",
    "dolar_oficial_manana",
    "euro",
    "cafe_colombian_milds",
    "uvr",
    "petroleo_brent",
    "petroleo_wti",
    "dtf",
    "msci_colcap",
    "ibr_overnight",
    "ibr_1_mes",
    "ibr_3_meses",
    "ibr_6_meses",
    "ibr_12_meses",
    "usd_brl_compra",
    "gas_ttf_nl",
    "gas_henry_micro",
    "lng_jkm",
}


def test_all_18_indicators_present(config: AppConfig) -> None:
    slugs = {indicator.slug for indicator in config.indicators}
    assert slugs == REQUIRED_SLUGS
    assert len(config.indicators) == 18


def test_ibr_series_mapping(config: AppConfig) -> None:
    expected = {
        "ibr_overnight": (241, 15324),
        "ibr_1_mes": (242, 15325),
        "ibr_3_meses": (243, 15326),
        "ibr_6_meses": (16560, 16561),
        "ibr_12_meses": (16562, 16563),
    }
    for slug, (nominal, published) in expected.items():
        indicator = config.by_slug(slug)
        assert indicator.source_series is not None
        assert indicator.source_series.nominal == nominal
        assert indicator.source_series.published == published
        assert indicator.unit == "% nominal anual"
        assert indicator.unit_published == "% E.A."


def test_number_format_per_source(config: AppConfig) -> None:
    assert config.sources["larepublica_main"].number_format == "es-CO"
    assert config.sources["investing"].number_format == "es-ES"
    assert config.sources["banrep"].number_format == "json-dot"
    assert config.sources["bcb"].number_format == "json-dot"


def test_verified_live_flag(config: AppConfig) -> None:
    assert config.by_slug("dolar_oficial_manana").verification == "verified_live"
    assert config.by_slug("dolar_oficial_hoy").verification is None


def test_unknown_source_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
settings:
  timezone: America/Bogota
  history_days: 31
  database: data/x.db
  max_gap_days: 7
  politeness_seconds: 1.0
  request_timeout: 10
  artifacts_dir: artifacts
  user_agents: ["ua"]
sources:
  only:
    method: html
    number_format: es-CO
    retries: 1
indicators:
  - slug: x
    source: nope
    url: http://x
    visible_label: X
    unit: COP
    plausible_range: [1, 2]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_invalid_range_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad2.yaml"
    path.write_text(
        """
settings:
  timezone: America/Bogota
  history_days: 31
  database: data/x.db
  max_gap_days: 7
  politeness_seconds: 1.0
  request_timeout: 10
  artifacts_dir: artifacts
  user_agents: ["ua"]
sources:
  only:
    method: html
    number_format: es-CO
    retries: 1
indicators:
  - slug: x
    source: only
    url: http://x
    visible_label: X
    unit: COP
    plausible_range: [5, 1]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_duplicate_slug_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad3.yaml"
    path.write_text(
        """
settings:
  timezone: America/Bogota
  history_days: 31
  database: data/x.db
  max_gap_days: 7
  politeness_seconds: 1.0
  request_timeout: 10
  artifacts_dir: artifacts
  user_agents: ["ua"]
sources:
  only:
    method: html
    number_format: es-CO
    retries: 1
indicators:
  - slug: x
    source: only
    url: http://x
    visible_label: X
    unit: COP
    plausible_range: [1, 2]
  - slug: x
    source: only
    url: http://x
    visible_label: X2
    unit: COP
    plausible_range: [1, 2]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(path)
