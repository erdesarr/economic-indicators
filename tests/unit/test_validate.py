"""Tests for the validation/approval helpers (offline)."""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from pathlib import Path

from rich.table import Table

from etl.models import AppConfig, Reading, SourceResult
from etl.pipeline import PipelineDeps
from etl.storage import connect, indicator_id, init_db, sync_indicators, upsert_reading
from etl.validate import (
    RecordingClient,
    build_table,
    collect_live,
    previous_values_from_db,
    raw_fragment,
    recalibrated_range,
    record_approval,
    save_fixtures,
    tolerance_for,
    update_ranges,
)
from tests.conftest import CONFIG_PATH, GOLDEN, load_text


def ok_result(slug: str, value: float = 100.0) -> SourceResult:
    return SourceResult(
        slug=slug,
        value=value,
        unit_as_published="unidad",
        raw_label=slug,
        raw_text=f"{value}",
        source_date=date(2026, 10, 8),
    )


def test_tolerance_for_known_and_unknown() -> None:
    assert tolerance_for("COP") == ("relative", 0.10)
    assert tolerance_for("% E.A.") == ("absolute", 2.0)
    assert tolerance_for("unidad-desconocida") == ("relative", 0.20)


def test_recalibrated_range_relative_and_absolute(config: AppConfig) -> None:
    cop = recalibrated_range(config.by_slug("dolar_oficial_hoy"), 3200.0)
    assert cop == [2880.0, 3520.0]
    ibr = recalibrated_range(config.by_slug("ibr_overnight"), 11.5)
    assert ibr == [9.5, 13.5]


def test_build_table_rows_and_columns(config: AppConfig) -> None:
    results = {indicator.slug: ok_result(indicator.slug) for indicator in config.indicators}
    results["euro"] = SourceResult(slug="euro", error="timeout")
    table = build_table(config, results, {"euro": 100.0})
    assert isinstance(table, Table)
    assert table.row_count == len(config.indicators)


def test_raw_fragment_html_and_json() -> None:
    client = RecordingClient.__new__(RecordingClient)
    client.texts = {"http://x": "<html>DÓLAR OFICIAL HOY <b>$ 3.238,88</b></html>"}
    client.jsons = {"http://y": [{"data": "08/10/2026", "valor": "5.0113"}]}
    assert "3.238,88" in raw_fragment("s", "http://x", "DÓLAR OFICIAL HOY", client)
    assert "5.0113" in raw_fragment("s", "http://y", "Compra", client)
    assert "no raw payload" in raw_fragment("s", "http://z", "nada", client)


def test_update_ranges_rewrites_yaml(tmp_path: Path, config: AppConfig) -> None:
    target = tmp_path / "indicators.yaml"
    target.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    results = {"dolar_oficial_hoy": ok_result("dolar_oficial_hoy", 3238.88)}
    update_ranges(target, config, results)
    import yaml

    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    entry = next(e for e in raw["indicators"] if e["slug"] == "dolar_oficial_hoy")
    assert entry["plausible_range"] == [2915.0, 3563.0]


