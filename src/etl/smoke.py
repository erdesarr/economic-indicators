"""Smoke validation of the ETL run report (no live re-scrape).

Reading ``artifacts/run_report.json`` avoids false negatives when GitHub delays
the scheduled cron (e.g. a 21:00 Bogota run landing after midnight, when the
site no longer publishes the "tomorrow" block). The ETL itself already reports
forward-fill/degraded data via its Healthchecks ping, so a valid forward_filled
row must pass here instead of duplicating the alert.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# "fallback" is accepted defensively (form-updater source); the ETL report
# emits ok/forward_filled/manual/failed.
ALLOWED_STATUSES = {"ok", "forward_filled", "manual", "fallback"}


class SmokeError(RuntimeError):
    """Raised when the run report cannot be read."""


def load_run_report(path: Path | str) -> dict[str, Any]:
    report_path = Path(path)
    if not report_path.exists():
        raise SmokeError(f"run report not found: {report_path}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SmokeError(f"cannot read run report {report_path}: {exc}") from exc
    if not isinstance(report, dict):
        raise SmokeError("run report root must be an object")
    return report


def validate_run_report(report: dict[str, Any], expected_slugs: set[str]) -> list[str]:
    """Return a list of problems; empty means the ETL output is healthy."""

    results = report.get("results")
    if not isinstance(results, list):
        return ["run report has no 'results' list"]

    by_slug: dict[str, dict[str, Any]] = {}
    for result in results:
        if isinstance(result, dict) and result.get("slug"):
            by_slug[str(result["slug"])] = result

    errors: list[str] = []
    missing = sorted(expected_slugs - set(by_slug))
    if missing:
        errors.append(f"missing indicators in run report: {missing}")
    if len(results) < len(expected_slugs):
        errors.append(
            f"run report has {len(results)} results, expected at least {len(expected_slugs)}"
        )
    for slug, result in by_slug.items():
        status = result.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{slug}: status {status!r} is not one of {sorted(ALLOWED_STATUSES)}")
    return errors
