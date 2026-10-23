# Form updater — Google Sheet driven web form updates

**English | [Español](FORM_UPDATER.es.md)**

The form updater reads the latest ETL data and writes formatted values to the
public GlobalNews "Indicadores" ASP.NET FormView. Configuration is edited by 3
people in a public Google Sheet; no workflow YAML is ever rewritten.

## Pipeline

```
Google Sheet (indicators_config + ajustes)
        │  config_refresh.yml (Apps Script onChange: sync + write)
        │  sync_config.yml   (6 h backup, gviz CSV)
        ▼
config/sheet_config.csv  (normalized snapshot, committed)
        │  scheduler.yml (Apps Script clock primary; 6 h backup)
        ├── horario_etl      → gh workflow run scrape.yml
        └── horario_updater  → gh workflow run form_update.yml -f mode=write
                                   │  Playwright + golden records
                                   ▼
                            GlobalNews indicadores form
```

## Sheet schema

### `indicators_config` (8 columns + optional override)

| Col | Name | Meaning |
|---|---|---|
| A | `indicador` | ETL catalog slug; anything else is an invalid row (skipped with warning) |
| B | `prefijo` | literal text before the value; empty = none; trailing spaces preserved (`"US$ "`) |
| C | `sufijo` | literal text after the value; leading spaces preserved (`" /MMBtu"`) |
| D | `mostrar_variacion` | boolean checkbox |
| E | `simbolo_variacion` | `UP|DOWN` pair (`+|-`, `^|v`, `↑|↓`); single token = up token + `-` with warning; empty = `+|-` |
| F | `fallback` | slug of another catalog variable; empty = no fallback (NOT a URL) |
| G | `id_indicador` | form record id (`IndicadorID`); legacy header `numero_control` accepted |
| H | `activo` | FALSE pauses **writing** that row, not extraction |

Optional column I: `umbral_variacion_pct` per-row override of the global
threshold (empty = inherit global).

Booleans are bilingual (`true/verdadero/1/si/sí/x` vs `false/falso/0/no/empty`;
anything else → warning + False). Prefix/suffix cells may carry literal
backticks for visual spaces (`` `$ ` ``); the sync strips one leading and one
trailing backtick. The symbol separator must be the ASCII pipe **U+007C**.

### `ajustes` (key/value)

| key | value |
|---|---|
| `horario_etl` | cron for `scrape.yml`, e.g. `0 21 * * *` |
| `horario_updater` | cron for `form_update.yml`, e.g. `30 21 * * *` |
| `umbral_variacion_pct` | global variation guard, e.g. `30` |

Values are found by `clave` (order-proof). Unknown keys → warning. Missing keys
→ last committed snapshot value, else bootstrap default from
`config/form_updater.yaml`, plus a warning and a `/fail` ping on the sync check.
Cron expressions support `m h * * *` and `m h * * dow` and are validated with
croniter. Invalid values are ignored with a warning (the scheduler keeps the
last valid committed value).

## Sync

`sync_config.yml` runs every **6 h** (`0 */6 * * *` UTC) as the snapshot
backup and on demand. The primary path is `config_refresh.yml`, dispatched by
the Apps Script `onChange`: it runs the same sync and then the form updater in
write mode. The sync fetches both tabs through gviz
(`.../gviz/tq?tqx=out:csv&sheet=...`) with httpx (10 s, 3 retries), normalizes,
validates and writes `config/sheet_config.csv` (one row per indicator + settings
rows with `clave`/`valor`). It commits only when the snapshot changes. If gviz
fails it does not commit and pings `/fail` on the sync check; the repository
keeps the last valid configuration.

SHEET_ID lives in `config/form_updater.yaml` (not sensitive). The book is shared
as "Anyone with the link → Reader"; 3 people are editors by email; both tabs are
covered by the book permissions. Sheets **version history** is the safety net.

## Resolution chain (with variation guard)

For each ACTIVE row the candidates are `[primary, fallback, forward_fill]`:

1. **primary** fresh (`status=ok`) and, when a previous day exists,
   `|variation_pct| <= threshold` → use it (`source=primary`).
2. Otherwise **fallback** (if configured) fresh and within threshold → use it
   (`source=fallback`). The displayed variation belongs to the fallback.
3. Otherwise **forward_fill** → the previous stored reading
   (`source=forward_filled`). It is never discarded by the guard.
4. Otherwise the row is omitted, reported and the form-updater ping goes to
   `/fail`.

A fresh candidate is discarded with the reason
`variación X% excede umbral Y%` (logged and included in the `/fail` body).
With no previous day the guard does not apply. `activo=FALSE` pauses writing
only; the variable remains available as a fallback for other rows (the resolver
reads the ETL contract, not the sheet).

## Formatting

```
valor_final = prefijo + numero_es_CO(value) + sufijo
if mostrar_variacion and variation_pct is not None:
    symbol = up if variation_pct >= 0 else down
    valor_final += " " + symbol + numero_es_CO(abs(variation_pct), 2) + "%"
```

