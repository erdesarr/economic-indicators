"""Formatter tests: Spanish CO numbers, literals and variation blocks."""

from __future__ import annotations

import pytest

from form_updater.formatter import format_value, numero_es_co
from form_updater.sheet import IndicatorRow


def row(
    *,
    prefijo: str = "",
    sufijo: str = "",
    mostrar_variacion: bool = True,
    symbol_up: str = "+",
    symbol_down: str = "-",
) -> IndicatorRow:
    return IndicatorRow(
        indicador="uvr",
        prefijo=prefijo,
        sufijo=sufijo,
        mostrar_variacion=mostrar_variacion,
        symbol_up=symbol_up,
        symbol_down=symbol_down,
        fallback="",
        id_indicador=5,
        activo=True,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3238.88, "3.238,88"),
        (3.58, "3,58"),
        (77.205, "77,205"),
        (5.0113, "5,0113"),
        (419.29, "419,29"),
        (419.0, "419"),
        (1234567.8912, "1.234.567,8912"),
        (0.5, "0,5"),
    ],
)
def test_numero_es_co_value(value: float, expected: str) -> None:
    assert numero_es_co(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(2.0, "2,00"), (0.0351, "0,04"), (10.19, "10,19"), (1234.5, "1.234,50")],
)
def test_numero_es_co_two_decimals(value: float, expected: str) -> None:
    assert numero_es_co(value, 2) == expected


def test_empty_prefix_and_suffix() -> None:
    assert format_value(row(), 3238.88, 0.71) == "3.238,88 +0,71%"


def test_literal_prefix_and_suffix_preserve_spaces() -> None:
    assert format_value(row(prefijo="US$ ", sufijo=" /lb"), 3.58, -2.86) == "US$ 3,58 /lb -2,86%"
    assert format_value(row(prefijo="$ "), 419.29, None) == "$ 419,29"


def test_symbol_pairs() -> None:
    assert format_value(row(symbol_up="^", symbol_down="v"), 10.0, 1.5) == "10 ^1,50%"
    assert format_value(row(symbol_up="^", symbol_down="v"), 10.0, -1.5) == "10 v1,50%"
    assert format_value(row(symbol_up="↑", symbol_down="↓"), 10.0, 0.0) == "10 ↑0,00%"


def test_mostrar_variacion_false_hides_variation() -> None:
    assert format_value(row(mostrar_variacion=False), 10.19, 2.5) == "10,19"


def test_variation_none_hides_block() -> None:
    assert format_value(row(), 10.19, None) == "10,19"


def test_always_one_space_before_variation_with_suffix() -> None:
    assert format_value(row(sufijo=" %"), 10.19, -0.19) == "10,19 % -0,19%"


def test_variation_uses_source_value() -> None:
    # The caller passes the variation of the variable whose value is used
    # (e.g. fallback): the formatter must not infer it from anything else.
    assert format_value(row(prefijo="$ "), 3607.58, 5.5) == "$ 3.607,58 +5,50%"
