"""Healthchecks client tests with a mocked HTTP transport (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from etl.alerts import (
    HealthchecksClient,
    notify_crash,
    notify_report,
    notify_start,
)

UUID_URL = "https://hc-ping.com/ed63b362-3485-4a86-81bb-75159e896dd9"


class Recorder:
    def __init__(self, statuses: list[int] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.statuses = statuses or []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((str(request.url), request.content.decode("utf-8")))
        status = self.statuses.pop(0) if self.statuses else 200
        return httpx.Response(status, text="OK")

    def client(self) -> HealthchecksClient:
        transport = httpx.MockTransport(self.handler)
        return HealthchecksClient(UUID_URL, client=httpx.Client(transport=transport))


def test_ping_start() -> None:
    recorder = Recorder()
    assert recorder.client().ping_start() is True
    assert recorder.calls == [(f"{UUID_URL}/start", "")]


def test_ping_success_with_body() -> None:
    recorder = Recorder()
    assert recorder.client().ping_success("run_id=1 ok=18") is True
    assert recorder.calls == [(UUID_URL, "run_id=1 ok=18")]


def test_ping_fail_with_body() -> None:
    recorder = Recorder()
    assert recorder.client().ping_fail("| Indicador | ... |") is True
    assert recorder.calls == [(f"{UUID_URL}/fail", "| Indicador | ... |")]


def test_retries_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    recorder = Recorder(statuses=[500, 502, 200])
    assert recorder.client().ping_start() is True
    assert len(recorder.calls) == 3


def test_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    recorder = Recorder(statuses=[500, 500, 500])
    assert recorder.client().ping_start() is False
    assert len(recorder.calls) == 3


def test_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    recorder = Recorder(statuses=[404])
    assert recorder.client().ping_fail("x") is False
    assert len(recorder.calls) == 1


def test_from_env_missing_is_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEALTHCHECKS_URL", raising=False)
    assert HealthchecksClient.from_env() is None
    assert notify_start(client=None) is False


def test_from_env_reads_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEALTHCHECKS_URL", UUID_URL)
    client = HealthchecksClient.from_env()
    assert client is not None
    assert client.url == UUID_URL


def test_notify_report_with_failures_pings_fail(tmp_path: Path) -> None:
    report = {
        "run_id": "r1",
        "run_date": "2026-10-08",
        "ok": 17,
        "pruned_rows": 0,
        "failures": [
            {
                "slug": "euro",
                "source": "larepublica_main",
                "error": "timeout",
                "forward_filled_value": 3625.6,
                "status": "forward_filled",
                "stale_warning": False,
            }
        ],
        "stale_warnings": [],
    }
    path = tmp_path / "run_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    recorder = Recorder()
    assert notify_report(path, client=recorder.client()) is True
    url, body = recorder.calls[0]
    assert url == f"{UUID_URL}/fail"
    assert "| euro | larepublica_main | timeout | 3625.6 | forward_filled |" in body
    assert "ok=17 failures=1" in body


def test_notify_report_success_with_stale_warning(tmp_path: Path) -> None:
    report = {
        "run_id": "r2",
        "run_date": "2026-10-08",
        "ok": 18,
        "pruned_rows": 0,
        "failures": [],
        "stale_warnings": [
            {
                "slug": "uvr",
                "source": "larepublica_main",
                "error": "source_date=2026-10-07",
                "forward_filled_value": 419.29,
                "status": "stale",
                "stale_warning": True,
            }
        ],
    }
    path = tmp_path / "run_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    recorder = Recorder()
    assert notify_report(path, client=recorder.client()) is True
    url, body = recorder.calls[0]
    assert url == UUID_URL  # success ping
    assert "stale" in body
    assert "uvr" in body


def test_notify_report_missing_file_is_graceful(tmp_path: Path) -> None:
    recorder = Recorder()
    assert notify_report(tmp_path / "nope.json", client=recorder.client()) is False
    assert recorder.calls == []


def test_notify_crash_body(tmp_path: Path) -> None:
    recorder = Recorder()
    assert notify_crash("etl", client=recorder.client()) is True
    assert recorder.calls == [(f"{UUID_URL}/fail", "crash en paso etl")]
