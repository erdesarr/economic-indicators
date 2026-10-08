"""HTTP helper tests with a fake requests session (no network)."""

from __future__ import annotations

import pytest
import requests

from etl.http import FetchError, HttpClient
from etl.models import Settings


def settings() -> Settings:
    return Settings(
        timezone="America/Bogota",
        history_days=31,
        database="data/x.db",
        max_gap_days=7,
        politeness_seconds=0.01,
        request_timeout=5,
        artifacts_dir="artifacts",
        user_agents=("ua-1", "ua-2"),
    )


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok", payload: object = None) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> object:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0
        self.last_headers: dict[str, str] = {}

    def get(self, url: str, headers: dict[str, str], timeout: int) -> object:
        self.calls += 1
        self.last_headers = headers
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_get_text_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    session = FakeSession([FakeResponse(200, "<html>hola</html>")])
    client = HttpClient(settings(), session=session)  # type: ignore[arg-type]
    assert client.get_text("http://x") == "<html>hola</html>"
    assert "User-Agent" in session.last_headers


def test_get_retries_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    session = FakeSession([FakeResponse(503), FakeResponse(200, "ok")])
    client = HttpClient(settings(), session=session)  # type: ignore[arg-type]
    assert client.get_text("http://x") == "ok"
    assert session.calls == 2


def test_get_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    session = FakeSession([requests.ConnectionError("boom")] * 3)
    client = HttpClient(settings(), session=session)  # type: ignore[arg-type]
    with pytest.raises(FetchError):
        client.get_text("http://x", retries=3)
    assert session.calls == 3


def test_get_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    session = FakeSession([FakeResponse(200, payload=[{"a": 1}])])
    client = HttpClient(settings(), session=session)  # type: ignore[arg-type]
    assert client.get_json("http://x") == [{"a": 1}]


def test_get_json_retries_on_bad_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)

    class BadJsonResponse(FakeResponse):
        def json(self) -> object:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    session = FakeSession([BadJsonResponse(200), FakeResponse(200, payload={"ok": True})])
    client = HttpClient(settings(), session=session)  # type: ignore[arg-type]
    assert client.get_json("http://x") == {"ok": True}
    assert session.calls == 2


def test_get_json_gives_up_on_bad_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)

    class BadJsonResponse(FakeResponse):
        def json(self) -> object:
            raise ValueError("nope")

    session = FakeSession([BadJsonResponse(200)] * 3)
    client = HttpClient(settings(), session=session)  # type: ignore[arg-type]
    with pytest.raises(FetchError):
        client.get_json("http://x", retries=3)


def test_client_error_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    session = FakeSession([FakeResponse(404)])
    client = HttpClient(settings(), session=session)  # type: ignore[arg-type]
    with pytest.raises(FetchError):
        client.get_text("http://x", retries=3)
    assert session.calls == 1


def test_sleep_politeness(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)
    client = HttpClient(settings())
    client.sleep_politeness()
    assert slept == [0.01]
