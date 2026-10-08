"""Failure/run summary builders (markdown bodies for Healthchecks pings)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FailureItem:
    slug: str
    source: str
    error: str
    forward_filled_value: float | None
    status: str
    stale_warning: bool = False


def format_value(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def build_failure_table_markdown(failures: list[FailureItem]) -> str:
    """Markdown table used as the BODY of the Healthchecks /fail ping."""

    lines = [
        "| Indicador | Fuente | Error | Valor forward-fill | Status |",
        "|---|---|---|---|---|",
    ]
    for item in failures:
        error = item.error.replace("|", "\\|")
        lines.append(
            f"| {item.slug} | {item.source} | {error} | "
            f"{format_value(item.forward_filled_value)} | {item.status} |"
        )
    return "\n".join(lines)


def build_run_body(report: dict[str, Any]) -> str:
    """Body for /success and /fail pings.

    Rule: stale warnings without failures are reported in a SUCCESS ping body
    (never as a failure).
    """

    failures = [FailureItem(**item) for item in report.get("failures", [])]
    stale = [FailureItem(**item) for item in report.get("stale_warnings", [])]
    lines = [
        f"run_id={report.get('run_id')} date={report.get('run_date')}",
        (
            f"ok={report.get('ok', 0)} failures={len(failures)} "
            f"stale={len(stale)} pruned={report.get('pruned_rows', 0)}"
        ),
    ]
    if failures:
        lines.append("")
        lines.append("**Fallos (forward-fill aplicado):**")
        lines.append(build_failure_table_markdown(failures))
    if stale:
        lines.append("")
        lines.append("**Aviso: dato stale (source_date repetida, sin failures):**")
        for item in stale:
            lines.append(f"- {item.slug} ({item.source}): {item.error}")
    return "\n".join(lines)
