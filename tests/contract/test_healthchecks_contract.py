"""Contract: alerting must be graceful when HEALTHCHECKS_URL is missing."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from etl.alerts import (
    HealthchecksClient,
    notify_crash,
    notify_report,
    notify_start,
)


def test_missing_secret_disables_alerting(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("HEALTHCHECKS_URL", raising=False)
    assert HealthchecksClient.from_env() is None
    assert notify_start() is False
    assert notify_crash("etl") is False


def test_missing_secret_report_is_graceful(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("HEALTHCHECKS_URL", raising=False)
    report = {
        "run_id": "r",
        "run_date": "2026-10-08",
        "ok": 18,
        "failures": [],
        "stale_warnings": [],
        "pruned_rows": 0,
    }
    path = tmp_path / "run_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert notify_report(path) is False


def test_transport_error_never_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("time.sleep", lambda _: None)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    client = HealthchecksClient(
        "https://hc-ping.com/uuid", client=httpx.Client(transport=httpx.MockTransport(boom))
    )
    assert client.ping_start() is False
    assert client.ping_fail("x") is False
    assert client.ping_success("y") is False
