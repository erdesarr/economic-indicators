"""La República HTML extractor.

Strategy (business-approved): anchor on the normalized VISIBLE label text and
take the nearest price-like value. No positional selectors, no framework
classes. Missing label (e.g. "DÓLAR OFICIAL MAÑANA" before its ~15:00
publication) is a controlled error, never a crash.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from etl.http import FetchError, HttpClient
from etl.models import IndicatorConfig, Settings, SourceConfig, SourceResult
from etl.normalize import is_price_like, normalize_number, normalize_text, parse_date

MAX_ANCESTOR_DEPTH = 8
UNIT_RE = re.compile(r"^[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ /.]{2,40}$")


class LaRepublicaAdapter:
    """Handles the main indicators page, DTF detail and IGBC detail pages."""

    def __init__(self, name: str) -> None:
        self.name = name

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
                html = client.get_text(url, retries=source.retries)
            except FetchError as exc:
                for indicator in group:
                    results[indicator.slug] = SourceResult(slug=indicator.slug, error=str(exc))
                continue
            for indicator in group:
                results[indicator.slug] = parse_indicator(html, indicator)
            client.sleep_politeness()
        return results


def _ancestors(node: NavigableString, max_depth: int) -> list[tuple[int, Tag]]:
    chain: list[tuple[int, Tag]] = []
    current: Tag | None = node.parent
    depth = 1
    while current is not None and depth <= max_depth:
        chain.append((depth, current))
        current = current.parent
        depth += 1
    return chain


def _descendants_after(ancestor: Tag, label_node: NavigableString) -> list[Tag]:
    found = False
    after: list[Tag] = []
    for child in ancestor.descendants:
        if child is label_node:
            found = True
            continue
        if found and isinstance(child, Tag):
            after.append(child)
    return after


def _first_price(ancestor: Tag, label_node: NavigableString) -> tuple[Tag, str] | None:
    for tag in _descendants_after(ancestor, label_node):
        text = tag.get_text(" ", strip=True)
        if is_price_like(text):
            return tag, text
    return None


def _unit_for(price_tag: Tag, label_node: NavigableString) -> str:
    text = price_tag.get_text(" ", strip=True)
    if "%" in text:
        return "%"
    node: Tag | None = price_tag
    for _ in range(4):
        if node is None:
            break
        for child in node.descendants:
            if isinstance(child, NavigableString) and child is not label_node:
                candidate = child.strip()
                if "/" in candidate and UNIT_RE.match(candidate):
                    return candidate
        node = node.parent
    return ""


def _source_date_for(price_tag: Tag, label_node: NavigableString) -> date | None:
    node: Tag | None = price_tag
    for _ in range(4):
        if node is None:
            break
        for child in _descendants_after(node, label_node):
            text = child.get_text(" ", strip=True)
            parsed = parse_date(text)
            if parsed is not None:
                return parsed
        node = node.parent
    return None


def parse_indicator(html: str, indicator: IndicatorConfig) -> SourceResult:
    """Parse a single indicator from a La República HTML page."""

    soup = BeautifulSoup(html, "lxml")
    target = normalize_text(indicator.visible_label)
    matches = [node for node in soup.find_all(string=True) if normalize_text(str(node)) == target]
    if not matches:
        return SourceResult(
            slug=indicator.slug,
            error=f"label not found in page: {indicator.visible_label!r}",
        )

    candidates: list[tuple[int, Tag, str, Tag, NavigableString]] = []
    for node in matches:
        for depth, ancestor in _ancestors(node, MAX_ANCESTOR_DEPTH):
            found = _first_price(ancestor, node)
            if found is not None:
                price_tag, price_text = found
                candidates.append((depth, price_tag, price_text, ancestor, node))
                break

    if not candidates:
        return SourceResult(
            slug=indicator.slug,
            error=f"no price-like value near label: {indicator.visible_label!r}",
        )

    candidates.sort(key=lambda item: item[0])
    best_depth = candidates[0][0]
    best = [c for c in candidates if c[0] == best_depth]
    distinct_texts = {c[2] for c in best}
    if len(distinct_texts) > 1:
        return SourceResult(
            slug=indicator.slug,
            error=f"ambiguous label match: {indicator.visible_label!r} -> {sorted(distinct_texts)}",
        )

    _, price_tag, price_text, _ancestor, label_node = best[0]
    try:
        value = normalize_number(price_text, indicator.number_format)
    except Exception as exc:  # noqa: BLE001 - controlled parse failure
        return SourceResult(
            slug=indicator.slug,
            error=f"cannot normalize value {price_text!r}: {exc}",
        )

    source_date = _source_date_for(price_tag, label_node)
    return SourceResult(
        slug=indicator.slug,
        value=value,
        unit_as_published=_unit_for(price_tag, label_node),
        raw_label=str(label_node).strip(),
        raw_text=price_text,
        source_date=source_date,
    )
