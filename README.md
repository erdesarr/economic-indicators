# economic-indicators

**English | [Español](README.es.md)**

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
| `HEALTHCHECKS_URL` | yes (owner-created) | full ping URL of the `scrape` check |
| `HEALTHCHECKS_URL_SYNC` | yes (owner-created) | full ping URL of the `sync` check |
| `HEALTHCHECKS_URL_SCHEDULER` | yes (owner-created) | full ping URL of the `scheduler` check |
| `HEALTHCHECKS_URL_FORM_UPDATER` | yes (owner-created) | full ping URL of the `form-updater` check |
| `FORM_URL` | yes (owner-created) | web form base URL (no querystring) |

Each value must be the **complete ping URL** (`https://hc-ping.com/<uuid>` of
that specific check), not a bare uuid. SMTP secrets are **retired**
(`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO`).
If a check URL is missing, only that check is disabled with a log warning and
the job keeps running (graceful degradation, covered by
`tests/contract/test_healthchecks_contract.py`).

**Check schedule rule:** the Healthchecks check period must mirror the
success-ping frequency of its job: `appscript-clock` **5 min / 30 min grace**;
`scheduler` **6 h / 2 h grace**; `sync` **6 h / 2 h grace**; `scrape` once per
scheduled slot (currently 19:00 America/Bogota from the sheet, daily grace);
`form-updater` daily with 30 min grace. An explicit `/fail` (gviz down, invalid
config) still raises an immediate alert regardless of the period.

## Alerting (Healthchecks.io)

Per run, `scrape.yml` sends:

- `POST /start` at the beginning;
- `POST /fail` with a markdown table **only for unexpected failures**: a
  forward-fill/failed indicator INSIDE its `publication_window` (source down
  when it should be published);
- `POST <uuid>` (success) with the run summary otherwise. **A forward-fill
  OUTSIDE the indicator's `publication_window` is expected** (source not
  published yet) and appears as a warning in the success body, e.g.
  `dolar_oficial_manana` before 15:00 or after 20:00. **Stale warnings without
  unexpected failures are also a success ping with the warning in the body**;
- `if: failure()` → `POST /fail` with `crash en paso <nombre>`.
- Apps Script clock: `appscript-clock` receives a ping every 5 min tick; a
  `/fail` body reports auth/dispatch errors, and a dead trigger means no ping.

The scrape check can therefore stay green with expected forward-fills. Red
means a real source failure (inside its window) or a crash, not the normal
pre-publication state of `dolar_oficial_manana`.

## Runbook

- **Smoke validates the ETL output, not a re-scrape:** the post-ETL step reads
  `artifacts/run_report.json` (same run) and checks that all 18 indicators are
  present with `ok`/`forward_filled`/`manual`. This avoids false negatives when
  GitHub delays the cron and the run lands after midnight, when the site no
  longer publishes the "tomorrow" block. `make test-live` keeps the live
  re-scrape for manual checks (`scripts/test_live.py --live`).
- **Forward-fill and publication windows:** a failed indicator copies the last
  stored reading with `status='forward_filled'`, `base_gap=1` and `reason`. The
  scrape ping is `/fail` only when the failure happens inside its
  `publication_window` in Bogotá (default `00:00-23:59`;
  `dolar_oficial_manana` is `15:00-20:00`). Outside the window it is an expected
  warning in the success body. If there is no previous reading, **no row is
  invented**: the indicator is reported as `failed` with "sin histórico para
  forward-fill" and the run exits 1 only when every source failed. The smoke
  still accepts `forward_filled` as presence.
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
- **Cron:** `scrape.yml` has no own cron; `scheduler.yml` triggers it at the
  sheet's `horario_etl` (currently 19:00 America/Bogota). The scheduler is
  dispatched primarily by the Apps Script clock; the GitHub schedule is the
  6 h backup. The old `schedule` block in `scrape.yml` stays **commented out**
  to avoid double runs.

## Adding an indicator

Edit `config/indicators.yaml` only: `slug`, `source`, `url`, `visible_label`,
`unit`, `plausible_range`, optional `source_series`/`stale_check` and
`publication_window` (Bogotá `HH:MM-HH:MM`; defaults to `00:00-23:59`). The
catalog, pipeline, API and Pages contract pick it up automatically. Then run
`make validate` to approve the values and recalibrate the range.

## Form updater (Google Sheet → public web form)

