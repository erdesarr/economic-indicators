# economic-indicators

Daily ETL for 18 Colombian/global economic indicators with a rolling 31-day
history, day-over-day variation, a read-only REST API (FastAPI), a static JSON
contract on GitHub Pages and Healthchecks.io alerting. Runs on GitHub Actions.

- Python 3.12, `ruff` + `mypy` strict, `pytest` (coverage gate 80% on `src/etl`).
- SQLite (`data/indicadores.db`) committed by the workflow; git is the backup.
- No secrets in code: everything through environment variables / GitHub Secrets.
- Human approval workflow (`make validate`) writes `plausible_range`, golden
  fixtures and `docs/APPROVALS.md`.

## Layout

```
config/indicators.yaml        declarative indicator catalog (add indicators here only)
config/certs/                 pinned TLS intermediates (Banrep chain fix)
src/etl/                      core: config, http, normalize, storage, variation,
                              pipeline, validate, contract, sources/, alerts/
src/api/                      FastAPI app (read-only SQLite)
scripts/                      run_etl, validate_live, test_live, hc_ping, build_pages
tests/{unit,contract}/        offline tests; tests/golden/<source>/ golden fixtures
.github/workflows/            ci.yml, scrape.yml, pages.yml
data/indicadores.db           committed SQLite database
docs/                         CONTRACT.md, APPROVALS.md, docs/api/v1 (generated)
```

## Setup

```bash
make install            # venv + pip install -e ".[dev]"
make install-browser    # Playwright Chromium (only needed for investing.com)
make check              # ruff + mypy
make test               # pytest unit+contract offline with coverage gate
```

`make` targets also work without GNU Make: run the commands shown in the
`Makefile` directly with `.venv/bin/python` (POSIX) or
`.venv\Scripts\python.exe` (Windows).

## Indicators (18)

| Source | Method | Indicators |
|---|---|---|
| larepublica.co (main page, HTML) | label-anchored HTML | `dolar_oficial_hoy`, `dolar_oficial_manana`*, `euro`, `cafe_colombian_milds`, `uvr`, `petroleo_brent`, `petroleo_wti` |
| larepublica.co (detail pages, HTML) | label-anchored HTML | `dtf`, `msci_colcap` |
| suameca.banrep.gov.co (official REST) | JSON API | `ibr_overnight`, `ibr_1_mes`, `ibr_3_meses`, `ibr_6_meses`, `ibr_12_meses` |
| api.bcb.gov.br SGS 10813 (PTAX compra) | JSON API | `usd_brl_compra` |
| es.investing.com (Cloudflare) | Playwright `channel="chromium"` | `gas_ttf_nl` (TFAc1), `gas_henry_micro` (MNDc1), `lng_jkm` (JKMc1) |

\* `dolar_oficial_manana` is `verification: pending_live`: the block is
published around 15:00 America/Bogota. Until the real capture is approved it is
covered offline by a synthetic twin of the `trm-today` block
(`tests/golden/larepublica/main_with_manana_synthetic.html`).

**IBR decision (owner-approved):** `value` is the **nominal** series exactly as
published (unit `% nominal anual`). The effective bar is kept for reference in
`value_published` (unit `% E.A.`). No conversion is ever applied between them.

## Commands

```bash
make validate    # live scrape + rich table + interactive y/n approval
make scrape      # one ETL run (writes data/indicadores.db + artifacts/run_report.json)
make test        # offline unit+contract tests
make test-live   # live smoke test: 18/18 present, in range, coherent units
make api         # FastAPI on http://127.0.0.1:8000/docs
make pages       # generate docs/api/v1 static contract
```

## Secrets

| Secret | Required | Purpose |
|---|---|---|
| `HEALTHCHECKS_URL` | yes (owner-created) | full Healthchecks.io ping URL (`https://hc-ping.com/<uuid>`) |

SMTP secrets are **retired** (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASSWORD`, `ALERT_EMAIL_TO`). If `HEALTHCHECKS_URL` is missing, alerting
is disabled with a log warning and the ETL continues (graceful degradation,
covered by `tests/contract/test_healthchecks_contract.py`).

## Alerting (Healthchecks.io)

Per run, `scrape.yml` sends:

- `POST /start` at the beginning;
- `POST /fail` with a markdown table of failures when `failures > 0`
  (forward-fill applied);
- `POST <uuid>` (success) with the run summary otherwise. **Stale warnings
  without failures are a success ping with the warning in the body**;
- `if: failure()` → `POST /fail` with `crash en paso <nombre>`.

## Runbook

- **Forward-fill:** a failed indicator copies the last stored reading with
  `status='forward_filled'`, `base_gap=1` and `reason`. If there is no previous
  reading, **no row is invented**: the indicator is reported as `failed` with
  "sin histórico para forward-fill" and the run exits 1 only when every source
  failed.
- **Stale data:** if `source_date` repeats across two consecutive `ok` runs for
  an indicator with `stale_check: true`, the alert body carries a stale warning
  (success ping).
- **investing.com 403:** retries with backoff; persistent Cloudflare block is a
  controlled failure (forward-fill), never invented data.
- **Banrep TLS:** the server omits the intermediate CA from its chain. The
  intermediate is pinned in `config/certs/geotrust_ev_rsa_ca_g2.pem` and
  combined with certifi roots by `src/etl/http.py`. `tests/unit/test_certs.py`
  fails CI if the pin expires in <30 days. If Banrep fixes its chain, delete
  the PEM + the `EXTRA_CA_HOSTS` entry and drop the test.
- **BCB SGS:** the adapter adds a bounded `dataInicial`/`dataFinal` window
  (HTTP 406 otherwise).
- **Cron:** `scrape.yml` runs at 21:00 America/Bogota when enabled
  (`0 2 * * *` UTC, Colombia has no DST). The `schedule` block is **commented
  out** until the owner approves the three gate conditions in
  `docs/APPROVALS.md`.

## Adding an indicator

Edit `config/indicators.yaml` only: `slug`, `source`, `url`, `visible_label`,
`unit`, `plausible_range`, optional `source_series`/`stale_check`. The catalog,
pipeline, API and Pages contract pick it up automatically. Then run
`make validate` to approve the values and recalibrate the range.

## API

### FastAPI (local / Docker)

```bash
make api
# GET /api/v1/indicators
# GET /api/v1/indicators/{slug}/latest
# GET /api/v1/indicators/{slug}/history?from=&to=
# GET /api/v1/indicators/{slug}/variation
# OpenAPI docs with real-data examples: http://127.0.0.1:8000/docs
```

```bash
docker build -t indicadores-api .
docker run --rm -p 8000:8000 -v "$PWD/data:/app/data:ro" indicadores-api
```

### Static contract (GitHub Pages)

`pages.yml` builds and deploys `docs/api/v1/`:

```
https://<user>.github.io/<repo>/api/v1/indicators.json
https://<user>.github.io/<repo>/api/v1/latest.json
https://<user>.github.io/<repo>/api/v1/indicators/<slug>/latest.json
https://<user>.github.io/<repo>/api/v1/indicators/<slug>/history.json
https://<user>.github.io/<repo>/api/v1/indicators/<slug>/variation.json
```

See `docs/CONTRACT.md` for the full field-by-field contract.

## Approvals

`docs/APPROVALS.md` records every human approval (values, run ids, fixture
hashes) and the alerting decision change (SMTP → Healthchecks).
