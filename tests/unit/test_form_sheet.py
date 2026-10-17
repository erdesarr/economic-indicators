"""Unit tests for the Google Sheet sync (gviz parsing, normalization, snapshot)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from form_updater.sheet import (
    DEFAULT_SYMBOL,
    SYMBOL_SEPARATOR,
    SheetError,
    fetch_tab,
    finalize_settings,
    parse_bool,
    parse_indicators,
    parse_settings,
    parse_symbol,
    read_snapshot,
    snapshot_text,
    strip_outer_backticks,
    validate_schedule,
    write_snapshot,
)

CATALOG = {"dolar_oficial_hoy", "dolar_oficial_manana", "euro", "uvr", "cafe_colombian_milds"}

HEADER = "indicador,prefijo,sufijo,mostrar_variacion,simbolo_variacion,fallback,id_indicador,activo"

FIXTURE = "\n".join(
    [
        HEADER,
        "dolar_oficial_hoy,`$ `,,FALSE,,,1,FALSE",
        "dolar_oficial_manana,`$ `,,VERDADERO,,dolar_oficial_hoy,2,TRUE",
        "euro,`$ `,,0,,,3,x",
        "uvr,,,,,,5,sí",
        "no_existe_en_catalogo,,,TRUE,,,9,TRUE",
        "euro,,,,,,0,TRUE",
        "euro,,,,,,abc,TRUE",
        "cafe_colombian_milds,`US$ `,` /lb`,no,,no_existe,4,falso",
        "",
    ]
)


def test_boolean_variants() -> None:
    warnings: list[str] = []
    for cell in ("TRUE", "true", "Verdadero", "1", "SI", "sí", "x"):
        assert parse_bool(cell, "ctx", warnings) is True
    for cell in ("FALSE", "false", "Falso", "0", "NO", "no", ""):
        assert parse_bool(cell, "ctx", warnings) is False
    assert warnings == []
    assert parse_bool("quizas", "ctx", warnings) is False
    assert len(warnings) == 1
    assert "unrecognized" in warnings[0]


def test_strip_outer_backticks() -> None:
    assert strip_outer_backticks("`$ `") == "$ "
    assert strip_outer_backticks("` /lb`") == " /lb"
    assert strip_outer_backticks("`%`") == "%"
    assert strip_outer_backticks("US$ ") == "US$ "
    assert strip_outer_backticks("``") == ""
    assert strip_outer_backticks("") == ""


def test_symbol_separator_is_ascii_pipe() -> None:
    assert ord(SYMBOL_SEPARATOR) == 0x007C
    assert SYMBOL_SEPARATOR != "\t"


def test_symbol_variants() -> None:
    warnings: list[str] = []
    assert parse_symbol("+|-", "ctx", warnings) == ("+", "-")
    assert parse_symbol("^|v", "ctx", warnings) == ("^", "v")
    assert parse_symbol("↑|↓", "ctx", warnings) == ("↑", "↓")
    assert parse_symbol("", "ctx", warnings) == DEFAULT_SYMBOL
    assert warnings == []
    # Single token -> up token + "-" down, warning.
    assert parse_symbol("^", "ctx", warnings) == ("^", "-")
    assert len(warnings) == 1
    assert "single token" in warnings[0]
    # Malformed pair -> default + warning.
    assert parse_symbol("a|b|c", "ctx", warnings) == DEFAULT_SYMBOL
    assert len(warnings) == 2
    assert "malformed" in warnings[1]


def test_parse_indicators_valid_rows_and_skips() -> None:
    parsed = parse_indicators(FIXTURE, CATALOG)
    slugs = [(row.indicador, row.id_indicador) for row in parsed.rows]
    assert slugs == [
        ("dolar_oficial_hoy", 1),
        ("dolar_oficial_manana", 2),
        ("euro", 3),
        ("uvr", 5),
        ("cafe_colombian_milds", 4),
    ]
    # Unknown slug skipped; non-positive and non-numeric ids skipped.
    assert sum("unknown indicator slug" in w for w in parsed.warnings) == 1
    assert sum("must be positive" in w for w in parsed.warnings) == 1
    assert sum("invalid id_indicador" in w for w in parsed.warnings) == 1
    # Unknown fallback cleared with warning.
    cafe = parsed.rows[-1]
    assert cafe.fallback == ""
    assert any("not in catalog; cleared" in w for w in parsed.warnings)
    # Backticks stripped, spaces preserved.
    assert cafe.prefijo == "US$ "
    assert cafe.sufijo == " /lb"
    assert parsed.rows[0].prefijo == "$ "
    # Inactive row is KEPT (activo=False pauses writing, not extraction nor
    # fallback availability); active_rows excludes it.
    assert parsed.rows[0].activo is False
    assert parsed.rows[0].indicador == "dolar_oficial_hoy"
    assert all(row.indicador != "dolar_oficial_hoy" for row in parsed.active_rows)
    assert [row.indicador for row in parsed.active_rows if row.indicador == "euro"]


def test_inactive_variable_still_available_as_fallback() -> None:
    """Point 5: activo=FALSE pauses writing but not fallback availability.

    The resolver reads the ETL catalog/latest values, so an inactive row's slug
    must remain a valid fallback target.
    """

    fixture = "\n".join(
        [
            HEADER,
            "euro,,,,,,3,FALSE",
            "uvr,,,,,euro,5,TRUE",
        ]
    )
    parsed = parse_indicators(fixture, CATALOG)
    assert [row.indicador for row in parsed.active_rows] == ["uvr"]
    assert parsed.rows[1].fallback == "euro"
    assert parsed.rows[0].activo is False


def test_legacy_numero_control_alias() -> None:
    fixture = "\n".join(
        [
            "indicador,prefijo,sufijo,mostrar_variacion,simbolo_variacion,"
            "fallback,numero_control,activo",
            "euro,,,FALSE,,,3,TRUE",
        ]
    )
    parsed = parse_indicators(fixture, CATALOG)
    assert parsed.rows[0].id_indicador == 3


def test_unknown_columns_are_reported() -> None:
    fixture = "\n".join(
        [
            HEADER + ",umbral_variacion_pct",
            "euro,,,,,,3,TRUE,30",
        ]
    )
    parsed = parse_indicators(fixture, CATALOG)
    assert parsed.unknown_columns == ["umbral_variacion_pct"]
    assert parsed.rows[0].id_indicador == 3


def test_missing_required_columns_raise() -> None:
    with pytest.raises(SheetError):
        parse_indicators("indicador,prefijo\neuro,$ \n", CATALOG)


def test_parse_settings_key_value() -> None:
    text = '"clave","valor"\n"horario_etl","0 21 * * *"\n"horario_updater","30 21 * * *"\n'
    parsed = parse_settings(text)
    assert parsed.settings["horario_etl"] == "0 21 * * *"
    assert parsed.settings["horario_updater"] == "30 21 * * *"
    assert parsed.unknown_keys == []


def test_parse_settings_unknown_key_warns() -> None:
    text = "clave,valor\nhorario_etl,0 21 * * *\ncolor_favorito,azul\n"
    parsed = parse_settings(text)
    assert parsed.unknown_keys == ["color_favorito"]
    assert any("unknown key" in w for w in parsed.warnings)
    assert "color_favorito" not in parsed.settings


def test_parse_settings_requires_headers() -> None:
    with pytest.raises(SheetError):
        parse_settings("a,b\n1,2\n")


@pytest.mark.parametrize("expression", ["0 21 * * *", "30 21 * * *", "5 3 * * 1", "0 2 * * 0,6"])
def test_validate_schedule_accepts_supported(expression: str) -> None:
    assert validate_schedule(expression) is None


@pytest.mark.parametrize(
    "expression",
    [
        "0 21 * *",
        "0 21 1 * *",
        "0 21 * 1 *",
        "a 21 * * *",
        "61 21 * * *",
        "0 24 * * *",
        "0 21 * * 7",
    ],
)
def test_validate_schedule_rejects(expression: str) -> None:
    assert validate_schedule(expression) is not None


def test_finalize_settings_missing_keys_are_critical() -> None:
    warnings: list[str] = []
    result = finalize_settings(
        {"horario_etl": "0 21 * * *"},
        {"umbral_variacion_pct": 30.0},
        {},
        warnings,
    )
    assert result.settings["horario_etl"] == "0 21 * * *"
    assert result.settings["umbral_variacion_pct"] == "30.0"
    assert "horario_updater" not in result.settings  # no fallback available
    assert any("missing in sheet" in w for w in result.warnings)
    assert any("missing in sheet" in w for w in result.critical)
    assert any("no fallback" in w for w in result.critical)


def test_finalize_settings_uses_last_committed_before_default() -> None:
    warnings: list[str] = []
    result = finalize_settings(
        {},
        {"umbral_variacion_pct": 30.0},
        {"horario_etl": "0 20 * * *", "horario_updater": "15 20 * * *"},
        warnings,
    )
    assert result.settings["horario_etl"] == "0 20 * * *"
    assert result.settings["horario_updater"] == "15 20 * * *"
    assert result.settings["umbral_variacion_pct"] == "30.0"  # bootstrap default
    assert result.critical  # missing key must ping /fail


def test_finalize_settings_invalid_cron_and_threshold() -> None:
    warnings: list[str] = []
    result = finalize_settings(
        {
            "horario_etl": "banana",
            "horario_updater": "30 21 * * *",
            "umbral_variacion_pct": "-5",
        },
        {"umbral_variacion_pct": 30.0},
        {"horario_etl": "0 21 * * *", "umbral_variacion_pct": "25"},
        warnings,
    )
    assert result.settings["horario_etl"] == "0 21 * * *"  # last committed
    assert result.settings["umbral_variacion_pct"] == "25"  # last committed
    assert len(result.critical) == 2


def test_snapshot_roundtrip_and_change_detection(tmp_path: Path) -> None:
    parsed = parse_indicators(FIXTURE, CATALOG)
    settings = {"horario_etl": "0 21 * * *", "umbral_variacion_pct": "30"}
    path = tmp_path / "sheet_config.csv"
    assert write_snapshot(path, parsed.rows, settings, "indicators_config", "ajustes") is True
    rows, read_settings = read_snapshot(path)
    assert read_settings == settings
    assert [(row.indicador, row.id_indicador, row.activo) for row in rows] == [
        (row.indicador, row.id_indicador, row.activo) for row in parsed.rows
    ]
    assert rows[0].prefijo == "$ "
    assert rows[1].fallback == "dolar_oficial_hoy"
    assert rows[1].symbol == "+|-"
    # Idempotent: identical content -> changed False.
    assert write_snapshot(path, parsed.rows, settings, "indicators_config", "ajustes") is False


def test_snapshot_text_contains_both_tabs() -> None:
    text = snapshot_text([], {"horario_etl": "0 21 * * *"}, "indicators_config", "ajustes")
    lines = text.splitlines()
    assert lines[0].startswith("tab,indicador,prefijo")
    fields = lines[1].split(",")
    assert len(fields) == 11
    assert fields[0] == "ajustes"
    assert fields[-2:] == ["horario_etl", "0 21 * * *"]


def _mock_client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_tab_retries_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    responses = [httpx.Response(500), httpx.Response(200, text="ok")]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    assert fetch_tab("https://x/gviz", retries=3, client=_mock_client(handler)) == "ok"


def test_fetch_tab_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(SheetError):
        fetch_tab("https://x/gviz", retries=3, client=_mock_client(handler))


def test_fetch_tab_client_error_is_permanent() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(SheetError):
        fetch_tab("https://x/gviz", retries=3, client=_mock_client(handler))
    assert calls["n"] == 1
