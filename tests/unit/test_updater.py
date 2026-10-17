"""Updater plan tests: contract loading, freshness, resolution and formatting."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from form_updater.sheet import IndicatorRow, write_snapshot
from form_updater.updater import (
    StaleDataError,
    build_plan,
    format_plan_lines,
    freshness_error,
    load_active_rows,
    load_variable_data,
    resolve_variable_data,
)

TODAY = date(2026, 10, 16)


def make_row(
    slug: str,
    id_indicador: int,
    *,
    activo: bool = True,
    fallback: str = "",
    umbral: float = 30.0,
    prefijo: str = "$ ",
    sufijo: str = "",
    mostrar: bool = True,
) -> IndicatorRow:
    return IndicatorRow(
        indicador=slug,
        prefijo=prefijo,
        sufijo=sufijo,
        mostrar_variacion=mostrar,
        symbol_up="+",
        symbol_down="-",
        fallback=fallback,
        id_indicador=id_indicador,
        activo=activo,
        umbral_variacion_pct=umbral,
    )


def write_contract(root: Path, data: dict[str, dict]) -> None:
    v1 = root / "v1"
    latest = {"generated_at": "2026-10-16T21:00:00+00:00", "indicators": {}}
    for slug, body in data.items():
        latest["indicators"][slug] = {
            "slug": slug,
            "date": body.get("date", TODAY.isoformat()),
            "value": body["value"],
            "variation_pct": body["variation"],
            "status": body.get("status", "ok"),
            "unit": "COP",
            "unit_as_published": "",
            "source_date": TODAY.isoformat(),
            "base_gap": False,
            "value_published": None,
        }
        history_dir = v1 / "indicators" / slug
        history_dir.mkdir(parents=True, exist_ok=True)
        readings = []
        if body.get("previous") is not None:
            readings.append(
                {
                    "slug": slug,
                    "date": "2026-10-15",
                    "value": body["previous"],
                    "variation_pct": body.get("previous_variation"),
                }
            )
        readings.append(
            {
                "slug": slug,
                "date": body.get("date", TODAY.isoformat()),
                "value": body["value"],
                "variation_pct": body["variation"],
            }
        )
        (history_dir / "history.json").write_text(
            json.dumps({"slug": slug, "readings": readings}), encoding="utf-8"
        )
    v1.mkdir(parents=True, exist_ok=True)
    (v1 / "latest.json").write_text(json.dumps(latest), encoding="utf-8")


def test_load_active_rows_excludes_inactive(tmp_path: Path) -> None:
    path = tmp_path / "sheet_config.csv"
    write_snapshot(
        path,
        [make_row("euro", 3), make_row("uvr", 5, activo=False)],
        {"umbral_variacion_pct": "30"},
        "indicators_config",
        "ajustes",
    )
    rows = load_active_rows(path)
    assert [row.indicador for row in rows] == ["euro"]


def test_plan_primary_and_fallback(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    write_snapshot(
        sheet,
        [
            make_row("euro", 3, umbral=30.0),
            make_row("petroleo_brent", 7, fallback="petroleo_wti", umbral=30.0),
        ],
        {"umbral_variacion_pct": "30"},
        "indicators_config",
        "ajustes",
    )
    contract = tmp_path / "contract"
    write_contract(
        contract,
        {
            "euro": {"value": 3607.58, "variation": 0.15, "previous": 3602.0},
            "petroleo_brent": {
                "value": 200.0,
                "variation": 45.0,
                "previous": 104.0,
                "previous_variation": 3.0,
            },
            "petroleo_wti": {"value": 101.71, "variation": 4.2, "previous": 97.6},
        },
    )
    data = load_variable_data(contract)
    plan = build_plan(load_active_rows(sheet), data)
    assert plan.ok
    by_slug = {item.row.indicador: item for item in plan.planned}
    assert by_slug["euro"].resolution.source == "primary"
    assert by_slug["euro"].formatted == "$ 3.607,58 +0,15%"
    assert by_slug["petroleo_brent"].resolution.source == "fallback"
    # Variation belongs to the fallback value, not the discarded primary.
    assert by_slug["petroleo_brent"].formatted == "$ 101,71 +4,20%"
    assert any(
        "excede umbral" in reason for reason in by_slug["petroleo_brent"].resolution.discarded
    )


def test_plan_forward_fill_when_both_over_threshold(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    write_snapshot(
        sheet,
        [make_row("petroleo_brent", 7, fallback="petroleo_wti")],
        {"umbral_variacion_pct": "30"},
        "indicators_config",
        "ajustes",
    )
    contract = tmp_path / "contract"
    write_contract(
        contract,
        {
            "petroleo_brent": {
                "value": 200.0,
                "variation": 45.0,
                "previous": 104.0,
                "previous_variation": 2.5,
            },
            "petroleo_wti": {
                "value": 180.0,
                "variation": 60.0,
                "previous": 97.6,
                "previous_variation": 1.0,
            },
        },
    )
    plan = build_plan(load_active_rows(sheet), load_variable_data(contract))
    item = plan.planned[0]
    assert item.resolution.source == "forward_filled"
    assert item.resolution.value == pytest.approx(104.0)
    assert item.formatted == "$ 104 +2,50%"


def test_plan_omits_row_without_candidates(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    write_snapshot(
        sheet,
        [make_row("euro", 3)],
        {"umbral_variacion_pct": "30"},
        "indicators_config",
        "ajustes",
    )
    contract = tmp_path / "contract"
    write_contract(contract, {})
    plan = build_plan(load_active_rows(sheet), load_variable_data(contract))
    assert not plan.ok
    assert plan.failures[0][0].indicador == "euro"


def test_plan_lines_use_repr(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    write_snapshot(
        sheet,
        [make_row("cafe_colombian_milds", 4, prefijo="US$ ", sufijo=" /lb")],
        {"umbral_variacion_pct": "30"},
        "indicators_config",
        "ajustes",
    )
    contract = tmp_path / "contract"
    write_contract(
        contract,
        {"cafe_colombian_milds": {"value": 3.58, "variation": -2.86, "previous": 3.68}},
    )
    plan = build_plan(load_active_rows(sheet), load_variable_data(contract))
    lines = format_plan_lines(plan)
    assert "'US$ 3,58 /lb -2,86%'" in lines[1]
    assert "primary" in lines[1]


def test_freshness_check(tmp_path: Path) -> None:
    contract = tmp_path / "contract"
    write_contract(contract, {"euro": {"value": 3607.58, "variation": 0.1, "previous": 3600.0}})
    assert freshness_error(contract, TODAY) is None
    stale = freshness_error(contract, date(2026, 10, 17))
    assert stale is not None
    assert "stale" in stale


def _raw_contract_transport(
    newest_date: date,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/latest.json"):
            return httpx.Response(
                200,
                json={
                    "indicators": {
                        "euro": {
                            "slug": "euro",
                            "date": newest_date.isoformat(),
                            "value": 3600.0,
                            "variation_pct": 0.5,
                            "status": "ok",
                        }
                    }
                },
            )
        if url.endswith("/history.json"):
            return httpx.Response(
                200,
                json={
                    "readings": [
                        {"date": "2026-10-15", "value": 3590.0, "variation_pct": 0.2},
                        {
                            "date": newest_date.isoformat(),
                            "value": 3600.0,
                            "variation_pct": 0.5,
                        },
                    ]
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_stale_local_falls_back_to_fresh_raw(tmp_path: Path) -> None:
    local = tmp_path / "contract"
    write_contract(
        local,
        {"euro": {"value": 3000.0, "variation": 1.0, "previous": 2990.0, "date": "2026-10-15"}},
    )
    assert freshness_error(local, TODAY) is not None
    client = httpx.Client(transport=_raw_contract_transport(TODAY))
    source = resolve_variable_data(
        local, TODAY, raw_base_url="https://raw.test/docs/api/v1", client=client
    )
    assert source.origin == "raw.githubusercontent.com"
    assert source.data["euro"].value == pytest.approx(3600.0)
    assert source.data["euro"].previous_value == pytest.approx(3590.0)


def test_stale_local_and_stale_raw_fails(tmp_path: Path) -> None:
    local = tmp_path / "contract"
    write_contract(
        local,
        {"euro": {"value": 3000.0, "variation": 1.0, "previous": 2990.0, "date": "2026-10-15"}},
    )
    client = httpx.Client(transport=_raw_contract_transport(date(2026, 10, 15)))
    with pytest.raises(StaleDataError) as excinfo:
        resolve_variable_data(
            local, TODAY, raw_base_url="https://raw.test/docs/api/v1", client=client
        )
    assert "refusing to write old data" in str(excinfo.value)
    assert "raw.githubusercontent fallback failed" in str(excinfo.value)


def test_fresh_local_does_not_call_remote(tmp_path: Path) -> None:
    local = tmp_path / "contract"
    write_contract(local, {"euro": {"value": 3607.58, "variation": 0.1, "previous": 3600.0}})

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected remote call: {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = resolve_variable_data(
        local, TODAY, raw_base_url="https://raw.test/docs/api/v1", client=client
    )
    assert source.origin == "local"
    assert source.data["euro"].value == pytest.approx(3607.58)
