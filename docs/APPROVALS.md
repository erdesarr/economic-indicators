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
