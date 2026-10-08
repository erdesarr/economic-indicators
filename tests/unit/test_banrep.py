"""Golden tests for the Banrep IBR adapter (nominal vs effective)."""

from __future__ import annotations

import pytest

from etl.models import AppConfig
from etl.sources.banrep import parse_banrep_payload
from tests.conftest import load_expected, load_json

OWNER_SNAPSHOT = "owner_snapshot_20261007.json"

# Owner-verified values captured from the real page on 2026-10-07.
OWNER_NOMINAL = {
    "ibr_overnight": 11.411,
    "ibr_1_mes": 11.495,
    "ibr_3_meses": 11.735,
    "ibr_6_meses": 12.033,
    "ibr_12_meses": 12.571,
}
OWNER_EFFECTIVE = {
    "ibr_overnight": 12.263,
    "ibr_1_mes": 12.292,
    "ibr_3_meses": 12.438,
    "ibr_6_meses": 12.573,
    "ibr_12_meses": 12.746,
}


def test_owner_snapshot_returns_exact_nominals(config: AppConfig) -> None:
    payload = load_json("banrep", OWNER_SNAPSHOT)
    for slug, expected in OWNER_NOMINAL.items():
        result = parse_banrep_payload(payload, config.by_slug(slug))
        assert result.ok, f"{slug}: {result.error}"
        assert result.value == pytest.approx(expected), slug


def test_owner_snapshot_value_published_is_effective(config: AppConfig) -> None:
    payload = load_json("banrep", OWNER_SNAPSHOT)
    for slug, expected in OWNER_EFFECTIVE.items():
        result = parse_banrep_payload(payload, config.by_slug(slug))
        assert result.value_published == pytest.approx(expected), slug


def test_effective_never_leaks_into_value(config: AppConfig) -> None:
    payload = load_json("banrep", OWNER_SNAPSHOT)
    for slug in OWNER_NOMINAL:
        result = parse_banrep_payload(payload, config.by_slug(slug))
        assert result.value != pytest.approx(OWNER_EFFECTIVE[slug]), slug
        assert result.value == pytest.approx(OWNER_NOMINAL[slug]), slug


def test_live_golden_fixture_roundtrip(config: AppConfig) -> None:
    payload = load_json("banrep", "series.json")
    expected = load_expected("banrep")["indicators"]
    for slug, body in expected.items():
        result = parse_banrep_payload(payload, config.by_slug(slug))
        assert result.ok, f"{slug}: {result.error}"
        assert result.value == pytest.approx(body["value"]), slug
        assert result.value_published == pytest.approx(body["value_published"]), slug
        assert result.source_date is not None


def test_missing_series_is_controlled_error(config: AppConfig) -> None:
    result = parse_banrep_payload([], config.by_slug("ibr_overnight"))
    assert not result.ok
    assert "not in payload" in (result.error or "")


def test_missing_source_series_mapping(config: AppConfig) -> None:
    from etl.models import IndicatorConfig

    indicator = IndicatorConfig(
        slug="ibr_overnight",
        source="banrep",
        url="http://example.invalid",
        visible_label="Overnight",
        unit="%",
        unit_published="%",
        plausible_range=(0.0, 20.0),
        method="api",
        number_format="json-dot",
        retries=1,
    )
    result = parse_banrep_payload([], indicator)
    assert not result.ok
    assert "source_series" in (result.error or "")