- `numero_es_CO`: dot thousands, comma decimals; values keep up to 4 decimals
  (trailing zeros trimmed), variations use exactly 2 decimals.
- Always one space before the variation block; prefix/suffix are literal.
- Example: `US$ 3,118 /MMBtu -4,09%`, `R$ 5,082 +0,76%`, `11,409%`.

## Updater data source and freshness

`run_form_updater.py` rebuilds the JSON contract from `data/indicadores.db`
(`latest.json` + per-indicator `history.json`). If the local newest reading is
not today (America/Bogota), it tries the committed contract on
`raw.githubusercontent.com` (`CONTRACT_RAW_BASE` or
`https://raw.githubusercontent.com/<repo>/main/docs/api/v1`). If the remote is
also stale or unreachable, the run aborts: **stale data is never written**.

## Web form model (verified live)

- URL: `FORM_URL` (base without querystring) + `?IndicadorID={id_indicador}`.
- One fixed value textbox:
  `ctl00$ContentPlaceHolder1$FormView1$ctl04$ctl02$__valor$TextBox1`.
- Fields `Nombre` and `Formato` are read-only for the updater; `Formato` is the
  `.NET` placeholder `{0}` and is never modified.
- Before writing, the visible `Nombre` must match
  `tests/golden/webform/records.json` (id → Nombre). A mismatch skips the write
  and reports `/fail`.
- "Actualizar" is an `<a>` using `WebForm_DoPostBackWithOptions`; after the
  postback the site redirects to `List.aspx`. The updater re-navigates to the
  record and re-reads the value to verify persistence. It logs the previous and
  new values (manual rollback evidence) and stores screenshots as artifacts.

## Modes

| Mode | Writes? | Purpose |
|---|---|---|
| `dry-run` | no | print a `repr()` table with value, variation, formatted text, source and discard reasons; no browser |
| `write` | yes | update the real form; Healthchecks `/start`, success or `/fail` with table |
| `capture` | no | read `Nombre` for the active ids and save `tests/golden/webform/records.json` |
| `probe` | no | diagnostic dump of inputs/clickables + page HTML (artifacts) |

CLI: `python scripts/run_form_updater.py --mode dry-run [--slugs 40,35,41]`.
Workflow: Actions → **Form update** → Run workflow with `mode`/`slugs`.

## Healthchecks

| Check | Trigger | Success |
|---|---|---|
| `form-updater` | write mode | every write run; `/fail` with the failure table |
| `sync` | 6 h backup + `config_refresh` | success each run; `/fail` on gviz failure or missing/invalid settings |
| `scheduler` | Apps Script clock (primary) + 6 h backup | success each run; `/fail` on crash |
| `appscript-clock` | every 5 min tick | success; `/fail` on sheet-read/auth/dispatch errors; a dead trigger means no ping |

Missing `HEALTHCHECKS_URL_*` disables only that check (warning, no crash).

## Tests

- `tests/unit/test_form_sheet.py`: gviz parsing, booleans, backticks, symbol
  pipe, invalid rows, overrides, snapshot round-trip.
- `tests/unit/test_resolution.py`: the five guard paths.
- `tests/unit/test_formatter.py`: es-CO numbers, prefix/suffix, symbols, hidden
  variation, null variation.
- `tests/unit/test_updater.py`: plan/sources, freshness + raw fallback.
- `tests/unit/test_webform.py`: golden record matching, no write on mismatch.
- `tests/contract/test_webform_playwright.py`: Playwright against a mock Flask
  FormView (VIEWSTATE/EVENTVALIDATION, querystring, fill, click, redirect,
  verification).

## Origin-aware dedup, natural-only watchdog and Pages readiness (2026-10-20)

- `config/scheduler_state.json` stores per key `{origin: occurrence-date}` with
  `origin` = `natural` (cron window) or `forced` (manual/`--force`/`--now` test).
  Dedup only blocks a repeat of the SAME origin+occurrence: a forced/test run
  never vetoes the natural window, and a natural run does not block tomorrow.
- `config/form_update_state.json` records every write with its origin. The
  watchdog counts only `natural` writes: a forced/test write does not reset the
  26 h clock and cannot mask a silent natural updater.
- Before writing, the updater polls the deployed Pages `latest.json` (every
  30 s, up to ~5 min) until it contains today's readings and a recent
  `generated_at`. If it is still stale, the run fails with an explicit
  form-updater `/fail`. Chosen option: polling (not a 30-minute sheet gap), so
  a 15-minute ETL→updater gap is safe even when Pages is still deploying.

Dedup tokens: natural = Bogota date; forced/test = exact occurrence timestamp.
Natural vetoes are logged as WARNING and included in the scheduler ping body
(`VETOED_NATURAL=...`); forced/test dispatches never veto the natural window.
