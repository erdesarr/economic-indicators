"""Adapter orchestration tests: grouping, error paths and browser fake."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from etl.http import FetchError
from etl.models import AppConfig, Settings, SourceConfig
from etl.sources.banrep import BanrepAdapter
from etl.sources.bcb import BcbAdapter, build_query_url
from etl.sources.investing import InvestingAdapter
from etl.sources.larepublica import LaRepublicaAdapter
from tests.conftest import load_json, load_text


def settings() -> Settings:
    return Settings(
        timezone="America/Bogota",
        history_days=31,
        database="data/x.db",
        max_gap_days=7,
        politeness_seconds=0.0,
        request_timeout=5,
        artifacts_dir="artifacts",
        user_agents=("ua",),
    )


class FakeClient:
    def __init__(
        self, texts: dict[str, str] | None = None, jsons: dict[str, Any] | None = None
    ) -> None:
        self.texts = texts or {}
        self.jsons = jsons or {}
        self.slept = 0

    def get_text(self, url: str, retries: int = 3) -> str:
        if url not in self.texts:
            raise FetchError(f"missing {url}")
        return self.texts[url]

    def get_json(self, url: str, retries: int = 3) -> Any:
        if url not in self.jsons:
            raise FetchError(f"missing {url}")
        return self.jsons[url]

    def sleep_politeness(self) -> None:
        self.slept += 1


def larepublica_indicators(config: AppConfig) -> list:
    return [i for i in config.indicators if i.source == "larepublica_main"]


def test_larepublica_adapter_groups_by_url(config: AppConfig) -> None:
    indicators = larepublica_indicators(config)
    url = indicators[0].url
    client = FakeClient(texts={url: load_text("larepublica", "main.html")})
    source = SourceConfig("larepublica_main", "html", "es-CO", 1)
    results = LaRepublicaAdapter("larepublica_main").fetch(
        indicators,
        source,
        settings(),
        client,  # type: ignore[arg-type]
    )
    assert len(results) == len(indicators)
    assert results["euro"].ok
    assert client.slept == 1


def test_larepublica_adapter_fetch_error(config: AppConfig) -> None:
    indicators = larepublica_indicators(config)
    client = FakeClient()
    source = SourceConfig("larepublica_main", "html", "es-CO", 1)
    results = LaRepublicaAdapter("larepublica_main").fetch(
        indicators,
        source,
        settings(),
        client,  # type: ignore[arg-type]
    )
    assert all(not result.ok for result in results.values())


def test_banrep_adapter_fetch(config: AppConfig) -> None:
    indicators = [i for i in config.indicators if i.source == "banrep"]
    url = indicators[0].url
    payload = load_json("banrep", "series.json")
    client = FakeClient(jsons={url: payload})
    source = SourceConfig("banrep", "api", "json-dot", 1)
    results = BanrepAdapter().fetch(indicators, source, settings(), client)  # type: ignore[arg-type]
    assert all(result.ok for result in results.values())
    assert all(result.value_published is not None for result in results.values())


def test_bcb_adapter_fetch(config: AppConfig) -> None:
    indicators = [i for i in config.indicators if i.source == "bcb"]
    url = indicators[0].url
    payload = load_json("bcb", "sgs_10813.json")
    client = FakeClient(jsons={build_query_url(url): payload})
    source = SourceConfig("bcb", "api", "json-dot", 1)
    results = BcbAdapter().fetch(indicators, source, settings(), client)  # type: ignore[arg-type]
    assert results["usd_brl_compra"].ok


def test_bcb_query_url_bounded_window() -> None:
    from datetime import date

    url = build_query_url(
        "https://api.bcb.gov.br/dados/serie/bcdata.sgs.10813/dados?formato=json",
        today=date(2026, 10, 8),
    )
    assert "formato=json" in url
    assert "dataInicial=23%2F09%2F2026" in url
    assert "dataFinal=08%2F10%2F2026" in url


# ---------------------------------------------------------------------------
# investing.com fake browser
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status


class FakePage:
    def __init__(
        self,
        html: str = "",
        status: int = 200,
        title: str = "Precio TTF",
        pages: dict[str, str] | None = None,
    ) -> None:
        self._html = html
        self._status = status
        self._title = title
        self._pages = pages or {}
        self.screenshots = 0

    def goto(self, url: str, wait_until: str, timeout: int) -> FakeResponse:
        if url in self._pages:
            self._html = self._pages[url]
            self._status = 200
            self._title = "Precio"
            return FakeResponse(200)
        return FakeResponse(self._status)

    def wait_for_timeout(self, ms: int) -> None:
        return None

    def title(self) -> str:
        return self._title

    def content(self) -> str:
        return self._html

    def screenshot(self, path: str, full_page: bool = False) -> None:
        self.screenshots += 1
        Path(path).write_bytes(b"png")


class FakeBrowser:
    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self.pages = pages or {}

    def new_context(self, **kwargs: object) -> FakeContext:
        return FakeContext(self)

    def close(self) -> None:
        return None


class FakeContext:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.page = FakePage(pages=browser.pages)

    def add_init_script(self, script: str) -> None:
        return None

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        return None


class FakeChromium:
    def __init__(self, fail_channel: bool = False, pages: dict[str, str] | None = None) -> None:
        self.fail_channel = fail_channel
        self.pages = pages or {}
        self.launches: list[dict[str, Any]] = []

    def launch(self, **kwargs: Any) -> FakeBrowser:
        self.launches.append(kwargs)
        if self.fail_channel and kwargs.get("channel"):
            raise RuntimeError("channel unavailable")
        return FakeBrowser(self.pages)


class FakePlaywright:
    def __init__(self, fail_channel: bool = False, pages: dict[str, str] | None = None) -> None:
        self.chromium = FakeChromium(fail_channel, pages)


def test_investing_launch_falls_back_to_bundled(monkeypatch: pytest.MonkeyPatch) -> None:
    playwright = FakePlaywright(fail_channel=True)
    browser = InvestingAdapter._launch(playwright)
    assert isinstance(browser, FakeBrowser)
    assert playwright.chromium.launches[0]["channel"] == "chromium"
    assert "channel" not in playwright.chromium.launches[1]


def test_investing_fetch_one_success(config: AppConfig, tmp_path: Path) -> None:
    indicator = config.by_slug("gas_ttf_nl")
    source = SourceConfig("investing", "browser", "es-ES", 1, "https://es.investing.com/")
    page = FakePage(load_text("investing", "gas_ttf_nl.html"))
    result = InvestingAdapter()._fetch_one(page, indicator, source, settings(), tmp_path)
    assert result.ok
    assert result.value == pytest.approx(77.205)
    assert (tmp_path / "gas_ttf_nl.html").exists()
    assert page.screenshots == 1


def test_investing_fetch_one_blocked(
    config: AppConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    indicator = config.by_slug("gas_ttf_nl")
    source = SourceConfig("investing", "browser", "es-ES", 2)
    page = FakePage("<html></html>", status=403, title="Un momento…")
    result = InvestingAdapter()._fetch_one(page, indicator, source, settings(), tmp_path)
    assert not result.ok
    assert "blocked" in (result.error or "")


def test_investing_fetch_full_flow(
    config: AppConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import playwright.sync_api as pw_api

    pages = {
        indicator.url: load_text("investing", f"{indicator.slug}.html")
        for indicator in config.indicators
        if indicator.source == "investing"
    }
    fake = FakePlaywright(pages=pages)
    monkeypatch.setattr(pw_api, "sync_playwright", lambda: _Ctx(fake))
    indicators = [i for i in config.indicators if i.source == "investing"]
    source = SourceConfig("investing", "browser", "es-ES", 1, "https://es.investing.com/")
    local_settings = settings()
    local_settings = type(local_settings)(
        timezone=local_settings.timezone,
        history_days=local_settings.history_days,
        database=local_settings.database,
        max_gap_days=local_settings.max_gap_days,
        politeness_seconds=0.0,
        request_timeout=local_settings.request_timeout,
        artifacts_dir=str(tmp_path),
        user_agents=local_settings.user_agents,
    )
    results = InvestingAdapter().fetch(indicators, source, local_settings, FakeClient())  # type: ignore[arg-type]
    assert len(results) == 3
    assert all(result.ok for result in results.values())
    assert results["gas_ttf_nl"].value == pytest.approx(77.205)
    assert results["gas_henry_micro"].value == pytest.approx(3.143)


class _Ctx:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    def __enter__(self) -> FakePlaywright:
        return self.playwright

    def __exit__(self, *args: object) -> bool:
        return False
