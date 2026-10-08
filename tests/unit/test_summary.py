"""Markdown summary builder tests (Healthchecks ping bodies)."""

from __future__ import annotations

from etl.alerts import FailureItem, build_failure_table_markdown, build_run_body


def failure() -> FailureItem:
    return FailureItem(
        slug="euro",
        source="larepublica_main",
        error="timeout | after retries",
        forward_filled_value=3625.6,
        status="forward_filled",
    )


def test_failure_table_markdown() -> None:
    table = build_failure_table_markdown([failure()])
    lines = table.splitlines()
    assert lines[0] == "| Indicador | Fuente | Error | Valor forward-fill | Status |"
    assert lines[1] == "|---|---|---|---|---|"
    assert lines[2].startswith("| euro | larepublica_main | timeout \\| after retries |")
    assert "3625.6" in lines[2]
    assert lines[2].endswith("| forward_filled |")


def test_failure_table_null_value() -> None:
    item = FailureItem(
        slug="manana",
        source="larepublica_main",
        error="label not found",
        forward_filled_value=None,
        status="failed",
    )
    assert "| — |" in build_failure_table_markdown([item])


def test_run_body_with_failures() -> None:
    body = build_run_body(
        {
            "run_id": "r1",
            "run_date": "2026-10-08",
            "ok": 17,
            "pruned_rows": 2,
            "failures": [failure().__dict__],
            "stale_warnings": [],
        }
    )
    assert "run_id=r1 date=2026-10-08" in body
    assert "ok=17 failures=1 stale=0 pruned=2" in body
    assert "**Fallos (forward-fill aplicado):**" in body


def test_run_body_stale_only_is_success_shaped() -> None:
    stale = FailureItem(
        slug="uvr",
        source="larepublica_main",
        error="source_date=2026-10-07",
        forward_filled_value=419.29,
        status="stale",
        stale_warning=True,
    )
    body = build_run_body(
        {
            "run_id": "r2",
            "run_date": "2026-10-08",
            "ok": 18,
            "pruned_rows": 0,
            "failures": [],
            "stale_warnings": [stale.__dict__],
        }
    )
    assert "failures=0" in body
    assert "stale=1" in body
    assert "dato stale" in body
    assert "Fallos" not in body
