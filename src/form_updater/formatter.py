"""Value formatting for the web form (Spanish Colombia number format).

The formatter only formats a value plus variation; it never resolves which
source (primary/fallback/forward_fill) provides them (see ``resolution.py``).

Approved semantics::

    valor_final = prefijo + numero_es_CO(value) + sufijo
    if mostrar_variacion and variation_pct is not None:
        simbolo = symbol_up if variation_pct >= 0 else symbol_down
        valor_final += " " + simbolo + numero_es_CO(abs(variation_pct), 2) + "%"

``prefijo``/``sufijo`` are literal (spaces preserved). ``numero_es_CO`` uses
dot thousands and comma decimals; values keep up to 4 decimals, variations use
exactly 2.
"""

from __future__ import annotations

from form_updater.sheet import IndicatorRow

VALUE_MAX_DECIMALS = 4
VARIATION_DECIMALS = 2


def numero_es_co(value: float, decimals: int | None = None) -> str:
    """Format a number with dot thousands and comma decimals.

    With ``decimals=None`` up to 4 decimals are kept, trailing zeros trimmed.
    With ``decimals=N`` exactly N decimals are emitted (used for variations).
    """

    if decimals is None:
        text = f"{value:.{VALUE_MAX_DECIMALS}f}"
        text = text.rstrip("0").rstrip(".")
    else:
        text = f"{value:.{decimals}f}"
    sign = ""
    if text.startswith("-"):
        sign = "-"
        text = text[1:]
    if "." in text:
        integer, fraction = text.split(".", 1)
    else:
        integer, fraction = text, ""
    grouped = f"{int(integer):,}".replace(",", ".")
    if fraction:
        return f"{sign}{grouped},{fraction}"
    return f"{sign}{grouped}"


def format_value(row: IndicatorRow, value: float, variation_pct: float | None) -> str:
    """Compose the final form text for a resolved value and its variation."""

    text = f"{row.prefijo}{numero_es_co(value)}{row.sufijo}"
    if row.mostrar_variacion and variation_pct is not None:
        symbol = row.symbol_up if variation_pct >= 0 else row.symbol_down
        text += f" {symbol}{numero_es_co(abs(variation_pct), VARIATION_DECIMALS)}%"
    return text