def _tmp_config(tmp_path: Path, config: AppConfig) -> AppConfig:
    settings = dataclasses.replace(
        config.settings,
        database=str(tmp_path / "val.db"),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    return dataclasses.replace(config, settings=settings)


def test_save_fixtures_writes_all_sources(tmp_path: Path, config: AppConfig) -> None:
    tmp_cfg = _tmp_config(tmp_path, config)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    for slug in ("gas_ttf_nl", "gas_henry_micro", "lng_jkm"):
        (artifacts / f"{slug}.html").write_text("<html></html>", encoding="utf-8")

    recorder = RecordingClient(tmp_cfg.settings)
    igbc_url = next(
        indicator.url
        for indicator in config.indicators
        if indicator.source == "larepublica_igbc"
    )
    recorder.texts = {
        "https://www.larepublica.co/indicadores-economicos": load_text(
            "larepublica", "main.html"
        ),
        "https://www.larepublica.co/indicadores-economicos/bancos/dtf": load_text(
            "larepublica", "dtf.html"
        ),
        igbc_url: load_text("larepublica", "igbc.html"),
    }
    recorder.jsons = {
        "https://suameca.banrep.gov.co/x": [{"id": 241, "data": [[1, 11.4]]}],
        "https://api.bcb.gov.br/x": [{"data": "08/10/2026", "valor": "5.0113"}],
    }
    results = {indicator.slug: ok_result(indicator.slug) for indicator in config.indicators}
    written = save_fixtures(tmp_cfg, results, recorder, golden_dir=tmp_path / "golden")
    names = {path.name for path in written}
    assert {"main.html", "dtf.html", "igbc.html"} <= names
    assert {"series.json", "sgs_10813.json", "expected.json"} <= names
    assert (tmp_path / "golden" / "larepublica" / "expected.json").exists()
    assert (tmp_path / "golden" / "investing" / "gas_ttf_nl.html").exists()
    data = json.loads((tmp_path / "golden" / "larepublica" / "expected.json").read_text())
    assert "dolar_oficial_hoy" in data["indicators"]


def test_record_approval_writes_hash(tmp_path: Path, config: AppConfig) -> None:
    fixture = tmp_path / "expected.json"
    fixture.write_text('{"ok": true}', encoding="utf-8")
    approvals = tmp_path / "APPROVALS.md"
    results = {"uvr": ok_result("uvr")}
    record_approval(
        CONFIG_PATH,
        config,
        results,
        [fixture],
        approvals_path=approvals,
        approved_by="tester",
    )
    content = approvals.read_text(encoding="utf-8")
    assert "tester" in content
    assert "expected.json" in content
    assert "Aprobaciones del dueño" in content


def test_previous_values_from_db(tmp_path: Path, config: AppConfig) -> None:
    tmp_cfg = _tmp_config(tmp_path, config)
    conn = connect(tmp_cfg.settings.database)
    init_db(conn)
    sync_indicators(conn, tmp_cfg.indicators)
    iid = indicator_id(conn, "uvr")
    upsert_reading(
        conn,
        Reading(
            indicator_id=iid,
            date=date(2026, 10, 7),
            value=419.0,
            value_published=None,
            unit_as_published="COP",
            raw_label="UVR",
            raw_text="$ 419,00",
            source_date=date(2026, 10, 7),
            status="ok",
            base_gap=False,
            reason=None,
            created_at="x",
            run_id="r",
        ),
    )
    conn.close()
    values = previous_values_from_db(tmp_cfg, date(2026, 10, 8))
    assert values["uvr"] == 419.0
    assert values["euro"] is None


class FakeAdapter:
    name = "fake"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def fetch(self, indicators, source, settings, client):  # type: ignore[no-untyped-def]
        return {indicator.slug: ok_result(indicator.slug) for indicator in indicators}


def test_collect_live_with_fake_adapters(tmp_path: Path, config: AppConfig) -> None:
    tmp_cfg = _tmp_config(tmp_path, config)
    adapter = FakeAdapter(tmp_cfg)
    deps = PipelineDeps(adapters=dict.fromkeys(tmp_cfg.sources, adapter))
    results, recorder = collect_live(tmp_cfg, deps=deps)
    assert len(results) == len(tmp_cfg.indicators)
    assert all(result.ok for result in results.values())
    assert recorder is not None


def test_collect_live_source_exception(config: AppConfig) -> None:
    class Exploding:
        name = "boom"

        def fetch(self, indicators, source, settings, client):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    deps = PipelineDeps(adapters=dict.fromkeys(config.sources, Exploding()))
    results, _ = collect_live(config, deps=deps)
    assert all(not result.ok for result in results.values())
    assert all("source failure" in (result.error or "") for result in results.values())


def test_golden_fixtures_exist() -> None:
    for source in ("larepublica", "banrep", "bcb", "investing"):
        assert (GOLDEN / source / "expected.json").exists(), source
