"""Contract tests for the FastAPI app against a temporary database."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from etl.models import AppConfig, Reading
from etl.storage import connect, indicator_id, init_db, sync_indicators, upsert_reading


def make_reading(iid: int, day: date, value: float) -> Reading:
    return Reading(
        indicator_id=iid,
        date=day,
        value=value,
        value_published=value * 2,
        unit_as_published="COP",
        raw_label="label",
        raw_text="raw",
        source_date=day,
        status="ok",
        base_gap=False,
        reason=None,
        created_at="2026-10-08T00:00:00Z",
        run_id="api-test",
    )


@pytest.fixture
def seeded_db(tmp_path: Path, config: AppConfig, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "api.db"
    conn = connect(db)
    init_db(conn)
    sync_indicators(conn, config.indicators)
    uvr = indicator_id(conn, "uvr")
    upsert_reading(conn, make_reading(uvr, date(2026, 10, 7), 419.0))
    upsert_reading(conn, make_reading(uvr, date(2026, 10, 8), 419.29))
    dolar = indicator_id(conn, "dolar_oficial_hoy")
    upsert_reading(conn, make_reading(dolar, date(2026, 10, 8), 3238.88))
    conn.close()
    monkeypatch.setenv("INDICADORES_DB", str(db))
    return db


@pytest.fixture
def client(seeded_db: Path) -> TestClient:
    return TestClient(app)


def test_list_indicators(client: TestClient) -> None:
    response = client.get("/api/v1/indicators")
    assert response.status_code == 200
    body = response.json()
    assert len(body["indicators"]) == 18
    first = next(item for item in body["indicators"] if item["slug"] == "uvr")
    assert first["unit"] == "COP"
    assert first["latest"]["value"] == pytest.approx(419.29)
    assert first["latest"]["status"] == "ok"


def test_latest_with_variation(client: TestClient) -> None:
    response = client.get("/api/v1/indicators/uvr/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["value"] == pytest.approx(419.29)
    assert body["value_published"] == pytest.approx(838.58)
    assert body["unit_as_published"] == "COP"
    assert body["base_gap"] is False
    assert body["source_date"] == "2026-10-08"
    assert body["variation_pct"] == pytest.approx((419.29 - 419.0) / 419.0 * 100)


def test_history_range_filter(client: TestClient) -> None:
    response = client.get(
        "/api/v1/indicators/uvr/history", params={"from": "2026-10-07", "to": "2026-10-08"}
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["date"] for item in body["readings"]] == ["2026-10-07", "2026-10-08"]
    assert body["unit"] == "COP"


def test_history_default_window(client: TestClient) -> None:
    response = client.get("/api/v1/indicators/uvr/history")
    assert response.status_code == 200
    body = response.json()
    assert len(body["readings"]) == 2


def test_variation_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/indicators/uvr/variation")
    assert response.status_code == 200
    body = response.json()
    assert body["previous_date"] == "2026-10-07"
    assert body["previous_value"] == pytest.approx(419.0)
    assert body["variation_pct"] == pytest.approx((419.29 - 419.0) / 419.0 * 100)
    assert body["base_gap"] is False


def test_unknown_slug_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/indicators/nope/latest").status_code == 404
    assert client.get("/api/v1/indicators/nope/history").status_code == 404
    assert client.get("/api/v1/indicators/nope/variation").status_code == 404


def test_cors_headers(client: TestClient) -> None:
    response = client.get("/api/v1/indicators", headers={"Origin": "https://example.com"})
    assert response.headers["access-control-allow-origin"] == "*"


def test_openapi_examples_use_real_data(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    latest_examples = schema["components"]["schemas"]["LatestPayload"]["examples"]
    values = {example["slug"]: example["value"] for example in latest_examples}
    assert values["dolar_oficial_hoy"] == 3238.88
    assert values["ibr_overnight"] == 11.407
    assert values["gas_ttf_nl"] == 77.205


def test_missing_database_returns_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDICADORES_DB", str(tmp_path / "missing.db"))
    client = TestClient(app)
    assert client.get("/api/v1/indicators").status_code == 503
