"""ETL pipeline orchestration: extract, persist, forward-fill, prune, alert."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from etl.alerts import FailureItem, Sender, SmtpConfig, maybe_send_alert, send_email
from etl.http import HttpClient
from etl.models import (
    AppConfig,
    IndicatorConfig,
    IndicatorRun,
    Reading,
    RunReport,
    Settings,
    SourceResult,
)
from etl.sources import get_adapter
from etl.sources.base import SourceAdapter
from etl.storage import (
    connect,
    indicator_id,
    init_db,
    now_utc_iso,
    previous_reading,
    prune,
    sync_indicators,
    upsert_reading,
)
from etl.variation import compute_variation

logger = logging.getLogger(__name__)


@dataclass
class PipelineDeps:
    adapters: Mapping[str, SourceAdapter] | None = None
    client: HttpClient | None = None
    smtp: SmtpConfig | None = None
    sender: Sender | None = None


def local_run_date(settings: Settings, now: datetime | None = None) -> date:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(settings.timezone)).date()


def make_run_id(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _fetch_source_results(
    indicators: Sequence[IndicatorConfig],
    config: AppConfig,
    deps: PipelineDeps,
    client: HttpClient,
) -> dict[str, SourceResult]:
    source_name = indicators[0].source
    source_cfg = config.sources[source_name]
    adapter = (deps.adapters or {}).get(source_name) or get_adapter(source_name)
    try:
        return adapter.fetch(indicators, source_cfg, config.settings, client)
    except Exception as exc:  # noqa: BLE001 - one source must never kill the run
        logger.exception("source %s failed entirely", source_name)
        return {
            indicator.slug: SourceResult(slug=indicator.slug, error=f"source failure: {exc}")
            for indicator in indicators
        }


def _build_ok_reading(
    indicator_id_value: int,
    result: SourceResult,
    run_date: date,
    run_id: str,
    base_gap: bool,
) -> Reading:
    return Reading(
        indicator_id=indicator_id_value,
        date=run_date,
        value=result.value if result.value is not None else 0.0,
        value_published=result.value_published,
        unit_as_published=result.unit_as_published,
        raw_label=result.raw_label,
        raw_text=result.raw_text,
        source_date=result.source_date,
        status="ok",
        base_gap=base_gap,
        reason=None,
        created_at=now_utc_iso(),
        run_id=run_id,
    )


def _forward_fill_reading(
    indicator_id_value: int,
    previous: sqlite3.Row,
    run_date: date,
    run_id: str,
    reason: str,
) -> Reading:
    return Reading(
        indicator_id=indicator_id_value,
        date=run_date,
        value=float(previous["value"]),
        value_published=(
            float(previous["value_published"]) if previous["value_published"] is not None else None
        ),
        unit_as_published=str(previous["unit_as_published"]),
        raw_label=str(previous["raw_label"]),
        raw_text=str(previous["raw_text"]),
        source_date=(
            date.fromisoformat(str(previous["source_date"])) if previous["source_date"] else None
        ),
        status="forward_filled",
        base_gap=True,
        reason=reason,
        created_at=now_utc_iso(),
        run_id=run_id,
    )


def run_etl(
    config: AppConfig,
    deps: PipelineDeps | None = None,
    run_date: date | None = None,
    db_path: str | None = None,
    run_id: str | None = None,
    run_url: str | None = None,
    send_alerts: bool = True,
) -> RunReport:
    """Execute one ETL run and return the report (exit code included)."""

    deps = deps or PipelineDeps()
    settings = config.settings
    run_day = run_date or local_run_date(settings)
    run_identifier = run_id or make_run_id()
    client = deps.client or HttpClient(settings)

    conn = connect(db_path or settings.database)
    try:
        init_db(conn)
        sync_indicators(conn, config.indicators)
        report = RunReport(run_id=run_identifier, run_date=run_day)
        failures: list[FailureItem] = []
        stale_warnings: list[FailureItem] = []

        for source_name, indicators in config.by_source().items():
            results = _fetch_source_results(indicators, config, deps, client)
            for indicator in indicators:
                result = results.get(indicator.slug) or SourceResult(
                    slug=indicator.slug, error="no result returned by adapter"
                )
                if result.ok and not indicator.in_range(result.value or 0.0):
                    low, high = indicator.plausible_range
                    result.error = (
                        f"value {result.value} outside plausible_range [{low}, {high}] "
                        "(parse failure)"
                    )
                    result.value = None

                iid = indicator_id(conn, indicator.slug)
                previous = previous_reading(conn, iid, run_day, settings.max_gap_days)

                if result.ok:
                    variation = compute_variation(
                        result.value or 0.0,
                        run_day,
                        float(previous["value"]) if previous else None,
                        date.fromisoformat(str(previous["date"])) if previous else None,
                        settings.max_gap_days,
                    )
                    source_date_iso = (
                        result.source_date.isoformat() if result.source_date else None
                    )
                    stale = bool(
                        indicator.stale_check
                        and previous is not None
                        and previous["status"] == "ok"
                        and source_date_iso is not None
                        and previous["source_date"] == source_date_iso
                    )
                    upsert_reading(
                        conn,
                        _build_ok_reading(iid, result, run_day, run_identifier, variation.base_gap),
                    )
                    report.results.append(
                        IndicatorRun(
                            slug=indicator.slug,
                            status="ok",
                            value=result.value,
                            value_published=result.value_published,
                            stale_warning=stale,
                            source=source_name,
                        )
                    )
                    if stale:
                        stale_warnings.append(
                            FailureItem(
                                slug=indicator.slug,
                                source=source_name,
                                error=f"source_date={source_date_iso}",
                                forward_filled_value=result.value,
                                status="stale",
                                stale_warning=True,
                            )
                        )
                else:
                    reason = result.error or "unknown error"
                    if previous is not None:
                        upsert_reading(
                            conn,
                            _forward_fill_reading(iid, previous, run_day, run_identifier, reason),
                        )
                        failures.append(
                            FailureItem(
                                slug=indicator.slug,
                                source=source_name,
                                error=reason,
                                forward_filled_value=float(previous["value"]),
                                status="forward_filled",
                            )
                        )
                        report.results.append(
                            IndicatorRun(
                                slug=indicator.slug,
                                status="forward_filled",
                                value=float(previous["value"]),
                                value_published=(
                                    float(previous["value_published"])
                                    if previous["value_published"] is not None
                                    else None
                                ),
                                error=reason,
                                forward_filled=True,
                                source=source_name,
                            )
                        )
                    else:
                        no_history_reason = (
                            f"{reason} — sin histórico para forward-fill"
                        )
                        failures.append(
                            FailureItem(
                                slug=indicator.slug,
                                source=source_name,
                                error=no_history_reason,
                                forward_filled_value=None,
                                status="failed",
                            )
                        )
                        report.results.append(
                            IndicatorRun(
                                slug=indicator.slug,
                                status="failed",
                                value=None,
                                value_published=None,
                                error=no_history_reason,
                                source=source_name,
                            )
                        )

        report.pruned_rows = prune(conn, settings.history_days, run_day)
        if send_alerts:
            report.email_sent = maybe_send_alert(
                run_day,
                failures,
                stale_warnings,
                run_url=run_url,
                smtp=deps.smtp,
                sender=deps.sender or send_email,
            )
        return report
    finally:
        conn.close()


def format_report(report: RunReport) -> str:
    lines = [
        f"run_id={report.run_id} date={report.run_date.isoformat()}",
        f"ok={report.ok_count} failures={len(report.failures)} "
        f"stale={len(report.stale_warnings)} pruned={report.pruned_rows}",
    ]
    for result in report.results:
        marker = "OK " if result.status == "ok" else "!! "
        lines.append(
            f"{marker}{result.slug}: status={result.status} value={result.value} "
            f"published={result.value_published} error={result.error or ''}"
        )
    return "\n".join(lines)
