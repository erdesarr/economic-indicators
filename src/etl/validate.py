"""Live validation and human approval workflow.

``make validate`` scrapes everything live, prints a rich table with the exact
columns the owner needs (value | unit | unit_as_published | raw_text |
value_published), asks for approval per source and globally, and on approval:
  (i) recalibrates ``plausible_range`` in config/indicators.yaml,
  (ii) stores offline golden fixtures under tests/golden/<source>/,
  (iii) appends an entry to docs/APPROVALS.md with the fixture hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table

from etl.http import HttpClient
from etl.models import AppConfig, IndicatorConfig, Settings, SourceResult
from etl.pipeline import PipelineDeps
from etl.sources import get_adapter

logger = logging.getLogger(__name__)

GOLDEN_DIR = Path("tests/golden")
APPROVALS_PATH = Path("docs/APPROVALS.md")
DEFAULT_CONFIG_PATH = Path("config/indicators.yaml")

TOLERANCES: dict[str, tuple[str, float]] = {
    "COP": ("relative", 0.10),
    "USD/lb": ("relative", 0.20),
    "USD/bbl": ("relative", 0.20),
    "% E.A.": ("absolute", 2.0),
    "% nominal anual": ("absolute", 2.0),
    "puntos": ("relative", 0.20),
    "BRL por USD": ("relative", 0.15),
    "EUR/MWh": ("relative", 0.30),
    "USD/MMBtu": ("relative", 0.30),
}


class RecordingClient(HttpClient):
    """HttpClient that records every raw payload (for golden fixtures)."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.texts: dict[str, str] = {}
        self.jsons: dict[str, Any] = {}

    def get_text(self, url: str, retries: int = 3) -> str:
        text = super().get_text(url, retries)
        self.texts[url] = text
        return text

    def get_json(self, url: str, retries: int = 3) -> Any:
        payload = super().get_json(url, retries)
        self.jsons[url] = payload
        return payload


def collect_live(
    config: AppConfig,
    client: RecordingClient | None = None,
    deps: PipelineDeps | None = None,
) -> tuple[dict[str, SourceResult], RecordingClient]:
    """Fetch every indicator live without touching the database."""

    deps = deps or PipelineDeps()
    recorder = client or RecordingClient(config.settings)
    results: dict[str, SourceResult] = {}
    for source_name, indicators in config.by_source().items():
        source_cfg = config.sources[source_name]
        adapter = (deps.adapters or {}).get(source_name) or get_adapter(source_name)
        try:
            results.update(adapter.fetch(indicators, source_cfg, config.settings, recorder))
        except Exception as exc:  # noqa: BLE001 - report per indicator instead of crashing
            logger.exception("source %s failed during validation", source_name)
            for indicator in indicators:
                results.setdefault(
                    indicator.slug,
                    SourceResult(slug=indicator.slug, error=f"source failure: {exc}"),
                )
    return results, recorder


