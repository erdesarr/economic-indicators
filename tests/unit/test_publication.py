"""Publication-window parsing and expected/unexpected failure classification."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from etl.publication import (
    in_publication_window,
    is_expected_forward_fill,
    parse_publication_window,
)

BOGOTA = ZoneInfo("America/Bogota")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 22, hour, minute, tzinfo=BOGOTA)


def test_parse_publication_window() -> None:
    window = parse_publication_window("15:00-20:00")
    assert (window.start.hour, window.start.minute) == (15, 0)
    assert (window.end.hour, window.end.minute) == (20, 0)


@pytest.mark.parametrize("value", ["15:00", "99:00-20:00", "15:00-", "a-b"])
def test_invalid_publication_window(value: str) -> None:
    with pytest.raises(ValueError):
        parse_publication_window(value)


def test_inside_window_boundaries_are_inclusive() -> None:
    assert in_publication_window(at(15, 0), "15:00-20:00") is True
    assert in_publication_window(at(16, 30), "15:00-20:00") is True
    assert in_publication_window(at(20, 0), "15:00-20:00") is True


def test_outside_window() -> None:
    assert in_publication_window(at(14, 59), "15:00-20:00") is False
    assert in_publication_window(at(20, 1), "15:00-20:00") is False
    assert in_publication_window(at(10, 0), "15:00-20:00") is False


def test_full_day_window() -> None:
    assert in_publication_window(at(0, 0), "00:00-23:59") is True
    assert in_publication_window(at(12, 0), "00:00-23:59") is True
    assert in_publication_window(at(23, 59), "00:00-23:59") is True


def test_overnight_window() -> None:
    assert in_publication_window(at(23, 0), "22:00-02:00") is True
    assert in_publication_window(at(1, 0), "22:00-02:00") is True
    assert in_publication_window(at(12, 0), "22:00-02:00") is False


def test_forward_fill_outside_window_is_expected() -> None:
    assert is_expected_forward_fill("forward_filled", "15:00-20:00", at(10, 0)) is True
    assert is_expected_forward_fill("forward_filled", "15:00-20:00", at(21, 0)) is True
    assert is_expected_forward_fill("failed", "15:00-20:00", at(10, 0)) is True


def test_forward_fill_inside_window_is_unexpected() -> None:
    assert is_expected_forward_fill("forward_filled", "15:00-20:00", at(16, 0)) is False
    assert is_expected_forward_fill("failed", "00:00-23:59", at(16, 0)) is False


def test_fresh_reading_is_never_expected() -> None:
    assert is_expected_forward_fill("ok", "00:00-23:59", at(16, 0)) is False
