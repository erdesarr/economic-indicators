PYTHON ?= python
VENV ?= .venv
ifeq ($(OS),Windows_NT)
	VENV_PY := $(VENV)/Scripts/python.exe
else
	VENV_PY := $(VENV)/bin/python
endif

.PHONY: install install-browser validate scrape test test-live api pages lint typecheck check clean

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -e ".[dev]"

install-browser:
	$(VENV_PY) -m playwright install chromium

lint:
	$(VENV_PY) -m ruff check src tests scripts
	$(VENV_PY) -m ruff format --check src tests scripts

typecheck:
	$(VENV_PY) -m mypy

check: lint typecheck

validate:
	$(VENV_PY) scripts/validate_live.py

scrape:
	$(VENV_PY) scripts/run_etl.py

test:
	$(VENV_PY) -m pytest tests/unit tests/contract -m "not live" --cov --cov-report=term-missing

test-live:
	$(VENV_PY) scripts/test_live.py

api:
	$(VENV_PY) -m uvicorn api.main:app --app-dir src --reload

pages:
	$(VENV_PY) scripts/build_pages.py

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
