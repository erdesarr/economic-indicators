"""Declarative configuration loading for the ETL."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from etl.models import (
    AppConfig,
    IndicatorConfig,
    SeriesMapping,
    Settings,
    SourceConfig,
)

DEFAULT_CONFIG_PATH = Path("config/indicators.yaml")

VALID_METHODS = {"api", "html", "browser"}
VALID_FORMATS = {"es-CO", "es-ES", "json-dot"}


class ConfigError(ValueError):
    """Raised when the declarative configuration is invalid."""


def _require(data: dict[str, Any], key: str, context: str) -> Any:
    if key not in data:
        raise ConfigError(f"missing key '{key}' in {context}")
    return data[key]


def _parse_settings(raw: dict[str, Any]) -> Settings:
    return Settings(
        timezone=str(_require(raw, "timezone", "settings")),
        history_days=int(_require(raw, "history_days", "settings")),
        database=str(_require(raw, "database", "settings")),
        max_gap_days=int(_require(raw, "max_gap_days", "settings")),
        politeness_seconds=float(_require(raw, "politeness_seconds", "settings")),
        request_timeout=int(_require(raw, "request_timeout", "settings")),
        artifacts_dir=str(_require(raw, "artifacts_dir", "settings")),
        user_agents=tuple(str(ua) for ua in _require(raw, "user_agents", "settings")),
    )


def _parse_sources(raw: dict[str, Any]) -> dict[str, SourceConfig]:
    sources: dict[str, SourceConfig] = {}
    for name, body in raw.items():
        method = str(_require(body, "method", f"source {name}"))
        if method not in VALID_METHODS:
            raise ConfigError(f"source {name}: invalid method '{method}'")
        number_format = str(_require(body, "number_format", f"source {name}"))
        if number_format not in VALID_FORMATS:
            raise ConfigError(f"source {name}: invalid number_format '{number_format}'")
        sources[name] = SourceConfig(
            name=name,
            method=method,
            number_format=number_format,
            retries=int(body.get("retries", 3)),
            warmup_url=body.get("warmup_url"),
        )
    return sources


def _parse_series(raw: Any, slug: str) -> SeriesMapping | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or "nominal" not in raw:
        raise ConfigError(f"indicator {slug}: source_series requires a 'nominal' id")
    published = raw.get("published")
    return SeriesMapping(
        nominal=int(raw["nominal"]),
        published=int(published) if published is not None else None,
    )


def _parse_indicators(
    raw: list[dict[str, Any]], sources: dict[str, SourceConfig]
) -> tuple[IndicatorConfig, ...]:
    indicators: list[IndicatorConfig] = []
    seen: set[str] = set()
    for body in raw:
        slug = str(_require(body, "slug", "indicator"))
        if slug in seen:
            raise ConfigError(f"duplicate indicator slug: {slug}")
        seen.add(slug)
        source_name = str(_require(body, "source", f"indicator {slug}"))
        if source_name not in sources:
            raise ConfigError(f"indicator {slug}: unknown source '{source_name}'")
        source = sources[source_name]
        plausible = _require(body, "plausible_range", f"indicator {slug}")
        if not isinstance(plausible, list) or len(plausible) != 2:
            raise ConfigError(f"indicator {slug}: plausible_range must be [min, max]")
        low, high = float(plausible[0]), float(plausible[1])
        if low >= high:
            raise ConfigError(f"indicator {slug}: plausible_range min >= max")
        indicators.append(
            IndicatorConfig(
                slug=slug,
                source=source_name,
                url=str(_require(body, "url", f"indicator {slug}")),
                visible_label=str(_require(body, "visible_label", f"indicator {slug}")),
                unit=str(_require(body, "unit", f"indicator {slug}")),
                unit_published=str(body.get("unit_published", "")),
                plausible_range=(low, high),
                method=str(body.get("method", source.method)),
                number_format=str(body.get("number_format", source.number_format)),
                retries=int(body.get("retries", source.retries)),
                stale_check=bool(body.get("stale_check", False)),
                source_series=_parse_series(body.get("source_series"), slug),
                verification=body.get("verification"),
            )
        )
    return tuple(indicators)


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load and validate the declarative configuration."""

    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"configuration file not found: {config_path}")
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a mapping")
    sources = _parse_sources(_require(raw, "sources", "root"))
    indicators = _parse_indicators(_require(raw, "indicators", "root"), sources)
    if not indicators:
        raise ConfigError("no indicators configured")
    return AppConfig(
        settings=_parse_settings(_require(raw, "settings", "root")),
        sources=sources,
        indicators=indicators,
    )
