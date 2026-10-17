"""Contract test: every user-facing English doc has a Spanish pair + selectors."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PAIRS = {
    "README.md": "README.es.md",
    "docs/CONTRACT.md": "docs/CONTRACT.es.md",
    "docs/RUNBOOK.md": "docs/RUNBOOK.es.md",
    "docs/FORM_UPDATER.md": "docs/FORM_UPDATER.es.md",
}


@pytest.mark.parametrize(("english", "spanish"), PAIRS.items())
def test_spanish_pair_exists(english: str, spanish: str) -> None:
    assert (REPO_ROOT / english).exists(), english
    assert (REPO_ROOT / spanish).exists(), spanish


@pytest.mark.parametrize(("english", "spanish"), PAIRS.items())
def test_english_links_to_spanish(english: str, spanish: str) -> None:
    content = (REPO_ROOT / english).read_text(encoding="utf-8")
    assert f"[Español]({Path(spanish).name})" in content, english
    assert "English" in content


@pytest.mark.parametrize(("english", "spanish"), PAIRS.items())
def test_spanish_links_to_english(english: str, spanish: str) -> None:
    content = (REPO_ROOT / spanish).read_text(encoding="utf-8")
    assert f"[English]({Path(english).name})" in content, spanish
    assert "Español" in content


def test_spanish_readme_documents_sheet_schema_and_gate() -> None:
    content = (REPO_ROOT / "README.es.md").read_text(encoding="utf-8")
    for token in (
        "indicators_config",
        "ajustes",
        "simbolo_variacion",
        "fallback",
        "forward_fill",
        "umbral_variacion_pct",
        "Historial de versiones",
        "Cualquiera con el vínculo",
        "scheduler",
        "Gate de activación",
    ):
        assert token in content, token
