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
    expected: bool = False
    publication_window: str = ""


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

    Rules:
      * forward-fill/failure INSIDE the indicator's publication window is an
        unexpected source failure -> /fail body table;
      * forward-fill/failure OUTSIDE its publication window is expected (source
        not published yet) -> /success body warning;
      * stale warnings without failures are reported in a SUCCESS body.
    """

    failures = [FailureItem(**item) for item in report.get("failures", [])]
    stale = [FailureItem(**item) for item in report.get("stale_warnings", [])]
    unexpected = [item for item in failures if not item.expected]
    expected = [item for item in failures if item.expected]
    lines = [
        f"run_id={report.get('run_id')} date={report.get('run_date')}",
        (
            f"ok={report.get('ok', 0)} failures={len(unexpected)} "
            f"expected={len(expected)} stale={len(stale)} "
            f"pruned={report.get('pruned_rows', 0)}"
        ),
    ]
    if unexpected:
        lines.append("")
        lines.append("**Fallos (forward-fill inesperado dentro de la ventana):**")
        lines.append(build_failure_table_markdown(unexpected))
    if expected:
        lines.append("")
        lines.append(
            "**Aviso: forward-fill esperado fuera de ventana de publicación (no es fallo):**"
        )
        for item in expected:
            window = item.publication_window or "sin ventana"
            lines.append(f"- {item.slug} ({item.source}, ventana {window}): {item.error}")
    if stale:
        lines.append("")
        lines.append("**Aviso: dato stale (source_date repetida, sin failures):**")
        for item in stale:
            lines.append(f"- {item.slug} ({item.source}): {item.error}")
    return "\n".join(lines)
