"""Google Sheet (gviz CSV) sync: fetch, normalize, validate and snapshot.

Rules are business-approved:
  * headers are Spanish without accents; ``id_indicador`` is canonical and
    ``numero_control`` is accepted as a legacy alias;
  * prefix/suffix may be wrapped in literal backticks (visual space markers);
    one leading and one trailing backtick are stripped;
  * booleans are bilingual (true/verdadero/1/si/sí/x vs false/falso/0/no/empty);
  * ``simbolo_variacion`` is "UP|DOWN" (pipe separator, U+007C); a single token
    means up-token + "-" down with a warning; empty means "+|-";
  * a row is INVALID (skipped with a warning) when the slug is not in the ETL
    catalog or the id is not a positive integer. Unknown fallbacks are cleared
    with a warning. Unknown columns/keys produce warnings, never crashes;
  * missing settings keys fall back to the last committed snapshot, then to the
    bootstrap defaults, with a warning (the caller pings /fail).
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from croniter import croniter

logger = logging.getLogger(__name__)

RETRY_BASE_SECONDS = 1.0
BACKTICK = "`"
SYMBOL_SEPARATOR = "|"
DEFAULT_SYMBOL = ("+", "-")

TRUE_VALUES = {"true", "verdadero", "1", "si", "sí", "x"}
FALSE_VALUES = {"false", "falso", "0", "no", ""}

INDICATOR_HEADERS = (
    "indicador",
    "prefijo",
    "sufijo",
    "mostrar_variacion",
    "simbolo_variacion",
    "fallback",
    "id_indicador",
    "activo",
)
HEADER_ALIASES = {"numero_control": "id_indicador"}
KNOWN_SETTINGS = ("horario_etl", "horario_updater", "umbral_variacion_pct")

SNAPSHOT_COLUMNS = (
    "tab",
    "indicador",
    "prefijo",
    "sufijo",
    "mostrar_variacion",
    "simbolo_variacion",
    "fallback",
    "id_indicador",
    "activo",
    "clave",
    "valor",
)


class SheetError(RuntimeError):
    """Raised for unrecoverable sheet problems (fetch or required headers)."""


class PermanentSheetError(SheetError):
    """Raised for non-retryable client errors (HTTP 4xx)."""


@dataclass(frozen=True)
class IndicatorRow:
    indicador: str
    prefijo: str
    sufijo: str
    mostrar_variacion: bool
    symbol_up: str
    symbol_down: str
    fallback: str
    id_indicador: int
    activo: bool

    @property
    def symbol(self) -> str:
        return f"{self.symbol_up}{SYMBOL_SEPARATOR}{self.symbol_down}"


@dataclass
class ParsedIndicators:
    rows: list[IndicatorRow]
    warnings: list[str]
    unknown_columns: list[str]

    @property
    def active_rows(self) -> list[IndicatorRow]:
        return [row for row in self.rows if row.activo]

    def by_slug(self) -> dict[str, list[IndicatorRow]]:
        grouped: dict[str, list[IndicatorRow]] = {}
        for row in self.rows:
            grouped.setdefault(row.indicador, []).append(row)
        return grouped


@dataclass
class ParsedSettings:
    settings: dict[str, str]
    warnings: list[str]
    unknown_keys: list[str]


@dataclass
class FinalizedSettings:
    settings: dict[str, str]
    warnings: list[str]
    critical: list[str]


def fetch_tab(
    url: str,
    timeout: float = 10.0,
    retries: int = 3,
    client: httpx.Client | None = None,
) -> str:
    """Fetch one gviz CSV tab with retries and backoff."""

    owned = client is None
    http = client or httpx.Client()
    last_error: Exception | None = None
    try:
        for attempt in range(1, retries + 1):
            try:
                response = http.get(url, timeout=timeout, follow_redirects=True)
                if response.status_code >= 500:
                    raise SheetError(f"gviz server error {response.status_code} for {url}")
                if response.status_code >= 400:
                    raise PermanentSheetError(f"gviz client error {response.status_code} for {url}")
                return response.text
            except PermanentSheetError:
                raise
            except (httpx.HTTPError, SheetError) as exc:
                last_error = exc
                logger.warning(
                    "gviz fetch failed (attempt %s/%s) url=%s error=%s",
                    attempt,
                    retries,
                    url,
                    exc,
                )
                if attempt < retries:
                    time.sleep(RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
        raise SheetError(f"giving up on {url} after {retries} attempts: {last_error}")
    finally:
        if owned:
            http.close()


def strip_outer_backticks(value: str) -> str:
    """Strip one leading and one trailing literal backtick when present."""

    result = value
    if result.startswith(BACKTICK):
        result = result[1:]
    if result.endswith(BACKTICK):
        result = result[:-1]
    return result


def parse_bool(cell: str, context: str, warnings: list[str]) -> bool:
    token = cell.strip().lower()
    if token in TRUE_VALUES:
        return True
    if token in FALSE_VALUES:
        return False
    warnings.append(f"{context}: unrecognized boolean {cell!r}; using False")
    return False


def parse_symbol(cell: str, context: str, warnings: list[str]) -> tuple[str, str]:
    token = cell.strip()
    if not token:
        return DEFAULT_SYMBOL
    if SYMBOL_SEPARATOR in token:
        parts = token.split(SYMBOL_SEPARATOR)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), parts[1].strip()
        warnings.append(f"{context}: malformed pair {cell!r}; using default '+|-'")
        return DEFAULT_SYMBOL
    warnings.append(f"{context}: single token {cell!r}; up={token!r}, down='-'")
    return token, "-"


def parse_indicators(text: str, catalog: set[str]) -> ParsedIndicators:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise SheetError("empty indicators sheet") from exc

    normalized = [cell.strip().lower() for cell in header]
    mapped = [HEADER_ALIASES.get(name, name) for name in normalized]
    unknown_columns = [name for name in mapped if name not in INDICATOR_HEADERS]
    missing = [name for name in INDICATOR_HEADERS if name not in mapped]
    if missing:
        raise SheetError(f"missing required columns: {missing}")
    index = {name: mapped.index(name) for name in INDICATOR_HEADERS}

    rows: list[IndicatorRow] = []
    warnings: list[str] = []
    for line_number, raw in enumerate(reader, start=2):
        if not any(cell.strip() for cell in raw):
            continue
        cells = {
            name: (raw[index[name]] if index[name] < len(raw) else "") for name in INDICATOR_HEADERS
        }
        slug = cells["indicador"].strip()
        if slug not in catalog:
            warnings.append(f"row {line_number}: unknown indicator slug {slug!r}; row skipped")
            continue
        id_text = cells["id_indicador"].strip()
        try:
            id_value = int(id_text)
        except ValueError:
            warnings.append(f"row {line_number}: invalid id_indicador {id_text!r}; row skipped")
            continue
        if id_value <= 0:
            warnings.append(f"row {line_number}: id_indicador must be positive; row skipped")
            continue
        fallback = cells["fallback"].strip()
        if fallback and fallback not in catalog:
            warnings.append(f"row {line_number}: fallback {fallback!r} not in catalog; cleared")
            fallback = ""
        symbol_up, symbol_down = parse_symbol(
            cells["simbolo_variacion"], f"row {line_number} simbolo_variacion", warnings
        )
        rows.append(
            IndicatorRow(
                indicador=slug,
                prefijo=strip_outer_backticks(cells["prefijo"]),
                sufijo=strip_outer_backticks(cells["sufijo"]),
                mostrar_variacion=parse_bool(
                    cells["mostrar_variacion"], f"row {line_number} mostrar_variacion", warnings
                ),
                symbol_up=symbol_up,
                symbol_down=symbol_down,
                fallback=fallback,
                id_indicador=id_value,
                activo=parse_bool(cells["activo"], f"row {line_number} activo", warnings),
            )
        )
    return ParsedIndicators(rows=rows, warnings=warnings, unknown_columns=unknown_columns)


def parse_settings(text: str) -> ParsedSettings:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise SheetError("empty settings sheet") from exc
    normalized = [cell.strip().lower() for cell in header]
    if "clave" not in normalized or "valor" not in normalized:
        raise SheetError(f"settings sheet must have 'clave'/'valor' headers, got {header!r}")
    key_index = normalized.index("clave")
    value_index = normalized.index("valor")

    settings: dict[str, str] = {}
    warnings: list[str] = []
    unknown_keys: list[str] = []
    for raw in reader:
        if not any(cell.strip() for cell in raw):
            continue
        key = raw[key_index].strip() if key_index < len(raw) else ""
        value = raw[value_index].strip() if value_index < len(raw) else ""
        if not key:
            continue
        if key not in KNOWN_SETTINGS:
            unknown_keys.append(key)
            warnings.append(f"settings: unknown key {key!r}; ignored")
            continue
        settings[key] = value
    return ParsedSettings(settings=settings, warnings=warnings, unknown_keys=unknown_keys)


def validate_schedule(expression: str) -> str | None:
    """Return a human reason when the expression is unsupported, else None.

    Supported subset: ``m h * * *`` and ``m h * * dow``.
    """

    parts = expression.split()
    if len(parts) != 5:
        return f"expected 5 fields, got {len(parts)}"
    minute, hour, dom, month, dow = parts
    if not minute.isdigit() or not hour.isdigit():
        return "minute and hour must be numbers"
    if int(minute) > 59 or int(hour) > 23:
        return "minute/hour out of range"
    if dom != "*" or month != "*":
        return "day-of-month and month must be '*'"
    if dow != "*" and not re.fullmatch(r"[0-6](,[0-6])*", dow):
        return f"unsupported day-of-week {dow!r}"
    if not croniter.is_valid(expression):
        return "croniter rejected the expression"
    return None


def finalize_settings(
    parsed: dict[str, str],
    defaults: dict[str, float],
    previous: dict[str, str],
    warnings: list[str],
) -> FinalizedSettings:
    """Apply last-committed/bootstrap fallbacks and validate values.

    Returns the effective settings plus ``critical`` warnings (missing keys or
    invalid cron/threshold values) that must trigger a /fail Healthchecks ping.
    """

    result: dict[str, str] = {}
    critical: list[str] = []
    for key in KNOWN_SETTINGS:
        value = parsed.get(key, "").strip()
        if not value:
            fallback = previous.get(key) or defaults.get(key)
            if fallback is None:
                message = f"setting {key!r} missing and has no fallback; skipped"
                warnings.append(message)
                critical.append(message)
                continue
            source = "last committed" if previous.get(key) else "bootstrap default"
            message = f"setting {key!r} missing in sheet; using {source} {fallback!r}"
            warnings.append(message)
            critical.append(message)
            result[key] = str(fallback)
            continue
        result[key] = value

    for key in ("horario_etl", "horario_updater"):
        if key in result:
            reason = validate_schedule(result[key])
            if reason is not None:
                fallback = previous.get(key) or defaults.get(key)
                message = (
                    f"setting {key!r} invalid ({reason}); using {fallback!r}"
                    if fallback is not None
                    else f"setting {key!r} invalid ({reason}); ignored"
                )
                warnings.append(message)
                critical.append(message)
                if fallback is not None:
                    result[key] = str(fallback)
                else:
                    del result[key]

    if "umbral_variacion_pct" in result:
        raw_value = result["umbral_variacion_pct"]
        valid = True
        try:
            numeric = float(raw_value)
        except ValueError:
            valid = False
            numeric = 0.0
        if not valid or numeric <= 0:
            fallback = previous.get("umbral_variacion_pct") or defaults.get("umbral_variacion_pct")
            message = f"setting 'umbral_variacion_pct' invalid ({raw_value!r}); using {fallback!r}"
            warnings.append(message)
            critical.append(message)
            if fallback is None:
                del result["umbral_variacion_pct"]
            else:
                result["umbral_variacion_pct"] = str(fallback)
    return FinalizedSettings(settings=result, warnings=warnings, critical=critical)


def _split_symbol(text: str) -> tuple[str, str]:
    parts = (text or "").split(SYMBOL_SEPARATOR)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return DEFAULT_SYMBOL


def read_snapshot(path: Path) -> tuple[list[IndicatorRow], dict[str, str]]:
    """Read the last committed snapshot (for last-valid settings values)."""

    if not path.exists():
        return [], {}
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8")))
    rows: list[IndicatorRow] = []
    settings: dict[str, str] = {}
    for record in reader:
        tab = (record.get("tab") or "").strip()
        if record.get("clave"):
            settings[record["clave"].strip()] = (record.get("valor") or "").strip()
            continue
        if tab and record.get("indicador"):
            try:
                symbol_up, symbol_down = _split_symbol(record.get("simbolo_variacion") or "")
                rows.append(
                    IndicatorRow(
                        indicador=(record.get("indicador") or "").strip(),
                        prefijo=record.get("prefijo") or "",
                        sufijo=record.get("sufijo") or "",
                        mostrar_variacion=(record.get("mostrar_variacion") or "").lower() == "true",
                        symbol_up=symbol_up,
                        symbol_down=symbol_down,
                        fallback=(record.get("fallback") or "").strip(),
                        id_indicador=int(record.get("id_indicador") or 0),
                        activo=(record.get("activo") or "").lower() == "true",
                    )
                )
            except (TypeError, ValueError):
                continue
    return rows, settings


def snapshot_text(
    rows: list[IndicatorRow],
    settings: dict[str, str],
    indicators_tab: str,
    settings_tab: str,
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(SNAPSHOT_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                indicators_tab,
                row.indicador,
                row.prefijo,
                row.sufijo,
                str(row.mostrar_variacion).lower(),
                row.symbol,
                row.fallback,
                row.id_indicador,
                str(row.activo).lower(),
                "",
                "",
            ]
        )
    for key, value in settings.items():
        writer.writerow([settings_tab, "", "", "", "", "", "", "", "", key, value])
    return buffer.getvalue()


def write_snapshot(
    path: Path,
    rows: list[IndicatorRow],
    settings: dict[str, str],
    indicators_tab: str,
    settings_tab: str,
) -> bool:
    """Write the normalized snapshot; returns True when the content changed."""

    content = snapshot_text(rows, settings, indicators_tab, settings_tab)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def snapshot_as_dicts(path: Path) -> list[dict[str, Any]]:
    """Convenience for consumers (scheduler/updater): list of snapshot rows."""

    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8")))
    return [dict(record) for record in reader]
