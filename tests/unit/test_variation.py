"""Tests for day-over-day variation, base_gap and division by zero."""

from __future__ import annotations

from datetime import date

import pytest

from etl.variation import compute_variation


def test_consecutive_days() -> None:
    result = compute_variation(110.0, date(2026, 10, 8), 100.0, date(2026, 10, 7))
    assert result.pct == pytest.approx(10.0)
    assert result.base_gap is False
    assert result.base_date == date(2026, 10, 7)


def test_gap_within_seven_days_sets_base_gap() -> None:
    result = compute_variation(110.0, date(2026, 10, 8), 100.0, date(2026, 10, 5))
    assert result.pct == pytest.approx(10.0)
    assert result.base_gap is True


def test_no_previous_returns_none() -> None:
    result = compute_variation(110.0, date(2026, 10, 8), None, None)
    assert result.pct is None
    assert result.base_gap is True


def test_previous_beyond_max_gap_returns_none() -> None:
    result = compute_variation(110.0, date(2026, 10, 8), 100.0, date(2026, 9, 20), max_gap_days=7)
    assert result.pct is None
    assert result.base_gap is True


def test_division_by_zero_returns_none() -> None:
    result = compute_variation(110.0, date(2026, 10, 8), 0.0, date(2026, 10, 7))
    assert result.pct is None
    assert result.base_gap is False


def test_negative_variation() -> None:
    result = compute_variation(90.0, date(2026, 10, 8), 100.0, date(2026, 10, 7))
    assert result.pct == pytest.approx(-10.0)


def test_same_day_base_is_rejected() -> None:
    result = compute_variation(110.0, date(2026, 10, 8), 100.0, date(2026, 10, 8))
    assert result.pct is None
    assert result.base_gap is True
