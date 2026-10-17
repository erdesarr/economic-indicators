"""Contract test: Playwright against a mock ASP.NET FormView (Flask).

Verifies navigation with ``?IndicadorID=``, the fixed value input, fill, the
"Actualizar" postback (with __VIEWSTATE/__EVENTVALIDATION), the List.aspx
redirect and post-write verification. No external network.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask, redirect, render_template_string, request
from werkzeug.serving import make_server

from form_updater.webform import VALUE_INPUT_NAME, WebFormClient

pytestmark = pytest.mark.browser

NOMBRE_NAME = "ctl00$ContentPlaceHolder1$FormView1$ctl04$ctl00$__Nombre$TextBox1"
FORMATO_NAME = "ctl00$ContentPlaceHolder1$FormView1$ctl04$ctl01$__Formato$TextBox1"

FORM_HTML = """
<!doctype html>
<html><body>
<h1>Editando Indicadores</h1>
<form id="form1" method="post" action="/Edit.aspx?IndicadorID={{ id }}">
  <input type="hidden" name="__VIEWSTATE" value="mock-viewstate" />
  <input type="hidden" name="__EVENTVALIDATION" value="mock-event-validation" />
  <table>
    <tr><td>Nombre</td><td>
      <input type="text" name="{{ nombre_name }}" value="{{ nombre }}" />
    </td></tr>
    <tr><td>Formato</td><td>
      <input type="text" name="{{ formato_name }}" value="{{ formato }}" />
    </td></tr>
    <tr><td>valor</td><td>
      <input type="text" name="{{ value_name }}" value="{{ valor }}" />
    </td></tr>
    <tr><td colspan="2">
      <input type="submit" value="Actualizar" />
      <a href="/List.aspx">Cancelar</a>
    </td></tr>
  </table>
</form>
</body></html>
"""

STATE: dict[int, dict[str, str]] = {}


def _reset_state() -> None:
    STATE.clear()
    STATE[40] = {"nombre": "USD/BRL", "formato": "{0}", "valor": "R$ 5,0800"}


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/Edit.aspx")
    def edit() -> str:
        id_indicador = int(request.args.get("IndicadorID", "40"))
        record = STATE[id_indicador]
        return render_template_string(
            FORM_HTML,
            id=id_indicador,
            nombre_name=NOMBRE_NAME,
            formato_name=FORMATO_NAME,
            value_name=VALUE_INPUT_NAME,
            **record,
        )

    @app.post("/Edit.aspx")
    def update() -> Any:
        id_indicador = int(request.args.get("IndicadorID", "40"))
        STATE[id_indicador]["valor"] = request.form.get(
            VALUE_INPUT_NAME, STATE[id_indicador]["valor"]
        )
        # Production redirects to the list page after a successful update.
        return redirect("/List.aspx")

    @app.get("/List.aspx")
    def list_page() -> str:
        return "<html><body>Listado</body></html>"

    return app


@pytest.fixture
def form_url() -> Iterator[str]:
    _reset_state()
    server = make_server("127.0.0.1", 0, create_app())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/Edit.aspx"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_capture_then_write_roundtrip(form_url: str) -> None:
    records = {"40": "USD/BRL"}
    with WebFormClient(form_url, records, courtesy_seconds=0) as client:
        captured = client.capture_names([40])
        assert captured == {"40": "USD/BRL"}
        outcome = client.write_record(40, "R$ 5,082 +0,76%")
        assert outcome.ok, outcome.error
        assert outcome.previous_value == "R$ 5,0800"
        assert outcome.new_value == "R$ 5,082 +0,76%"
    assert STATE[40]["valor"] == "R$ 5,082 +0,76%"


def test_wrong_nombre_is_not_written(form_url: str) -> None:
    records = {"40": "OTRO"}
    with WebFormClient(form_url, records, courtesy_seconds=0) as client:
        outcome = client.write_record(40, "R$ 9,999")
        assert outcome.ok is False
        assert "write skipped" in (outcome.error or "")
    assert STATE[40]["valor"] == "R$ 5,0800"  # unchanged


def test_querystring_selects_record(form_url: str) -> None:
    records = {"40": "USD/BRL"}
    with WebFormClient(form_url, records, courtesy_seconds=0) as client:
        outcome = client.write_record(40, "R$ 5,0820")
        assert outcome.nombre == "USD/BRL"
