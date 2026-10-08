"""Core domain models for the economic indicators ETL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date

from etl.alerts.summary import FailureItem


@dataclass(frozen=True)
class SeriesMapping:
    """Maps an indicator to its source series ids (e.g. IBR nominal/effective)."""

    nominal: int
    published: int | None = None


@dataclass(frozen=True)
class IndicatorConfig:
    slug: str
    source: str
    url: str
    visible_label: str
    unit: str
    unit_published: str
    plausible_range: tuple[float, float]
    method: str
    number_format: str
    retries: int
    stale_check: bool = False
    source_series: SeriesMapping | None = None
    verification: str | None = None

    def in_range(self, value: float) -> bool:
        low, high = self.plausible_range
        return low <= value <= high


@dataclass
class SourceResult:
    """Raw extraction result for a single indicator before persistence."""

    slug: str
    value: float | None = None
    value_published: float | None = None
    unit_as_published: str = ""
    raw_label: str = ""
    raw_text: str = ""
    source_date: Date | None = None
    error: str | None = None
    artifact_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.value is not None


@dataclass(frozen=True)
class Settings:
    timezone: str
    history_days: int
    database: str
    max_gap_days: int
    politeness_seconds: float
    request_timeout: int
    artifacts_dir: str
    user_agents: tuple[str, ...]


@dataclass(frozen=True)
class SourceConfig:
    name: str
    method: str
    number_format: str
    retries: int
    warmup_url: str | None = None


@dataclass(frozen=True)
class AppConfig:
    settings: Settings
    sources: dict[str, SourceConfig]
    indicators: tuple[IndicatorConfig, ...]

    def by_slug(self, slug: str) -> IndicatorConfig:
        for indicator in self.indicators:
            if indicator.slug == slug:
                return indicator
        raise KeyError(f"unknown indicator slug: {slug}")

    def by_source(self) -> dict[str, list[IndicatorConfig]]:
        grouped: dict[str, list[IndicatorConfig]] = {}
        for indicator in self.indicators:
            grouped.setdefault(indicator.source, []).append(indicator)
        return grouped


@dataclass(frozen=True)
class Reading:
    """A persisted reading row (status ok/forward_filled/manual)."""

    indicator_id: int
    date: Date
    value: float
    value_published: float | None
    unit_as_published: str
    raw_label: str
    raw_text: str
    source_date: Date | None
    status: str
    base_gap: bool
    reason: str | None
    created_at: str
    run_id: str


@dataclass
class IndicatorRun:
    """Outcome of processing a single indicator in one run."""

    slug: str
    status: str
    value: float | None
    value_published: float | None
    error: str | None = None
    forward_filled: bool = False
    stale_warning: bool = False
    source: str = ""


@dataclass
class RunReport:
    run_id: str
    run_date: Date
    results: list[IndicatorRun] = field(default_factory=list)
    failures_detail: list[FailureItem] = field(default_factory=list)
    stale_detail: list[FailureItem] = field(default_factory=list)
    pruned_rows: int = 0

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.status == "ok")

    @property
    def failures(self) -> list[IndicatorRun]:
        return [r for r in self.results if r.status in {"forward_filled", "failed"}]

    @property
    def stale_warnings(self) -> list[IndicatorRun]:
        return [r for r in self.results if r.stale_warning]

    @property
    def exit_code(self) -> int:
        return 0 if self.ok_count >= 1 else 1
