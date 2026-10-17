"""Web form selection/capture unit tests (no browser)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from form_updater.webform import (
    VALUE_INPUT_NAME,
    WebFormClient,
    WebFormError,
    load_records,
    record_error,
    save_records,
)


def test_record_match() -> None:
    records = {"3": "EURO", "40": "USD/BRL"}
    assert record_error(3, "EURO", records) is None
    assert record_error(3, "  EURO  ", records) is None


def test_record_mismatch_blocks_write() -> None:
    records = {"3": "EURO"}
    error = record_error(3, "OTRO", records)
    assert error is not None
    assert "write skipped" in error


def test_record_missing_blocks_write() -> None:
    error = record_error(99, "X", {"3": "EURO"})
    assert error is not None
    assert "missing from golden" in error


def test_records_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    save_records(path, {"40": "USD/BRL", "2": "TRM", "34": "TRM MAÑANA"})
    records = load_records(path)
    assert records == {"2": "TRM", "34": "TRM MAÑANA", "40": "USD/BRL"}
    assert list(records) == ["2", "34", "40"]  # sorted by id
    assert json.loads(path.read_text(encoding="utf-8"))["34"] == "TRM MAÑANA"


def test_records_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WebFormError):
        load_records(tmp_path / "nope.json")


def test_base_url_with_querystring_is_rejected() -> None:
    with pytest.raises(WebFormError):
        WebFormClient("https://example.test/Edit.aspx?x=1", {})


class StubClient(WebFormClient):
    """Replaces Playwright with an in-memory fake form."""

    def __init__(self, records: dict[str, str], nombre: str, current: str = "") -> None:
        super().__init__("https://example.test/Edit.aspx", records, courtesy_seconds=0)
        self.nombre = nombre
        self.current = current
        self.fills: list[str] = []
        self.gotos: list[int] = []
        self.set_fill: str | None = None

    def _goto(self, id_indicador: int) -> None:
        self.gotos.append(id_indicador)

    def read_nombre(self) -> str:
        return self.nombre

    def read_value(self) -> str:
        return self.current

    def _fill_value(self, value: str, timeout_seconds: float = 10.0) -> None:
        self.fills.append(value)
        self.current = value

    def _click_update(self) -> None:
        return None


def test_write_skips_on_nombre_mismatch() -> None:
    client = StubClient({"3": "EURO"}, nombre="OTRO")
    outcome = client.write_record(3, "$ 3.607,58 +0,15%")
    assert outcome.ok is False
    assert "write skipped" in (outcome.error or "")
    assert client.fills == []  # nothing was typed


def test_write_fills_and_verifies() -> None:
    client = StubClient({"3": "EURO"}, nombre="EURO", current="$ 3.500,00")
    outcome = client.write_record(3, "$ 3.607,58 +0,15%")
    assert outcome.ok is True
    assert outcome.previous_value == "$ 3.500,00"
    assert outcome.new_value == "$ 3.607,58 +0,15%"
    assert client.fills == ["$ 3.607,58 +0,15%"]
    assert client.gotos == [3]


def test_fixed_value_input_name_contract() -> None:
    assert VALUE_INPUT_NAME.endswith("$__valor$TextBox1")
    assert VALUE_INPUT_NAME.startswith("ctl00$ContentPlaceHolder1$FormView1$")
