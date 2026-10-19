# Aprobaciones del dueño

Registro de validaciones humanas del ETL (valores, fixtures y cambios de decisión).

## 2026-10-08 — Aprobación P3 (17/18 live)

- Aprobado por: dueño del proyecto.
- Run de GitHub Actions: `37827728885` (workflow_dispatch, Scrape #2).
- ETL `run_id`: `20261008T185342Z-5f29c0ad`, fecha `2026-10-08`.
- Alcance aprobado: los **17 valores** tal como salieron en el log del ETL
  (`ok=17 failures=1`), incluyendo `value` nominal y `value_published` efectivo
  de los IBR.
- `dolar_oficial_manana`: **pendiente de verificación live** (el bloque se
  publica ~15:00 America/Bogota; el run fue a las 13:54). El parser queda
  cubierto offline con el golden sintético del bloque gemelo de `trm-today`
  (`tests/golden/larepublica/main_with_manana_synthetic.html`).
- El 18/18 se aprobará por separado al capturar el HTML real post-15:00,
  regenerar los goldens con datos reales y confirmar el bloque.

Hashes SHA-256 de los `expected.json` vigentes en esta aprobación:

```
8778960a8f62a3d0301ef89021dbfa92834b00906e9e790a5ce3e32643176379  tests/golden/larepublica/expected.json
00659e50c260c1d9750072e0224d4c6437731bff3b88ad9d6a4db95d4ce320bf  tests/golden/banrep/expected.json
e5d410a51425b92602fa299055e40ae52e113e7016e91a76e8af295ac7079ed7  tests/golden/bcb/expected.json
70a7325f7eb9788fd0ec0952ffea42f5c49f56ff4b213aec418720120917edd6  tests/golden/investing/expected.json
```

## 2026-10-08 — Cambio de decisión: SMTP → Healthchecks.io

- Aprobado por: dueño del proyecto.
- Se retira por completo el alertador SMTP (`smtplib`, Secrets `SMTP_HOST`,
  `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO`).
- Nuevo alertador: pings HTTP a Healthchecks.io vía Secret `HEALTHCHECKS_URL`
  (UUID completo, creado por el dueño). El dueño recibe los avisos por las
  integraciones de Healthchecks (mail/Telegram/etc.).
- Semántica: `ping /start` al inicio; `/fail` con tabla markdown de fallos si
  `failures > 0`; ping de éxito con resumen si no hay fallos (un aviso de dato
  stale sin fallos viaja como warning dentro del ping de éxito); `if: failure()`
  → `/fail` con `crash en paso <nombre>`.
- Sin `HEALTHCHECKS_URL` el ETL continúa (warning en log), igual que la
  política anterior con SMTP.

## Gate del cron (2026-10-08: cumplido → activado)

- (a) 18/18 aprobado con golden real de `DÓLAR OFICIAL MAÑANA` — **cumplido**
  (run `37846703655`, valor real 3218.75 COP, `source_date=2026-10-09`).
- (b) ping de prueba visible en Healthchecks (start+success) — **cumplido**
  (scrape #3 y #4: `healthchecks start: sent=True`, `report: sent=True`).
- (c) dispatch post-15:00 America/Bogota verde con mañana en `ok` — **cumplido**
  (scrape #4: `ok=18 failures=0`, smoke 18/18).

Con las tres condiciones cumplidas y la aprobación explícita del dueño, el
`schedule` (`0 2 * * *` UTC = 21:00 America/Bogota) queda **activo**.

## 2026-10-08 — validación en vivo aprobada

- Aprobado por: dueño del proyecto
- Configuración: `config/indicators.yaml`
- Indicadores aprobados: 18
- Hashes SHA-256 de fixtures esperados:
  - `tests/golden/larepublica/expected.json` — `6d5e09eea10e6043f7d360c481e5ecea6482ff33466c821aaab2699cd5af9c61`
  - `tests/golden/banrep/expected.json` — `e606458d70666bd39d2891780327d4e448e17b36306c7a8c27310ad897859820`
  - `tests/golden/bcb/expected.json` — `d4da652e7b1f41a18f691de768fc090646ee6982bfe140387cf342d2e1cc99e1`
  - `tests/golden/investing/expected.json` — `e59d468585dc27967d129fbefbeb3c5442a63b8b5ff93fd70096b25b04ec7be8`

## 2026-10-08 — Aprobación 18/18 (bloque real de DÓLAR OFICIAL MAÑANA)

- Aprobado por: dueño del proyecto.
- GitHub Actions run: `37846703655` (workflow_dispatch, Scrape #4).
- ETL `run_id`: `20261008T212630Z-b04b7b82`, fecha `2026-10-08`.
- `dolar_oficial_manana`: valor real **3218.75 COP**, `raw_text` `$ 3.218,75`,
  `source_date 2026-10-09`. El golden sintético
  (`main_with_manana_synthetic.html`) fue **retirado** y reemplazado por la
  captura real (`tests/golden/larepublica/main.html`).
- `make validate` recalibró los `plausible_range` de los 18 indicadores
  (valor aprobado ± tolerancia por unidad) manteniendo los comentarios.
- Hashes SHA-256 de esta aprobación:
  - `tests/golden/larepublica/expected.json` — `6d5e09eea10e6043f7d360c481e5ecea6482ff33466c821aaab2699cd5af9c61`
  - `tests/golden/banrep/expected.json` — `e606458d70666bd39d2891780327d4e448e17b36306c7a8c27310ad897859820`
  - `tests/golden/bcb/expected.json` — `d4da652e7b1f41a18f691de768fc090646ee6982bfe140387cf342d2e1cc99e1`
  - `tests/golden/investing/expected.json` — `e59d468585dc27967d129fbefbeb3c5442a63b8b5ff93fd70096b25b04ec7be8`

## 2026-10-16 — Form updater governance approved (fallbacks + sync rules)

- Approved by: project owner.
- Book shared as "Anyone with the link -> Reader"; 3 editors by email; Sheets
  version history is the safety net against accidental edits.
- Sync rules approved: canonical header `id_indicador` (legacy alias
  `numero_control`), one leading/trailing backtick stripped from prefix/suffix,
  bilingual booleans, symbol pair parsed on pipe U+007C (single token -> up +
  "-" with warning, empty -> "+|-"), invalid rows skipped, unknown columns/keys
  warned, missing settings key -> last committed snapshot then bootstrap default
  with a /fail ping.
- Fallbacks (conservative table): only `dolar_oficial_manana -> dolar_oficial_hoy`
  is configured in the sheet. No fallbacks where no trustworthy level substitute
  exists (labeled forward-fill is the default net).
- `activo=FALSE` pauses WRITING that row only; the variable stays available as a
  fallback for other rows (the resolver reads latest.json, not the sheet).
- Open finding (owner to fix or approve): in the live sheet
  `umbral_variacion_pct=30` sits as an extra column of `indicators_config`
  instead of a key in `ajustes`. Sync warns once and uses the bootstrap default
  30.0 (critical -> /fail on the sync check). Spec-canonical location is `ajustes`.

## 2026-10-16 — Form updater: golden records, dry-run and real write approved

- Approved by: project owner (selected "Apruebo: write real 40,35,41").
- Golden `tests/golden/webform/records.json` captured live from the form (18
  active records; id 1 inactive, ids 2/34 both active with distinct names).
  SHA-256: `a0afb7151e67fe79977f4bbf9b100c9c4deb0502d7e45383260a75b4c15629ed`.
- Dry-run reviewed (run `38995934459`): 3 planned rows, 0 omitted:
  `35 IBR Overnight 11,409%`, `40 USD/BRL R$ 5,082 +0,76%`,
  `41 HH US$ 3,118 /MMBtu -4,09%` (all source=primary).
- Real write verified live (run `38997093851`): 3/3 ok, previous == new ==
  expected; screenshot artifact `form-updater-38997093851` shows the form with
  Nombre `USD/BRL`, Formato `{0}` untouched and valor `R$ 5,082 +0,76%`.
  Note: run `38996938801` also persisted the values but its post-write check
  was a false negative (ASP.NET redirects to `List.aspx` after update); the
  updater now re-navigates to the record to verify. Confirmed by the next run.
- Form details discovered live: "Actualizar" is an `<a>` with
  `WebForm_DoPostBackWithOptions`; the fixed value input is
  `...$ctl04$ctl02$__valor$TextBox1`; date validation and Formato `{0}` intact.
- Pending owner action: create `HEALTHCHECKS_URL_FORM_UPDATER` (full ping URL);
  the write run logged it as empty, so the form-updater check received no pings
  (graceful degradation, as designed).

## 2026-10-16 — Full autonomy activated (scheduler cron)

- Approved by: project owner (end-to-end chain + cron activation order).
- Chain evidence:
  - scheduler run `38998097481` (21:00 Bogota window) → triggered `scrape.yml`;
  - scrape run `38998117274`: `ok=18 failures=0`, smoke 18/18, Healthchecks
    start/report `sent=True`;
  - scheduler run `38998248948` (21:30 Bogota window) → triggered
    `form_update.yml` with `mode=write`;
  - form updater run `38998268446`: `written=18 failed=0 omitted=0`,
    Healthchecks form-updater success `sent=True`.
- `scheduler.yml`: `schedule: */5 * * * *` enabled.
- `scrape.yml`: own cron disabled; the scheduler is the single automatic trigger
  (avoids double runs). Manual `workflow_dispatch` remains.
- GitHub may delay crons; the scheduler tolerates a 5-minute window and the
  scrape smoke validates `artifacts/run_report.json` (valid forward-fill passes).

## 2026-10-16 — Gate 13 closure (final chain including Pages)

- Scheduler `39000869858` (force etl) → scrape `39000885286`:
  `ok=18 failures=0`, smoke 18/18, scrape Healthchecks start/report `sent=True`;
  the scrape run now includes the reusable Pages jobs (`pages / build`,
  `pages / deploy`) and deployed a fresh contract
  (`generated_at 2026-10-17T04:02:06Z`, CORS `*`).
- Scheduler `39001066614` (force updater, after Pages) → form updater
  `39001089496`: `data source: local (as of 2026-10-16)`,
  `written=18 failed=0 omitted=0`, form-updater Healthchecks start/success
  `sent=True`; screenshots artifact retained.
- Root cause fixed: `workflow_run` does not fire when the scrape is dispatched
  with `GITHUB_TOKEN` (scheduler). Pages is now a reusable `workflow_call` from
  the scrape run (checkout of `main` after the data commit).
- `scheduler.yml` cron `*/5 * * * *` UTC remains active; `scrape.yml` has no own
  cron (single trigger). Manual `workflow_dispatch` remains for both.

## 2026-10-19 — Form updater outage: root cause, recovery and watchdog

- Owner forensics: form-updater silent; scheduler/sync appeared alive.
- H1 ruled out: `config/sheet_config.csv` has `horario_etl=0 21 * * *` and
  `horario_updater=30 21 * * *`; `form_update.yml` has `workflow_dispatch` and
  the filename matches `scheduler.workflow_updater`.
- H2 ruled out: scheduler logs show both settings read correctly.
- Root cause (H3): GitHub throttles the scheduler `*/5` cron. Scheduled runs
  happened every 2-5 h and never inside 21:00-21:35 (e.g. 2026-10-18T04:51Z =
  23:51 Bogota; 2026-10-19T06:55Z = 01:56 Bogota; both "no window match;
  nothing dispatched"). Neither the ETL nor the updater ran naturally.
- Fix: catch-up horizon 8 h + occurrence-date dedup
  (`config/scheduler_state.json`) + sequential ETL wait before the updater.
- Recovery test 2026-10-19 with the sheet windows 12:45/13:15:
  scheduler `39296529251` -> scrape `39296576711` (17 ok + 1 forward_filled
  `dolar_oficial_manana`, smoke 18/18, scrape start/report sent=True, Pages
  `generated_at 2026-10-19T17:45:42Z`) -> scheduler `39296958287` (ETL skipped
  via state) -> form updater `39297019088` (data source local 2026-10-19,
  written=18 failed=0, form-updater start/success sent=True); sync #20
  start/success sent=True.
- Note: 12:45 is before the ~15:00 MANANA publication, so the ETL forward-filled
  it and the updater used the `dolar_oficial_hoy` fallback; the evening windows
  (after 15:00) produce 18/0.
- Hardening: `config/form_update_state.json` + `scripts/watchdog.py` (26 h stale
  or 2 consecutive zero-write runs -> form-updater `/fail`).
