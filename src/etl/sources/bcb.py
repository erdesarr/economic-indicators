"""Banco Central do Brasil (BCB) SGS adapter.

Series 10813 = "Taxa de câmbio - Livre - Dólar americano - Compra".
Verified equivalent to the visible value on bcb.gov.br ("Dólar EUA /
Compra (R$) / PTAX") and to the PTAX OData ``cotacaoCompra``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from etl.http import FetchError, HttpClient
from etl.models import IndicatorConfig, Settings, SourceConfig, SourceResult
from etl.normalize import normalize_number, parse_date

# BCB SGS rejects unbounded queries (HTTP 406); a short window is enough for
# the latest value and the day-over-day history kept in SQLite.
WINDOW_DAYS = 15


def build_query_url(url: str, today: date | None = None) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    reference = today or date.today()
    query.setdefault("dataInicial", (reference - timedelta(days=WINDOW_DAYS)).strftime("%d/%m/%Y"))
    query.setdefault("dataFinal", reference.strftime("%d/%m/%Y"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class BcbAdapter:
    name = "bcb"

    def fetch(
        self,
        indicators: Sequence[IndicatorConfig],
        source: SourceConfig,
        settings: Settings,
        client: HttpClient,
    ) -> dict[str, SourceResult]:
        results: dict[str, SourceResult] = {}
        for indicator in indicators:
            try:
                payload = client.get_json(
                    build_query_url(indicator.url), retries=source.retries
                )
            except FetchError as exc:
                results[indicator.slug] = SourceResult(slug=indicator.slug, error=str(exc))
                continue
            results[indicator.slug] = parse_bcb_payload(payload, indicator)
            client.sleep_politeness()
        return results


def parse_bcb_payload(payload: Any, indicator: IndicatorConfig) -> SourceResult:
    if not isinstance(payload, list) or not payload:
        return SourceResult(slug=indicator.slug, error="empty BCB SGS payload")
    last = payload[-1]
    if not isinstance(last, dict) or "valor" not in last:
        return SourceResult(slug=indicator.slug, error="malformed BCB SGS payload")
    raw_date = str(last.get("data", ""))
    raw_value = str(last["valor"])
    try:
        value = normalize_number(raw_value, indicator.number_format)
    except Exception as exc:  # noqa: BLE001 - controlled parse failure
        return SourceResult(
            slug=indicator.slug, error=f"cannot normalize BCB value {raw_value!r}: {exc}"
        )
    return SourceResult(
        slug=indicator.slug,
        value=value,
        unit_as_published="R$",
        raw_label="Compra (R$)",
        raw_text=f"{raw_date} (PTAX) {raw_value}",
        source_date=parse_date(raw_date),
    )
