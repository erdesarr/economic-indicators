"""Smoke report validation tests (no network)."""

from __future__ import annotations

from etl.smoke import ALLOWED_STATUSES, validate_run_report

SLUGS = {f"ind_{index}" for index in range(18)}


def report_with(statuses: list[str]) -> dict:
    return {
        "run_id": "test",
        "results": [
            {"slug": f"ind_{index}", "status": status} for index, status in enumerate(statuses)
        ],
    }


def test_full_report_with_mixed_statuses_passes() -> None:
    # 18 indicators: mix of ok / forward_filled (fallback is not an ETL status).
    statuses = ["ok"] * 12 + ["forward_filled"] * 6
    assert validate_run_report(report_with(statuses), SLUGS) == []


def test_forward_filled_is_valid_from_the_smoke_perspective() -> None:
    assert "forward_filled" in ALLOWED_STATUSES


def test_report_with_missing_indicator_fails() -> None:
    statuses = ["ok"] * 17
    errors = validate_run_report(report_with(statuses), SLUGS)
    assert any("missing indicators" in error for error in errors)


def test_report_with_fewer_results_fails() -> None:
    report = report_with(["ok"] * 10)
    errors = validate_run_report(report, SLUGS)
    assert any("expected at least 18" in error for error in errors)
    assert any("missing indicators" in error for error in errors)


def test_failed_status_fails_smoke() -> None:
    statuses = ["ok"] * 17 + ["failed"]
    errors = validate_run_report(report_with(statuses), SLUGS)
    assert any("status 'failed'" in error for error in errors)


def test_missing_results_list_fails() -> None:
    assert validate_run_report({"run_id": "x"}, SLUGS) == ["run report has no 'results' list"]


def test_empty_report_fails() -> None:
    errors = validate_run_report({"results": []}, SLUGS)
    assert errors
