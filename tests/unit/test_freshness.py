"""Pages freshness polling tests (no network)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from form_updater.updater import (
    StaleDataError,
    pages_fresh,
    wait_for_fresh_pages,
)

TODAY = date(2026, 10, 20)
NOW = datetime(2026, 10, 20, 16, 0, tzinfo=UTC)


def latest(day: str, generated_at: str) -> dict:
    return {
        "generated_at": generated_at,
        "indicators": {"euro": {"date": day, "value": 3607.58}},
    }


def test_fresh_when_today_and_recent() -> None:
    payload = latest("2026-10-20", "2026-10-20T15:55:00+00:00")
    assert pages_fresh(payload, TODAY, now_utc=NOW) is True


def test_stale_when_yesterday() -> None:
    payload = latest("2026-10-19", "2026-10-20T15:55:00+00:00")
    assert pages_fresh(payload, TODAY, now_utc=NOW) is False


def test_stale_when_generated_at_too_old() -> None:
    payload = latest("2026-10-20", "2026-10-20T05:00:00+00:00")
    assert pages_fresh(payload, TODAY, now_utc=NOW) is False


def test_wait_retries_until_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    responses = [
        latest("2026-10-19", "2026-10-20T15:55:00+00:00"),
        latest("2026-10-20", "2026-10-20T15:57:00+00:00"),
    ]

    def fetcher() -> dict:
        return responses.pop(0)

    result = wait_for_fresh_pages(
        TODAY, timeout_seconds=5, interval_seconds=0.01, fetcher=fetcher, now_utc=NOW
    )
    assert result["indicators"]["euro"]["date"] == "2026-10-20"


def test_wait_fails_when_still_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    stale = latest("2026-10-19", "2026-10-20T15:55:00+00:00")

    with pytest.raises(StaleDataError) as excinfo:
        wait_for_fresh_pages(
            TODAY,
            timeout_seconds=0.05,
            interval_seconds=0.01,
            fetcher=lambda: stale,
            now_utc=NOW,
        )
    assert "did not become fresh" in str(excinfo.value)