def _clip(text: str, size: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= size else text[: size - 1] + "…"


def build_table(
    config: AppConfig,
    results: Mapping[str, SourceResult],
    previous_values: Mapping[str, float | None] | None = None,
) -> Table:
    table = Table(title="Validación en vivo de indicadores", show_lines=True)
    table.add_column("Indicador", style="bold")
    table.add_column("Label encontrado")
    table.add_column("value", justify="right")
    table.add_column("unit")
    table.add_column("unit_as_published")
    table.add_column("raw_text")
    table.add_column("value_published", justify="right")
    table.add_column("vs día anterior", justify="right")
    table.add_column("status")

    previous_values = previous_values or {}
    for indicator in config.indicators:
        result = results.get(indicator.slug) or SourceResult(slug=indicator.slug, error="no result")
        status = "[green]ok[/green]" if result.ok else f"[red]error: {result.error}[/red]"
        previous = previous_values.get(indicator.slug)
        if result.ok and previous not in (None, 0):
            pct = (result.value - previous) / previous * 100.0  # type: ignore[operator]
            variation = f"{pct:+.2f}%"
        else:
            variation = "—"
        table.add_row(
            indicator.slug,
            _clip(result.raw_label or "—", 34),
            f"{result.value:g}" if result.value is not None else "—",
            indicator.unit,
            result.unit_as_published or "—",
            _clip(result.raw_text or "—"),
            f"{result.value_published:g}" if result.value_published is not None else "—",
            variation,
            status,
        )
    return table


def raw_fragment(source_name: str, url: str, label: str, recorder: RecordingClient) -> str:
    """Return the raw HTML/JSON fragment around a label for troubleshooting."""

    if url in recorder.texts:
        html = recorder.texts[url]
        index = html.upper().find(label.upper())
        if index >= 0:
            start = max(0, index - 400)
            return html[start : index + 600]
        return html[:800]
    if url in recorder.jsons:
        return json.dumps(recorder.jsons[url], ensure_ascii=False)[:1200]
    return "(no raw payload recorded)"


def tolerance_for(unit: str) -> tuple[str, float]:
    return TOLERANCES.get(unit, ("relative", 0.20))


def recalibrated_range(indicator: IndicatorConfig, value: float) -> list[float]:
    kind, amount = tolerance_for(indicator.unit)
    if kind == "absolute":
        low, high = value - amount, value + amount
    else:
        low, high = value * (1 - amount), value * (1 + amount)
    if indicator.unit in {"COP", "puntos"}:
        return [float(round(low)), float(round(high))]
    return [round(low, 4), round(high, 4)]


def update_ranges(
    config_path: Path, config: AppConfig, results: Mapping[str, SourceResult]
) -> None:
    """Rewrite plausible_range for each indicator with an approved value."""

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for entry in raw["indicators"]:
        result = results.get(entry["slug"])
        if result is None or not result.ok:
            continue
        indicator = config.by_slug(entry["slug"])
        entry["plausible_range"] = recalibrated_range(indicator, result.value or 0.0)
    config_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


def _trim_banrep(payload: Any, keep: int = 10) -> Any:
    if not isinstance(payload, list):
        return payload
    trimmed = []
    for series in payload:
        if not isinstance(series, dict):
            continue
        copy = dict(series)
        data = copy.get("data")
        if isinstance(data, list):
            copy["data"] = data[-keep:]
        trimmed.append(copy)
    return trimmed


def _payload_by_host(recorder: RecordingClient, host_fragment: str) -> Any | None:
    for url, payload in recorder.jsons.items():
        if host_fragment in url:
            return payload
    return None


def save_fixtures(
    config: AppConfig,
    results: Mapping[str, SourceResult],
    recorder: RecordingClient,
    golden_dir: Path = GOLDEN_DIR,
) -> list[Path]:
    """Persist offline golden fixtures and expected values."""

    written: list[Path] = []
    fixture_names = {
        "larepublica_main": "main.html",
        "larepublica_dtf": "dtf.html",
        "larepublica_igbc": "igbc.html",
    }
    by_dir: dict[str, list[IndicatorConfig]] = {}
    for indicator in config.indicators:
        by_dir.setdefault(indicator.source.split("_")[0], []).append(indicator)

    for dir_name, indicators in by_dir.items():
        source_dir = golden_dir / dir_name
        source_dir.mkdir(parents=True, exist_ok=True)
        expected: dict[str, Any] = {
            "source": dir_name,
            "captured_at": datetime.now(UTC).isoformat(),
            "indicators": {},
        }
        seen_files: set[Path] = set()
        for indicator in indicators:
            result = results.get(indicator.slug)
            if result is None or not result.ok:
                continue
            expected["indicators"][indicator.slug] = {
                "label": result.raw_label,
                "value": result.value,
                "unit": indicator.unit,
                "unit_as_published": result.unit_as_published,
                "raw_text": result.raw_text,
                "value_published": result.value_published,
                "source_date": result.source_date.isoformat() if result.source_date else None,
            }
            if indicator.source.startswith("larepublica"):
                html = recorder.texts.get(indicator.url)
                if html is not None:
                    path = source_dir / fixture_names.get(indicator.source, "main.html")
                    if path not in seen_files:
                        path.write_text(html, encoding="utf-8")
                        written.append(path)
                        seen_files.add(path)
            elif indicator.source == "banrep":
                payload = _payload_by_host(recorder, "banrep.gov.co")
                if payload is not None:
                    path = source_dir / "series.json"
                    if path not in seen_files:
                        path.write_text(
                            json.dumps(_trim_banrep(payload), ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        written.append(path)
                        seen_files.add(path)
            elif indicator.source == "bcb":
                payload = _payload_by_host(recorder, "bcb.gov.br")
                if payload is not None:
                    path = source_dir / "sgs_10813.json"
                    if path not in seen_files:
                        path.write_text(
                            json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        written.append(path)
                        seen_files.add(path)
            elif indicator.source == "investing":
                artifact = Path(config.settings.artifacts_dir) / f"{indicator.slug}.html"
                if artifact.exists():
                    target = source_dir / f"{indicator.slug}.html"
                    if target not in seen_files:
                        target.write_text(artifact.read_text(encoding="utf-8"), encoding="utf-8")
                        written.append(target)
                        seen_files.add(target)

        expected_path = source_dir / "expected.json"
        expected_path.write_text(
            json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written.append(expected_path)
    return written


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def record_approval(
    config_path: Path,
    config: AppConfig,
    results: Mapping[str, SourceResult],
    fixture_paths: Sequence[Path],
    approvals_path: Path = APPROVALS_PATH,
    approved_by: str = "dueño del proyecto",
) -> None:
    """Append the approval entry (date + expected.json hashes)."""

    approvals_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"\n## {date.today().isoformat()} — validación en vivo aprobada",
        "",
        f"- Aprobado por: {approved_by}",
        f"- Configuración: `{config_path.as_posix()}`",
        f"- Indicadores aprobados: {sum(1 for r in results.values() if r.ok)}",
        "- Hashes SHA-256 de fixtures esperados:",
    ]
    for path in fixture_paths:
        if path.name == "expected.json":
            lines.append(f"  - `{path.as_posix()}` — `{_sha256(path)}`")
    if not approvals_path.exists():
        approvals_path.write_text(
            "# Aprobaciones del dueño\n\nRegistro de validaciones humanas del ETL.\n",
            encoding="utf-8",
        )
    with approvals_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def previous_values_from_db(config: AppConfig, run_date: date) -> dict[str, float | None]:
    """Load the most recent stored value per indicator (for the vs column)."""

    from etl.storage import connect, previous_reading

    values: dict[str, float | None] = {}
    db_path = Path(config.settings.database)
    if not db_path.exists():
        return values
    conn = connect(db_path, read_only=True)
    try:
        for indicator in config.indicators:
            row = conn.execute(
                "SELECT id FROM indicator WHERE slug = ?", (indicator.slug,)
            ).fetchone()
            if row is None:
                continue
            previous = previous_reading(
                conn, int(row["id"]), run_date, config.settings.max_gap_days
            )
            values[indicator.slug] = float(previous["value"]) if previous else None
    finally:
        conn.close()
    return values


def console() -> Console:
    return Console()
