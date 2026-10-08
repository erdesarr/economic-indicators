"""Golden tests for the La República label-anchored HTML parser."""

from __future__ import annotations

import pytest

from etl.models import AppConfig
from etl.sources.larepublica import parse_indicator
from tests.conftest import GOLDEN, load_expected, load_text

PAGE_FOR_SOURCE = {
    "larepublica_main": "main.html",
    "larepublica_dtf": "dtf.html",
    "larepublica_igbc": "igbc.html",
}


def _golden_indicators(config: AppConfig) -> list[tuple[str, object]]:
    expected = load_expected("larepublica")
    return [
        (indicator.slug, indicator)
        for indicator in config.indicators
        if indicator.slug in expected["indicators"]
    ]


def test_golden_values_match(config: AppConfig) -> None:
    expected = load_expected("larepublica")
    for slug, indicator in _golden_indicators(config):
        html = load_text("larepublica", PAGE_FOR_SOURCE[indicator.source])
        result = parse_indicator(html, indicator)
        body = expected["indicators"][slug]
        assert result.ok, f"{slug}: {result.error}"
        assert result.value == pytest.approx(body["value"]), slug
        assert result.unit_as_published == body["unit_as_published"], slug
        assert result.raw_text == body["raw_text"], slug
        assert result.raw_label == body["label"], slug
        expected_date = body["source_date"]
        assert (result.source_date.isoformat() if result.source_date else None) == expected_date


def test_manana_block_missing_is_controlled_error(config: AppConfig) -> None:
    """Guard: page without the 'DÓLAR OFICIAL MAÑANA' block must not crash.

    Fixture captured before the ~15:00 publication (holidays/delays produce the
    same shape at run time).
    """

    indicator = config.by_slug("dolar_oficial_manana")
    html = load_text("larepublica", "main_without_manana.html")
    assert "DÓLAR OFICIAL MAÑANA" not in html
    result = parse_indicator(html, indicator)
    assert not result.ok
    assert result.value is None
    assert "label not found" in (result.error or "")


def test_manana_block_present_is_parsed(config: AppConfig) -> None:
    """Synthetic golden fixture: twin block built on the real trm-today markup.

    `main_with_manana_synthetic.html` = captured main page (without the block)
    + an exact `trm-today` twin (class `trm-tomorrow`, same nesting/classes).
    This keeps the pending_live adapter tested offline.
    """

    indicator = config.by_slug("dolar_oficial_manana")
    html = load_text("larepublica", "main_with_manana_synthetic.html")
    assert 'class="trm-tomorrow"' in html
    result = parse_indicator(html, indicator)
    assert result.ok, result.error
    assert result.value == pytest.approx(3250.10)
    assert result.raw_text == "$ 3.250,10"
    assert result.source_date is not None
    assert result.source_date.isoformat() == "2026-10-09"
    assert result.raw_label == "DÓLAR OFICIAL MAÑANA"


def test_ambiguous_label_is_error(config: AppConfig) -> None:
    indicator = config.by_slug("uvr")
    html = load_text("larepublica", "main.html")
    injected = html.replace(
        "</body>",
        (
            '<div class="col-4"><div class="cardI anconIndicador">'
            '<a href="#"><h3 class="col-10 pl-0 nameIndicator">UVR</h3></a>'
            '<div class="d-flex align-items-baseline">'
            '<span class="priceIndicator">$ 999,99</span></div></div></div></body>'
        ),
    )
    result = parse_indicator(injected, indicator)
    assert not result.ok
    assert "ambiguous" in (result.error or "")


def test_dtf_uses_detail_page_not_nav(config: AppConfig) -> None:
    indicator = config.by_slug("dtf")
    html = load_text("larepublica", "dtf.html")
    result = parse_indicator(html, indicator)
    assert result.ok
    assert result.value == pytest.approx(10.19)
    assert result.unit_as_published == "%"


def test_igbc_detail_page_value(config: AppConfig) -> None:
    indicator = config.by_slug("msci_colcap")
    html = load_text("larepublica", "igbc.html")
    result = parse_indicator(html, indicator)
    assert result.ok
    assert result.value is not None
    assert 800 <= result.value <= 3000


def test_fixture_files_exist() -> None:
    for name in ("main.html", "dtf.html", "igbc.html", "expected.json"):
        assert (GOLDEN / "larepublica" / name).exists(), name


def test_expected_json_has_no_string_values() -> None:
    expected = load_expected("larepublica")
    for slug, body in expected["indicators"].items():
        assert isinstance(body["value"], (int, float)), f"{slug} value is not numeric"