A separate module (`src/form_updater/`) updates the GlobalNews "Indicadores"
form from the latest ETL data. The book is edited by 3 people in Google Sheets
and shared as **Anyone with the link → Reader**; Sheets **version history** is
the safety net against accidental edits (both tabs are covered by the same
permissions).

Two tabs:

- `indicators_config` — 8 columns: `indicador`, `prefijo`, `sufijo`,
  `mostrar_variacion`, `simbolo_variacion`, `fallback`, `id_indicador`,
  `activo` (+ optional 9th `umbral_variacion_pct` per-row override).
- `ajustes` — key/value: `horario_etl`, `horario_updater`,
  `umbral_variacion_pct` (searched by `clave`, order-proof).

Key semantics:

- **Formatter:** `prefijo + numero_es_CO(value) + sufijo`; when enabled,
  ` +/−{symbol}{abs(variation):2dec}%` is appended (always one space). Numbers
  use dot thousands and comma decimals; values keep up to 4 decimals.
  `simbolo_variacion` is `UP|DOWN` (pipe U+007C); a single token means up-token
  and `-`; empty means `+|−`. Prefix/suffix are literal (spaces preserved; one
  outer backtick is stripped).
- **Resilience chain:** `primary → fallback → forward_fill`. A fresh candidate
  is discarded when `|variation_pct| > umbral_variacion_pct` (per-row override
  or global); forward_fill is never discarded. The displayed variation always
  belongs to the variable whose value was used.
- **Inactive rows** (`activo=FALSE`) pause writing only; the variable remains
  available as a fallback for other rows.
- **Scheduling:** the primary punctual path is the container-bound Apps Script
  clock (`apps_script/Code.gs`). Every 5 min it reads the **live** `ajustes`
  tab and, inside `[window, window+15 min)`, dispatches `scheduler.yml` (max one
  dispatch per tick) and always pings `appscript-clock`. An installable
  `onChange` (EDIT, 5 min throttle) dispatches `config_refresh.yml`, which
  syncs the sheet and refreshes the form immediately. GitHub's schedule
  (`scheduler.yml` and `sync_config.yml` every **6 h**) is only the backup; the
  8 h catch-up covers a missed window and origin-aware dedup avoids double
  dispatches. The scheduler remains the only brain: it reads the committed
  snapshot, dedupes with `config/scheduler_state.json`, and waits for the
  scrape before triggering the updater. The 6 h sync also runs the natural-only
  watchdog.
- **Safety:** the updater never writes stale data (freshness check with
  raw.githubusercontent fallback), verifies the record `Nombre` against
  `tests/golden/webform/records.json`, and re-reads the form after the postback.
- **Activation:** the scheduler/form-updater live gates are approved (see
  `docs/APPROVALS.md`); the Apps Script clock is the primary path and the 6 h
  GitHub schedule is the automatic fallback.

Full details: [docs/FORM_UPDATER.md](docs/FORM_UPDATER.md) ·
Operations: [docs/RUNBOOK.md](docs/RUNBOOK.md) ·
Contract: [docs/CONTRACT.md](docs/CONTRACT.md).

## Scheduling paths (Apps Script primary + 6 h backup)

```
Apps Script clock (5 min, LIVE ajustes)
  └─ window due ──> scheduler.yml ──> scrape.yml ──> form_update.yml
  └─ onChange EDIT (5 min throttle) ──> config_refresh.yml
                                          └─ sync sheet + form write

GitHub schedule 0 */6 * * * ──> scheduler.yml   (backup, catch-up 8 h)
GitHub schedule 0 */6 * * * ──> sync_config.yml (backup + watchdog)
```

- The Apps Script is a **thin dispatcher**: the scheduler still owns windows,
  catch-up, dedup, freshness and the ETL→updater ordering. Apps Script only
  reads the live `ajustes` to know when to dispatch and forwards sheet edits.
- Install/authorize: see [RUNBOOK §9](docs/RUNBOOK.md). The PAT is a fine-grained
  token for this repo with **Actions: write**; it lives in ScriptProperties
  (visible to script editors — rotate it when collaborators change).
- **Quota:** the two daily windows + occasional edits + four 6 h backups keep
  Actions well under ~30 runs/day (ETL ~4 min, updater ~3 min), far below the
  free plan's 2,000 min/month. The clock ticks 288 times/day but only reads the
  sheet and pings; its runtime stays well below the Apps Script daily quota.
- **Healthchecks:** `appscript-clock` 5 min / 30 min grace; `scheduler` and
  `sync` 6 h / 2 h grace; `scrape` and `form-updater` unchanged.

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
