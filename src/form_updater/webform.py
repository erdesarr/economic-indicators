"""Playwright client for the public ASP.NET FormView web form.

Verified model: FormView with ONE fixed value textbox
``ctl00$ContentPlaceHolder1$FormView1$ctl04$ctl02$__valor$TextBox1``; the record
is selected with ``Edit.aspx?IndicadorID={id}``. The formatter writes plain text
and never touches ``Nombre`` nor ``Formato`` (assumed ``{0}`` placeholder).

Anti-wrong-record: before writing, the visible ``Nombre`` must match the golden
``tests/golden/webform/records.json`` mapping (id -> Nombre).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RECORDS_PATH = Path("tests/golden/webform/records.json")
VALUE_INPUT_NAME = "ctl00$ContentPlaceHolder1$FormView1$ctl04$ctl02$__valor$TextBox1"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class WebFormError(RuntimeError):
    """Raised when the form structure or a write cannot be handled."""


@dataclass(frozen=True)
class WriteOutcome:
    id_indicador: int
    nombre: str
    previous_value: str
    new_value: str
    expected_value: str
    ok: bool
    error: str | None = None


def load_records(path: Path | str = RECORDS_PATH) -> dict[str, str]:
    records_path = Path(path)
    if not records_path.exists():
        raise WebFormError(f"golden records file not found: {records_path}")
    raw = json.loads(records_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise WebFormError("golden records must be a JSON object id -> Nombre")
    return {str(key): str(value) for key, value in raw.items()}


def save_records(path: Path | str, records: dict[str, str]) -> Path:
    records_path = Path(path)
    records_path.parent.mkdir(parents=True, exist_ok=True)
    records_path.write_text(
        json.dumps(
            dict(sorted(records.items(), key=lambda item: int(item[0]))),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return records_path


def record_error(id_indicador: int, actual_nombre: str, records: dict[str, str]) -> str | None:
    """Return a mismatch reason, or None when the record is safe to write."""

    expected = records.get(str(id_indicador))
    if expected is None:
        return f"record {id_indicador}: missing from golden records.json"
    if actual_nombre.strip() != expected.strip():
        return (
            f"record {id_indicador}: Nombre {actual_nombre.strip()!r} "
            f"!= golden {expected.strip()!r}; write skipped"
        )
    return None


class WebFormClient:
    """Context manager wrapping Playwright/Chromium."""

    def __init__(
        self,
        base_url: str,
        records: dict[str, str],
        headless: bool = True,
        timeout_ms: int = 30000,
        courtesy_seconds: float = 1.5,
        artifacts_dir: Path | None = None,
    ) -> None:
        if "?" in base_url:
            raise WebFormError("FORM_URL must be the base URL without querystring")
        self.base_url = base_url.rstrip("/")
        self.records = records
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.courtesy_seconds = courtesy_seconds
        self.artifacts_dir = artifacts_dir
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    def __enter__(self) -> WebFormClient:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled"]
        try:
            self._browser = self._playwright.chromium.launch(
                headless=self.headless, channel="chromium", args=args
            )
        except Exception:  # noqa: BLE001 - fall back to bundled chromium
            self._browser = self._playwright.chromium.launch(headless=self.headless, args=args)
        context = self._browser.new_context(user_agent=USER_AGENT, locale="es-CO")
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        self._page = context.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        return self

    def __exit__(self, *args: object) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def _url(self, id_indicador: int) -> str:
        return f"{self.base_url}?IndicadorID={id_indicador}"

    def _goto(self, id_indicador: int) -> None:
        self._page.goto(self._url(id_indicador), wait_until="domcontentloaded")
        self._page.wait_for_timeout(1000)
        time.sleep(self.courtesy_seconds)

    def _find_named_input(self, token: str, exclude: str | None = None) -> Any:
        inputs = self._page.locator("input[name]")
        names: list[str] = []
        for index in range(inputs.count()):
            element = inputs.nth(index)
            name = (element.get_attribute("name") or "").lower()
            names.append(name)
            if token.lower() in name and (exclude is None or exclude.lower() not in name):
                return element
        raise WebFormError(
            f"input containing {token!r} not found; "
            f"url={self._page.url} title={self._page.title()!r} inputs={names[:20]}"
        )

    def read_nombre(self) -> str:
        value = self._find_named_input("__nombre").input_value()
        return str(value)

    def read_value(self) -> str:
        try:
            return str(self._find_named_input("__valor").input_value())
        except WebFormError:
            value = self._page.locator(f'input[name="{VALUE_INPUT_NAME}"]').input_value(
                timeout=3000
            )
            return str(value)

    def _click_update(self) -> None:
        candidates = self._page.locator("input, button, a")
        seen: list[str] = []
        for index in range(candidates.count()):
            element = candidates.nth(index)
            try:
                label = element.get_attribute("value") or ""
                if not label:
                    label = element.inner_text() or ""
            except Exception:  # noqa: BLE001 - some elements are not readable
                continue
            title = element.get_attribute("title") or ""
            name = element.get_attribute("name") or ""
            seen.append(f"{name or title or label!r}")
            haystack = f"{label} {title} {name}".lower()
            if "actualizar" in haystack:
                element.click()
                self._page.wait_for_load_state("domcontentloaded")
                self._page.wait_for_timeout(1000)
                return
        raise WebFormError("'Actualizar' button not found; candidates=" + ", ".join(seen[:15]))

    def probe(self, id_indicador: int) -> dict[str, Any]:
        """Diagnostic dump of the record page (no writes)."""

        self._goto(id_indicador)
        inputs: list[dict[str, Any]] = []
        for index in range(self._page.locator("input, textarea, select").count()):
            element = self._page.locator("input, textarea, select").nth(index)
            inputs.append(
                {
                    "tag": element.evaluate("el => el.tagName"),
                    "name": element.get_attribute("name"),
                    "type": element.get_attribute("type"),
                    "id": element.get_attribute("id"),
                    "value": (element.get_attribute("value") or "")[:80],
                }
            )
        clickables: list[dict[str, Any]] = []
        for index in range(self._page.locator("input, button, a").count()):
            element = self._page.locator("input, button, a").nth(index)
            clickables.append(
                {
                    "tag": element.evaluate("el => el.tagName"),
                    "name": element.get_attribute("name"),
                    "id": element.get_attribute("id"),
                    "value": element.get_attribute("value"),
                    "title": element.get_attribute("title"),
                    "href": (element.get_attribute("href") or "")[:80],
                    "text": (element.inner_text() or "")[:60],
                }
            )
        return {
            "id": id_indicador,
            "url": self._page.url,
            "title": self._page.title(),
            "inputs": inputs,
            "clickables": clickables,
        }

    def page_html(self) -> str:
        return str(self._page.content())

    def capture_names(self, ids: list[int]) -> dict[str, str]:
        records: dict[str, str] = {}
        for id_indicador in ids:
            self._goto(id_indicador)
            records[str(id_indicador)] = self.read_nombre().strip()
            logger.info("captured record %s -> %r", id_indicador, records[str(id_indicador)])
        return records

    def write_record(self, id_indicador: int, expected_value: str) -> WriteOutcome:
        self._goto(id_indicador)
        nombre = self.read_nombre().strip()
        logger.info("record %s: url=%s nombre=%r", id_indicador, self._page.url, nombre)
        mismatch = record_error(id_indicador, nombre, self.records)
        if mismatch is not None:
            return WriteOutcome(
                id_indicador=id_indicador,
                nombre=nombre,
                previous_value="",
                new_value="",
                expected_value=expected_value,
                ok=False,
                error=mismatch,
            )
        previous = self.read_value()
        self._find_named_input("__valor").fill(expected_value)
        self._click_update()
        new_value = self.read_value()
        ok = new_value == expected_value
        error = (
            None
            if ok
            else (
                f"post-write verification failed: read {new_value!r}, expected {expected_value!r}"
            )
        )
        if self.artifacts_dir is not None:
            try:
                self.artifacts_dir.mkdir(parents=True, exist_ok=True)
                self._page.screenshot(
                    path=str(self.artifacts_dir / f"webform_{id_indicador}.png"),
                    full_page=False,
                )
            except Exception:  # noqa: BLE001 - artifacts are best-effort
                logger.warning("could not capture screenshot for record %s", id_indicador)
        return WriteOutcome(
            id_indicador=id_indicador,
            nombre=nombre,
            previous_value=previous,
            new_value=new_value,
            expected_value=expected_value,
            ok=ok,
            error=error,
        )
