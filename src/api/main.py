"""FastAPI read-only API over the indicators SQLite database.

Two read modes share the same model:
  * this app (``GET /api/v1/...``), and
  * the static JSON contract published on GitHub Pages (``scripts/build_pages.py``).

The database is opened in SQLite URI read-only mode.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date as Date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from etl.config import load_config
from etl.contract import (
    catalog_entry,
    history_payload,
    latest_payload,
    variation_payload,
)
from etl.storage import connect

DEFAULT_DB = "data/indicadores.db"
DEFAULT_CONFIG = "config/indicators.yaml"


def _db_path() -> Path:
    return Path(os.environ.get("INDICADORES_DB", DEFAULT_DB))


def _limits() -> tuple[int, int]:
    """(max_gap_days, history_days) from the declarative config, with fallback."""

    try:
        config = load_config(os.environ.get("INDICADORES_CONFIG", DEFAULT_CONFIG))
        return config.settings.max_gap_days, config.settings.history_days
    except Exception:  # noqa: BLE001 - API must boot even without the YAML
        return 7, 31


def get_conn() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    if not path.exists():
        raise HTTPException(status_code=503, detail="indicators database not available")
    conn = connect(path, read_only=True)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


class LatestPayload(BaseModel):
    """Latest reading for one indicator, with day-over-day variation."""

    slug: str
    date: Date
    value: float
    value_published: float | None = None
    unit: str
    unit_as_published: str = ""
    status: str
    base_gap: bool
    source_date: Date | None = None
    variation_pct: float | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "slug": "dolar_oficial_hoy",
                    "date": "2026-10-08",
                    "value": 3238.88,
                    "value_published": None,
                    "unit": "COP",
                    "unit_as_published": "",
                    "status": "ok",
                    "base_gap": True,
                    "source_date": "2026-10-08",
                    "variation_pct": None,
                },
                {
                    "slug": "ibr_overnight",
                    "date": "2026-10-08",
                    "value": 11.407,
                    "value_published": 12.259,
                    "unit": "% nominal anual",
                    "unit_as_published": "%",
                    "status": "ok",
                    "base_gap": True,
                    "source_date": "2026-10-08",
                    "variation_pct": None,
                },
                {
                    "slug": "gas_ttf_nl",
                    "date": "2026-10-08",
                    "value": 77.205,
                    "value_published": None,
                    "unit": "EUR/MWh",
                    "unit_as_published": "Valores en EUR",
                    "status": "ok",
                    "base_gap": True,
                    "source_date": "2026-10-08",
                    "variation_pct": None,
                },
            ]
        }
    )


class IndicatorSummary(BaseModel):
    slug: str
    source_url: str
    visible_label: str
    unit: str
    latest: LatestPayload | None = None


class IndicatorsResponse(BaseModel):
    generated_at: datetime
    indicators: list[IndicatorSummary]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "generated_at": "2026-10-08T19:00:00Z",
                    "indicators": [
                        {
                            "slug": "dolar_oficial_hoy",
                            "source_url": "https://www.larepublica.co/indicadores-economicos",
                            "visible_label": "DÓLAR OFICIAL HOY",
                            "unit": "COP",
                            "latest": {
                                "slug": "dolar_oficial_hoy",
                                "date": "2026-10-08",
                                "value": 3238.88,
                                "unit": "COP",
                                "status": "ok",
                                "base_gap": True,
                            },
                        }
                    ],
                }
            ]
        }
    )


class HistoryResponse(BaseModel):
    slug: str
    unit: str
    from_date: Date
    to_date: Date
    readings: list[LatestPayload]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "slug": "dolar_oficial_hoy",
                    "unit": "COP",
                    "from_date": "2026-09-08",
                    "to_date": "2026-10-08",
                    "readings": [
                        {
                            "slug": "dolar_oficial_hoy",
                            "date": "2026-10-08",
                            "value": 3238.88,
                            "unit": "COP",
                            "status": "ok",
                            "base_gap": False,
                            "variation_pct": 0.71,
                        }
                    ],
                }
            ]
        }
    )


class VariationResponse(BaseModel):
    slug: str
    date: Date
    value: float
    previous_date: Date | None
    previous_value: float | None
    variation_pct: float | None
    base_gap: bool

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "slug": "ibr_overnight",
                    "date": "2026-10-08",
                    "value": 11.407,
                    "previous_date": "2026-10-07",
                    "previous_value": 11.411,
                    "variation_pct": -0.0351,
                    "base_gap": False,
                }
            ]
        }
    )


app = FastAPI(
    title="Indicadores Económicos API",
    version="1.0.0",
    description=(
        "API de solo lectura sobre el histórico de 31 días del ETL diario de "
        "indicadores económicos. El contrato estático equivalente se publica en "
        "GitHub Pages (`/api/v1/`)."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _indicator_or_404(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM indicator WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown indicator: {slug}")
    return row  # type: ignore[no-any-return]


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"name": "indicadores-api", "docs": "/docs", "contract": "/api/v1/indicators"}


@app.get("/api/v1/indicators", response_model=IndicatorsResponse)
def list_indicators(conn: Conn) -> IndicatorsResponse:
    max_gap_days, _ = _limits()
    rows = conn.execute("SELECT * FROM indicator ORDER BY slug").fetchall()
    return IndicatorsResponse(
        generated_at=datetime.now(UTC),
        indicators=[
            IndicatorSummary.model_validate(catalog_entry(conn, row, max_gap_days)) for row in rows
        ],
    )


@app.get("/api/v1/indicators/{slug}/latest", response_model=LatestPayload)
def get_latest(slug: str, conn: Conn) -> LatestPayload:
    indicator = _indicator_or_404(conn, slug)
    max_gap_days, _ = _limits()
    payload = latest_payload(conn, indicator, max_gap_days)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"no readings for {slug}")
    return LatestPayload.model_validate(payload)


@app.get("/api/v1/indicators/{slug}/history", response_model=HistoryResponse)
def get_history(
    slug: str,
    conn: Conn,
    from_date: Annotated[Date | None, Query(alias="from")] = None,
    to_date: Annotated[Date | None, Query(alias="to")] = None,
) -> HistoryResponse:
    indicator = _indicator_or_404(conn, slug)
    max_gap_days, history_days = _limits()
    return HistoryResponse.model_validate(
        history_payload(conn, indicator, max_gap_days, history_days, from_date, to_date)
    )


@app.get("/api/v1/indicators/{slug}/variation", response_model=VariationResponse)
def get_variation(slug: str, conn: Conn) -> VariationResponse:
    indicator = _indicator_or_404(conn, slug)
    max_gap_days, _ = _limits()
    payload = variation_payload(conn, indicator, max_gap_days)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"no readings for {slug}")
    return VariationResponse.model_validate(payload)
