"""Unit tests for the numeric normalizer per source format."""

from __future__ import annotations

import pytest

from etl.normalize import (
    NumberParseError,
    extract_number_token,
    is_price_like,
    normalize_number,
    normalize_text,
    parse_date,
    try_normalize_number,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4.123,45", 4123.45),
        ("US$ 3,75", 3.75),
        ("12,259 % E.A.", 12.259),
        ("$ 3.238,88", 3238.88),
        ("US$ 0,72", 0.72),
        ("419", 419.0),
        ("1.234", 1234.0),
    ],
)
def test_es_co_format(text: str, expected: float) -> None:
    assert normalize_number(text, "es-CO") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5.4321", 5.4321),
        ("5.0113", 5.0113),
        ("-0.875", -0.875),
        ("11.407", 11.407),
    ],
)
def test_json_dot_format(text: str, expected: float) -> None:
    assert normalize_number(text, "json-dot") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("77,205", 77.205),
        ("3,143", 3.143),
        ("1.234,56", 1234.56),
        ("25,875", 25.875),
    ],
)
def test_es_es_format(text: str, expected: float) -> None:
    assert normalize_number(text, "es-ES") == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "sin número", "1,2,3", "1.23.4,5", "abc"])
def test_malformed_raises(text: str) -> None:
    with pytest.raises(NumberParseError):
        normalize_number(text, "es-CO")


def test_unknown_format_raises() -> None:
    with pytest.raises(NumberParseError):
        normalize_number("1,5", "fr-FR")


def test_try_normalize_returns_none() -> None:
    assert try_normalize_number("n/a", "es-CO") is None
    assert try_normalize_number("4.123,45", "es-CO") == pytest.approx(4123.45)


def test_extract_number_token() -> None:
    assert extract_number_token("US$ 101,13") == "101,13"
    assert extract_number_token("sin números") is None


def test_is_price_like() -> None:
    assert is_price_like("$ 3.238,88")
    assert is_price_like("US$ 3,58")
    assert is_price_like("10,19%")
    assert not is_price_like("07/10/2026")
    assert is_price_like("+$ 22,87")  # variations are price-like too; position wins
    assert not is_price_like("DÓLARES / BARRIL")


def test_normalize_text_folds_accents_and_case() -> None:
    assert normalize_text("DÓLAR OFICIAL MAÑANA") == "DOLAR OFICIAL MANANA"
    assert normalize_text("  café   colombian  ") == "CAFE COLOMBIAN"


def test_parse_date() -> None:
    assert parse_date("Fecha 07/10/2026") is not None
    assert parse_date("Periodo: 05/10/2026 - 11/10/2026").isoformat() == "2026-10-05"
    assert parse_date("sin fecha") is None
    assert parse_date("31/02/2026") is None
