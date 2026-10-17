"""Resolution chain tests: guard, fallback, forward_fill and source variation."""

from __future__ import annotations

import pytest

from form_updater.resolution import (
    Resolution,
    ResolutionFailure,
    VariableSnapshot,
    resolve_row,
)
from form_updater.sheet import IndicatorRow

THRESHOLD = 30.0


def row(
    indicador: str = "petroleo_brent",
    fallback: str = "petroleo_wti",
    umbral: float = THRESHOLD,
) -> IndicatorRow:
    return IndicatorRow(
        indicador=indicador,
        prefijo="US$ ",
        sufijo="",
        mostrar_variacion=True,
        symbol_up="+",
        symbol_down="-",
        fallback=fallback,
        id_indicador=7,
        activo=True,
        umbral_variacion_pct=umbral,
    )


def snapshot(
    value: float,
    variation: float | None,
    status: str = "ok",
    previous: float | None = None,
    previous_variation: float | None = None,
) -> VariableSnapshot:
    return VariableSnapshot(
        value=value,
        variation_pct=variation,
        status=status,
        previous_value=previous,
        previous_variation_pct=previous_variation,
    )


def test_primary_within_threshold() -> None:
    data = {
        "petroleo_brent": snapshot(109.59, 5.0, previous=104.0),
        "petroleo_wti": snapshot(101.71, 4.2, previous=97.6),
    }
    result = resolve_row(row(), data, THRESHOLD)
    assert isinstance(result, Resolution)
    assert result.source == "primary"
    assert result.source_slug == "petroleo_brent"
    assert result.value == pytest.approx(109.59)
    assert result.variation_pct == pytest.approx(5.0)
    assert result.degraded is False


def test_primary_over_threshold_uses_fallback() -> None:
    data = {
        "petroleo_brent": snapshot(200.0, 45.0, previous=104.0),
        "petroleo_wti": snapshot(101.71, 4.2, previous=97.6),
    }
    result = resolve_row(row(), data, THRESHOLD)
    assert isinstance(result, Resolution)
    assert result.source == "fallback"
    assert result.source_slug == "petroleo_wti"
    assert result.value == pytest.approx(101.71)
    # Variation belongs to the variable whose value was used (fallback).
    assert result.variation_pct == pytest.approx(4.2)
    assert any("excede umbral" in reason for reason in result.discarded)
    assert result.degraded is True


def test_primary_and_fallback_over_threshold_use_forward_fill() -> None:
    data = {
        "petroleo_brent": snapshot(200.0, 45.0, previous=104.0, previous_variation=3.1),
        "petroleo_wti": snapshot(180.0, 60.0, previous=97.6, previous_variation=2.0),
    }
    result = resolve_row(row(), data, THRESHOLD)
    assert isinstance(result, Resolution)
    assert result.source == "forward_filled"
    assert result.value == pytest.approx(104.0)  # previous stored reading
    assert result.variation_pct == pytest.approx(3.1)  # variation of the used value
    assert len(result.discarded) == 2
    assert result.degraded is True


def test_no_previous_day_guard_does_not_apply() -> None:
    data = {
        "petroleo_brent": snapshot(200.0, None, previous=None),
    }
    result = resolve_row(row(fallback=""), data, THRESHOLD)
    assert isinstance(result, Resolution)
    assert result.source == "primary"
    assert result.variation_pct is None


def test_forward_fill_is_never_discarded() -> None:
    # ETL marked forward_filled; its (copied) variation is huge in this
    # contrived snapshot, but the guard must not discard it.
    data = {
        "petroleo_brent": snapshot(
            104.0, 99.0, status="forward_filled", previous=104.0, previous_variation=99.0
        ),
    }
    result = resolve_row(row(fallback=""), data, THRESHOLD)
    assert isinstance(result, Resolution)
    assert result.source == "forward_filled"
    assert result.value == pytest.approx(104.0)
    assert not any("excede umbral" in reason for reason in result.discarded)


def test_forward_fill_when_primary_etl_failed_and_no_fallback() -> None:
    data = {
        "euro": snapshot(
            3607.58, 0.0, status="forward_filled", previous=3607.58, previous_variation=0.0
        ),
    }
    result = resolve_row(row(indicador="euro", fallback=""), data, THRESHOLD)
    assert isinstance(result, Resolution)
    assert result.source == "forward_filled"
    assert result.variation_pct == pytest.approx(0.0)


def test_no_usable_candidate_returns_failure() -> None:
    data = {"petroleo_brent": snapshot(200.0, 45.0, previous=None, previous_variation=None)}
    result = resolve_row(row(fallback=""), data, THRESHOLD)
    assert isinstance(result, ResolutionFailure)
    assert any("excede umbral" in reason for reason in result.reasons)
    assert any("no usable candidate" in reason for reason in result.reasons)


def test_missing_primary_data_uses_fallback() -> None:
    data = {"petroleo_wti": snapshot(101.71, 4.2, previous=97.6)}
    result = resolve_row(row(), data, THRESHOLD)
    assert isinstance(result, Resolution)
    assert result.source == "fallback"
    assert result.variation_pct == pytest.approx(4.2)


def test_threshold_is_per_row() -> None:
    # Same variation passes with a 50% row threshold and is discarded at 10%
    # (then forward_fill supplies the previous reading).
    data = {"uvr": snapshot(419.29, 20.0, previous=349.4, previous_variation=2.0)}
    passing = resolve_row(row(indicador="uvr", fallback="", umbral=50.0), data, 50.0)
    failing = resolve_row(row(indicador="uvr", fallback="", umbral=10.0), data, 10.0)
    assert isinstance(passing, Resolution)
    assert passing.source == "primary"
    assert isinstance(failing, Resolution)
    assert failing.source == "forward_filled"
    assert any("excede umbral" in reason for reason in failing.discarded)


def test_degraded_false_for_clean_primary() -> None:
    data = {"uvr": snapshot(419.29, 1.0, previous=415.0)}
    result = resolve_row(row(indicador="uvr", fallback=""), data, THRESHOLD)
    assert isinstance(result, Resolution)
    assert result.degraded is False
