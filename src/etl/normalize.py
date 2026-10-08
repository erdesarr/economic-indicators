"""Numeric normalization per source format.

``value`` must always be a REAL number: no symbols, no units, no spaces.
The original published string is preserved separately as ``raw_text`` and the
unit text as ``unit_as_published``.

Supported formats:
  * ``es-CO``: dot = thousands, comma = decimal (La República).
  * ``es-ES``: dot = thousands, comma = decimal (investing.com es).
  * ``json-dot``: plain JSON float ("5.0113").
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

NUMBER_TOKEN_RE = re.compile(r"[-+]?\d[\d.,\u00a0]*")

# A price-like element contains nothing but an optional currency/percent marker
# and a number. Used to pick values without falling for dates or prose.
STRICT_PRICE_RE = re.compile(
    r"^\s*[-+]?\s*(?:US\$|\$|R\$|€)?\s*[-+]?\d[\d.,\u00a0]*\s*(?:%|COP|USD)?\s*$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{4}\s*$")

_GROUPED_INT_RE = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
_PLAIN_INT_RE = re.compile(r"^\d+$")
_JSON_NUMBER_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?$")


class NumberParseError(ValueError):
    """Raised when a published string cannot be normalized to a number."""


def normalize_text(text: str) -> str:
    """Fold accents/case/whitespace for label matching (never for values)."""

    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_accents.upper().split())


def extract_number_token(text: str) -> str | None:
    """Return the first numeric token in a string, or None."""

    match = NUMBER_TOKEN_RE.search(text.replace("\u00a0", " "))
    return match.group(0) if match else None


def _parse_es(token: str) -> float:
    token = token.replace("\u00a0", "").strip()
    sign = -1.0 if token.startswith("-") else 1.0
    token = token.lstrip("+-")
    if not token:
        raise NumberParseError("empty token")
    if "," in token:
        if token.count(",") != 1:
            raise NumberParseError(f"malformed decimal separator: {token!r}")
        integer_part, decimal_part = token.split(",")
        if not decimal_part.isdigit():
            raise NumberParseError(f"malformed decimal part: {token!r}")
        if "." in integer_part:
            if not _GROUPED_INT_RE.match(integer_part):
                raise NumberParseError(f"malformed thousands grouping: {token!r}")
            integer_part = integer_part.replace(".", "")
        elif not _PLAIN_INT_RE.match(integer_part):
            raise NumberParseError(f"malformed integer part: {token!r}")
        return sign * float(f"{integer_part}.{decimal_part}")
    if _PLAIN_INT_RE.match(token):
        return sign * float(token)
    if _GROUPED_INT_RE.match(token):
        return sign * float(token.replace(".", ""))
    raise NumberParseError(f"malformed es number: {token!r}")


def _parse_json(token: str) -> float:
    token = token.strip()
    if not _JSON_NUMBER_RE.match(token):
        raise NumberParseError(f"malformed json number: {token!r}")
    return float(token)


def normalize_number(text: str, number_format: str) -> float:
    """Normalize a published string into a float.

    Raises:
        NumberParseError: if the text does not contain a parseable number or
            the format is unknown. Callers must treat this as a controlled
            failure (forward-fill + alert), never persist a string.
    """

    token = extract_number_token(text)
    if token is None:
        raise NumberParseError(f"no numeric token in {text!r}")
    if number_format in {"es-CO", "es-ES"}:
        return _parse_es(token)
    if number_format == "json-dot":
        return _parse_json(token)
    raise NumberParseError(f"unknown number format: {number_format!r}")


def try_normalize_number(text: str, number_format: str) -> float | None:
    """Like :func:`normalize_number` but returns None instead of raising."""

    try:
        return normalize_number(text, number_format)
    except NumberParseError:
        return None


def is_price_like(text: str) -> bool:
    """True when the whole element text is just a price (no dates/prose)."""

    stripped = text.strip()
    if not stripped or DATE_RE.match(stripped):
        return False
    return bool(STRICT_PRICE_RE.match(stripped))


def parse_date(text: str) -> date | None:
    """Best-effort dd/mm/yyyy parsing used for ``source_date``."""

    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None
