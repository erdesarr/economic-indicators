"""Golden tests for the BCB SGS adapter."""

from __future__ import annotations

import pytest

from etl.models import AppConfig
from etl.sources.bcb import parse_bcb_payload
from tests.conftest import load_expected, load_json


def test_bcb_golden_roundtrip(config: AppConfig) -> None:
    payload = load_json("bcb", "sgs_10813.json")
    expected = load_expected("bcb")["indicators"]
    indicator = config.by_slug("usd_brl_compra")
    result = parse_bcb_payload(payload, indicator)
    assert result.ok, result.error
    body = expected["usd_brl_compra"]
    assert result.value == pytest.approx(body["value"])
    assert result.unit_as_published == "R$"
    assert result.source_date is not None
    assert result.raw_text.endswith("5.0113")


def test_bcb_empty_payload(config: AppConfig) -> None:
    result = parse_bcb_payload([], config.by_slug("usd_brl_compra"))
    assert not result.ok


def test_bcb_malformed_payload(config: AppConfig) -> None:
    result = parse_bcb_payload([{"data": "08/10/2026"}], config.by_slug("usd_brl_compra"))
    assert not result.ok


def test_bcb_unparseable_value(config: AppConfig) -> None:
    result = parse_bcb_payload(
        [{"data": "08/10/2026", "valor": "n/d"}], config.by_slug("usd_brl_compra")
    )
    assert not result.ok
    assert "cannot normalize" in (result.error or "")
