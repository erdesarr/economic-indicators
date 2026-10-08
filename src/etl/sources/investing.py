"""investing.com browser adapter (only source that requires Playwright).

Cloudflare empirically blocks the bundled headless shell with a persistent 403
("Un momento…"). The full Chromium build (``channel="chromium"``) passes.
Strategy: realistic UA, warm-up on the homepage, courtesy delays, 3 retries
with exponential backoff. Persistent failure is a controlled error ->
forward-fill + alert; values are never invented.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from etl.http import HttpClient
from etl.models import IndicatorConfig, Settings, SourceConfig, SourceResult
from etl.normalize import normalize_number

logger = logging.getLogger(__name__)

CURRENT_DATE_RE = re.compile(r"CURRENT_DATE\\?[&quot;\"\\:]+([0-9]{2}\.[0-9]{2}\.[0-9]{4})")
BLOCK_TITLE_MARKER = "momento"


def parse_investing_html(html: str, indicator: IndicatorConfig) -> SourceResult:
    """Parse a saved investing.com page (offline, testable)."""

    code = indicator.visible_label
    soup = BeautifulSoup(html, "lxml")

    price_el = soup.select_one('[data-test="instrument-price-last"]')
    if price_el is None:
        return SourceResult(slug=indicator.slug, error=f"price element not found for {code}")
    raw_text = price_el.get_text(" ", strip=True)
    try:
        value = normalize_number(raw_text, indicator.number_format)
    except Exception as exc:  # noqa: BLE001 - controlled parse failure
        return SourceResult(
            slug=indicator.slug, error=f"cannot normalize investing value {raw_text!r}: {exc}"
        )

    raw_label = code
    for heading in soup.find_all("h1"):
        text = heading.get_text(" ", strip=True)
        if f"({code})" in text:
            raw_label = text
            break

    unit_el = soup.select_one('[data-test="currency-in-label"]')
    unit_as_published = ""
    if unit_el is not None:
        unit_as_published = " ".join(unit_el.get_text(" ", strip=True).split())

    return SourceResult(
        slug=indicator.slug,
        value=value,
        unit_as_published=unit_as_published,
        raw_label=raw_label,
        raw_text=raw_text,
        source_date=_current_date(html),
    )


def _current_date(html: str) -> date | None:
    match = CURRENT_DATE_RE.search(html)
    if not match:
        return None
    day, month, year = match.group(1).split(".")
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


class InvestingAdapter:
    name = "investing"

    def fetch(
        self,
        indicators: Sequence[IndicatorConfig],
        source: SourceConfig,
        settings: Settings,
        client: HttpClient,
    ) -> dict[str, SourceResult]:
        from playwright.sync_api import sync_playwright

        results: dict[str, SourceResult] = {}
        artifacts = Path(settings.artifacts_dir)
        artifacts.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser = self._launch(playwright)
            context = browser.new_context(
                user_agent=settings.user_agents[0],
                locale="es-ES",
                timezone_id="Europe/Madrid",
                viewport={"width": 1366, "height": 900},
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.new_page()
            try:
                if source.warmup_url:
                    page.goto(source.warmup_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(4000)
                for indicator in indicators:
                    results[indicator.slug] = self._fetch_one(
                        page, indicator, source, settings, artifacts
                    )
                    time.sleep(settings.politeness_seconds)
            finally:
                context.close()
                browser.close()
        return results

    @staticmethod
    def _launch(playwright: Any) -> Any:
        args = ["--disable-blink-features=AutomationControlled"]
        try:
            return playwright.chromium.launch(headless=True, channel="chromium", args=args)
        except Exception:  # noqa: BLE001 - fall back to bundled chromium
            logger.warning("full chromium channel unavailable; using bundled build")
            return playwright.chromium.launch(headless=True, args=args)

    def _fetch_one(
        self,
        page: Any,
        indicator: IndicatorConfig,
        source: SourceConfig,
        settings: Settings,
        artifacts: Path,
    ) -> SourceResult:
        last_error = "unknown error"
        for attempt in range(1, source.retries + 1):
            try:
                response = page.goto(indicator.url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)
                status = response.status if response else None
                title = page.title()
                if status == 200 and BLOCK_TITLE_MARKER not in title.lower():
                    html = page.content()
                    self._save_artifact(artifacts, indicator.slug, html, page)
                    return parse_investing_html(html, indicator)
                last_error = f"blocked or bad status (status={status}, title={title!r})"
            except Exception as exc:  # noqa: BLE001 - retried below
                last_error = str(exc)
            logger.warning(
                "investing fetch failed (attempt %s/%s) slug=%s error=%s",
                attempt,
                source.retries,
                indicator.slug,
                last_error,
            )
            if attempt < source.retries:
                time.sleep(3.0 * attempt)
        return SourceResult(slug=indicator.slug, error=f"investing fetch failed: {last_error}")

    @staticmethod
    def _save_artifact(artifacts: Path, slug: str, html: str, page: Any) -> None:
        (artifacts / f"{slug}.html").write_text(html, encoding="utf-8")
        try:
            page.screenshot(path=str(artifacts / f"{slug}.png"), full_page=False)
        except Exception:  # noqa: BLE001 - screenshots are best-effort artifacts
            logger.warning("could not capture screenshot for %s", slug)
