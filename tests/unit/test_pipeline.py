"""Pipeline tests: persistence, forward-fill, stale detection, exit codes."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from etl.models import (
    AppConfig,
    IndicatorConfig,
    Settings,
    SourceConfig,
    SourceResult,
)
from etl.pipeline import PipelineDeps, report_to_dict, run_etl
from etl.storage import connect, indicator_id
from tests.conftest import live_values


class FakeAdapter:
    name = "fake"

    def __init__(
        self,
        values: dict[str, float],
        errors: dict[str, str] | None = None,
        source_dates: dict[str, date] | None = None,
    ) -> None:
        self.values = values
        self.errors = errors or {}
        self.source_dates = source_dates or {}

    def fetch(
        self,
        indicators: Sequence[IndicatorConfig],
        source: SourceConfig,
        settings: Settings,
        client: object,
    ) -> dict[str, SourceResult]:
        results: dict[str, SourceResult] = {}
        for indicator in indicators:
            if indicator.slug in self.errors:
                results[indicator.slug] = SourceResult(
                    slug=indicator.slug, error=self.errors[indicator.slug]
                )
                continue
            results[indicator.slug] = SourceResult(
                slug=indicator.slug,
                value=self.values.get(indicator.slug, 100.0),
                raw_text=f"{self.values.get(indicator.slug, 100.0)}",
                unit_as_published="unidad",
                raw_label=indicator.visible_label,
                source_date=self.source_dates.get(indicator.slug),
            )
        return results


def make_deps(
    config: AppConfig,
    values: dict[str, float],
    errors: dict[str, str] | None = None,
    source_dates: dict[str, date] | None = None,
) -> PipelineDeps:
    adapter = FakeAdapter(values, errors, source_dates)
    return PipelineDeps(adapters=dict.fromkeys(config.sources, adapter))


def test_full_run_persists_all_ok(tmp_path: Path, config: AppConfig) -> None:
    values = live_values(config)
    values["dolar_oficial_manana"] = 3250.0
    deps = make_deps(config, values)
    report = run_etl(config, deps=deps, run_date=date(2026, 10, 8), db_path=str(tmp_path / "t.db"))
    assert report.ok_count == len(config.indicators)
    assert report.exit_code == 0
    assert report.failures_detail == []
    conn = connect(tmp_path / "t.db")
    try:
        rows = conn.execute("SELECT COUNT(*) AS n FROM reading WHERE status = 'ok'").fetchone()
        assert rows["n"] == len(config.indicators)
    finally:
        conn.close()


def test_forward_fill_on_failure(tmp_path: Path, config: AppConfig) -> None:
    db = str(tmp_path / "t.db")
    values = live_values(config)
    values["dolar_oficial_manana"] = 3250.0

    run_etl(config, deps=make_deps(config, values), run_date=date(2026, 10, 8), db_path=db)

    failing = make_deps(config, values, errors={"euro": "timeout"})
    report = run_etl(config, deps=failing, run_date=date(2026, 10, 9), db_path=db)
    assert len(report.failures) == 1
    assert report.failures[0].slug == "euro"
    assert len(report.failures_detail) == 1
    assert report.failures_detail[0].status == "forward_filled"

    conn = connect(db)
    try:
        iid = indicator_id(conn, "euro")
        row = conn.execute(
            "SELECT * FROM reading WHERE indicator_id = ? AND date = ?",
            (iid, "2026-10-09"),
        ).fetchone()
        assert row["status"] == "forward_filled"
        assert row["value"] == pytest.approx(values["euro"])
        assert row["reason"] == "timeout"
        assert row["base_gap"] == 1
    finally:
        conn.close()


def test_out_of_range_is_forward_filled(tmp_path: Path, config: AppConfig) -> None:
    db = str(tmp_path / "t.db")
    values = live_values(config)
    values["dolar_oficial_manana"] = 3250.0
    run_etl(config, deps=make_deps(config, values), run_date=date(2026, 10, 8), db_path=db)

    bad_values = dict(values)
    bad_values["uvr"] = 99999.0
    report = run_etl(
        config,
        deps=make_deps(config, bad_values),
        run_date=date(2026, 10, 9),
        db_path=db,
    )
    assert any(r.slug == "uvr" and r.status == "forward_filled" for r in report.results)
    assert any(item.slug == "uvr" for item in report.failures_detail)


def test_all_failures_without_previous_exits_1(tmp_path: Path, config: AppConfig) -> None:
    errors = {indicator.slug: "boom" for indicator in config.indicators}
    report = run_etl(
        config,
        deps=make_deps(config, {}, errors=errors),
        run_date=date(2026, 10, 8),
        db_path=str(tmp_path / "t.db"),
    )
    assert report.exit_code == 1
    assert all(r.status == "failed" for r in report.results)
    assert all("sin histórico para forward-fill" in (r.error or "") for r in report.results)


def test_stale_source_date_warning(tmp_path: Path, config: AppConfig) -> None:
    db = str(tmp_path / "t.db")
    values = live_values(config)
    values["dolar_oficial_manana"] = 3250.0
    run_etl(
        config,
        deps=make_deps(config, values, source_dates={"uvr": date(2026, 10, 6)}),
        run_date=date(2026, 10, 8),
        db_path=db,
    )

    report = run_etl(
        config,
        deps=make_deps(config, values, source_dates={"uvr": date(2026, 10, 6)}),
        run_date=date(2026, 10, 9),
        db_path=db,
    )
    stale = [r for r in report.results if r.stale_warning]
    assert [r.slug for r in stale] == ["uvr"]
    assert [item.slug for item in report.stale_detail] == ["uvr"]


def test_manana_missing_block_forward_fills(tmp_path: Path, config: AppConfig) -> None:
    """Simulates a page without the 'DÓLAR OFICIAL MAÑANA' block."""

    db = str(tmp_path / "t.db")
    values = live_values(config)
    values["dolar_oficial_manana"] = 3250.0
    run_etl(config, deps=make_deps(config, values), run_date=date(2026, 10, 8), db_path=db)

    missing = make_deps(config, values, errors={"dolar_oficial_manana": "label not found in page"})
    report = run_etl(config, deps=missing, run_date=date(2026, 10, 9), db_path=db)
    assert any(
        r.slug == "dolar_oficial_manana" and r.status == "forward_filled" for r in report.results
    )
    conn = connect(db)
    try:
        iid = indicator_id(conn, "dolar_oficial_manana")
        row = conn.execute(
            "SELECT * FROM reading WHERE indicator_id = ? AND date = ?",
            (iid, "2026-10-09"),
        ).fetchone()
        assert row["status"] == "forward_filled"
        assert row["value"] == pytest.approx(3250.0)
    finally:
        conn.close()


def test_prune_runs_on_successful_run(tmp_path: Path, config: AppConfig) -> None:
    db = str(tmp_path / "t.db")
    values = live_values(config)
    values["dolar_oficial_manana"] = 3250.0
    report = run_etl(config, deps=make_deps(config, values), run_date=date(2026, 10, 8), db_path=db)
    assert report.pruned_rows == 0

    conn = connect(db)
    try:
        iid = indicator_id(conn, "uvr")
        conn.execute(
            "INSERT INTO reading (indicator_id, date, value, status, base_gap, created_at, run_id) "
            "VALUES (?, '2026-01-01', 1.0, 'ok', 0, 'x', 'old')",
            (iid,),
        )
        conn.commit()
    finally:
        conn.close()

    report2 = run_etl(
        config, deps=make_deps(config, values), run_date=date(2026, 10, 9), db_path=db
    )
    assert report2.pruned_rows == 1


def test_variation_between_consecutive_runs(tmp_path: Path, config: AppConfig) -> None:
    db = str(tmp_path / "t.db")
    values = live_values(config)
    values["dolar_oficial_manana"] = 3250.0
    run_etl(config, deps=make_deps(config, values), run_date=date(2026, 10, 8), db_path=db)

    changed = dict(values)
    changed["uvr"] = values["uvr"] * 1.10
    run_etl(config, deps=make_deps(config, changed), run_date=date(2026, 10, 9), db_path=db)

    conn = connect(db)
    try:
        iid = indicator_id(conn, "uvr")
        row = conn.execute(
            "SELECT * FROM reading WHERE indicator_id = ? AND date = '2026-10-09'", (iid,)
        ).fetchone()
        assert row["base_gap"] == 0
    finally:
        conn.close()


def test_run_report_failures_property(tmp_path: Path, config: AppConfig) -> None:
    errors = {i.slug: "x" for i in config.indicators}
    report = run_etl(
        config,
        deps=make_deps(config, {}, errors=errors),
        run_date=date(2026, 10, 8),
        db_path=str(tmp_path / "t.db"),
    )
    assert len(report.failures) == len(config.indicators)
    assert isinstance(connect(str(tmp_path / "t.db")), sqlite3.Connection)


def test_report_to_dict_shape(tmp_path: Path, config: AppConfig) -> None:
    values = live_values(config)
    values["dolar_oficial_manana"] = 3250.0
    report = run_etl(
        config,
        deps=make_deps(config, values, errors={"euro": "timeout"}),
        run_date=date(2026, 10, 8),
        db_path=str(tmp_path / "t.db"),
    )
    payload = report_to_dict(report)
    assert payload["ok"] == 17
    assert payload["run_date"] == "2026-10-08"
    failures = payload["failures"]
    assert isinstance(failures, list)
    assert len(failures) == 1
    assert failures[0]["slug"] == "euro"
    assert failures[0]["forward_filled_value"] is None  # no previous reading
    assert "results" in payload


def test_source_exception_is_contained(tmp_path: Path, config: AppConfig) -> None:
    class Exploding:
        name = "boom"

        def fetch(self, indicators, source, settings, client):  # type: ignore[no-untyped-def]
            raise RuntimeError("kaboom")

    deps = PipelineDeps(adapters=dict.fromkeys(config.sources, Exploding()))
    report = run_etl(config, deps=deps, run_date=date(2026, 10, 8), db_path=str(tmp_path / "t.db"))
    assert report.exit_code == 1
    assert all("source failure" in (r.error or "") for r in report.results)


def test_format_report_contains_run_id(tmp_path: Path, config: AppConfig) -> None:
    from etl.pipeline import format_report

    values = live_values(config)
    values["dolar_oficial_manana"] = 3250.0
    report = run_etl(
        config,
        deps=make_deps(config, values),
        run_date=date(2026, 10, 8),
        db_path=str(tmp_path / "t.db"),
        run_id="fixed-run-id",
    )
    text = format_report(report)
    assert "fixed-run-id" in text
    assert "ok=18" in text


def test_local_run_date_handles_naive_datetime() -> None:
    from datetime import datetime

    from etl.models import Settings
    from etl.pipeline import local_run_date

    settings = Settings(
        timezone="America/Bogota",
        history_days=31,
        database="x",
        max_gap_days=7,
        politeness_seconds=0.0,
        request_timeout=5,
        artifacts_dir="a",
        user_agents=("ua",),
    )
    assert local_run_date(settings, datetime(2026, 10, 8, 12, 0)).isoformat() == "2026-10-08"


def test_make_run_id_unique() -> None:
    from etl.pipeline import make_run_id

    assert make_run_id() != make_run_id()
