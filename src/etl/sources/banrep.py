"""Banco de la República (suameca) REST adapter.

Official backend endpoint discovered from the SPA XHR traffic:
``.../consultaInformacionSerie?idSerie=...`` returns the full series history.

Owner decision: ``value`` is the NOMINAL series exactly as published; the
EFFECTIVE series is stored only as ``value_published`` (% E.A.) for reference.
No conversion is ever applied between the two.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from etl.http import FetchError, HttpClient
from etl.models import IndicatorConfig, Settings, SourceConfig, SourceResult
from etl.normalize import parse_date


class BanrepAdapter:
    name = "banrep"

    def fetch(
        self,
        indicators: Sequence[IndicatorConfig],
        source: SourceConfig,
        settings: Settings,
        client: HttpClient,
    ) -> dict[str, SourceResult]:
        results: dict[str, SourceResult] = {}
        by_url: dict[str, list[IndicatorConfig]] = {}
        for indicator in indicators:
            by_url.setdefault(indicator.url, []).append(indicator)

        for url, group in by_url.items():
            try:
                payload = client.get_json(url, retries=source.retries)
            except FetchError as exc:
                for indicator in group:
                    results[indicator.slug] = SourceResult(slug=indicator.slug, error=str(exc))
                continue
            for indicator in group:
                results[indicator.slug] = parse_banrep_payload(payload, indicator)
            client.sleep_politeness()
        return results


def _series_by_id(payload: Any) -> dict[int, dict[str, Any]]:
    if not isinstance(payload, list):
        return {}
    series: dict[int, dict[str, Any]] = {}
    for item in payload:
        if isinstance(item, dict) and "id" in item:
            series[int(item["id"])] = item
    return series


def _latest_point(series: dict[str, Any]) -> tuple[float, str] | None:
    data = series.get("data")
    if isinstance(data, list):
        for point in reversed(data):
            if isinstance(point, list) and len(point) == 2 and point[1] is not None:
                return float(point[1]), str(series.get("fecha", ""))
    value = series.get("valor")
    if value is not None:
        try:
            return float(value), str(series.get("fecha", ""))
        except (TypeError, ValueError):
            return None
    return None


def parse_banrep_payload(payload: Any, indicator: IndicatorConfig) -> SourceResult:
    mapping = indicator.source_series
    if mapping is None:
        return SourceResult(slug=indicator.slug, error="missing source_series mapping")
    series = _series_by_id(payload)
    nominal = series.get(mapping.nominal)
    if nominal is None:
        return SourceResult(
            slug=indicator.slug, error=f"nominal series {mapping.nominal} not in payload"
        )
    nominal_point = _latest_point(nominal)
    if nominal_point is None:
        return SourceResult(
            slug=indicator.slug, error=f"nominal series {mapping.nominal} has no data"
        )
    value, fecha = nominal_point

    value_published: float | None = None
    if mapping.published is not None:
        published = series.get(mapping.published)
        if published is not None:
            published_point = _latest_point(published)
            if published_point is not None:
                value_published = published_point[0]

    return SourceResult(
        slug=indicator.slug,
        value=value,
        value_published=value_published,
        unit_as_published=str(nominal.get("unidadCorta", "%")),
        raw_label=str(nominal.get("nombre", indicator.visible_label)),
        raw_text=f"{fecha} {nominal.get('valor')}",
        source_date=parse_date(fecha),
    )
