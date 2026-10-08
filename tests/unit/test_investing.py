"""Golden tests for the investing.com browser parser (offline HTML)."""

from __future__ import annotations

import pytest

from etl.models import AppConfig
from etl.sources.investing import _current_date, parse_investing_html
from tests.conftest import load_expected, load_text


def test_investing_golden_roundtrip(config: AppConfig) -> None:
    expected = load_expected("investing")["indicators"]
    for slug, body in expected.items():
        html = load_text("investing", f"{slug}.html")
        result = parse_investing_html(html, config.by_slug(slug))
        assert result.ok, f"{slug}: {result.error}"
        assert result.value == pytest.approx(body["value"]), slug
        assert result.raw_text == body["raw_text"], slug
        assert result.raw_label == body["label"], slug
        assert result.unit_as_published.startswith("Valores en"), slug
        assert result.source_date is not None


def test_investing_raw_label_contains_contract_code(config: AppConfig) -> None:
    for slug in ("gas_ttf_nl", "gas_henry_micro", "lng_jkm"):
        indicator = config.by_slug(slug)
        html = load_text("investing", f"{slug}.html")
        result = parse_investing_html(html, indicator)
        assert f"({indicator.visible_label})" in result.raw_label
        assert result.raw_label != indicator.visible_label  # full real label kept


def test_investing_missing_price_is_error(config: AppConfig) -> None:
    result = parse_investing_html("<html><body>empty</body></html>", config.by_slug("gas_ttf_nl"))
    assert not result.ok
    assert "price element not found" in (result.error or "")


def test_current_date_extraction() -> None:
    html = '<meta content="&quot;{\\&quot;CURRENT_DATE\\&quot;:\\&quot;08.10.2026\\&quot;}" />'
    assert _current_date(html) is not None
    assert _current_date(html).isoformat() == "2026-10-08"  # type: ignore[union-attr]
    assert _current_date("<html></html>") is None
