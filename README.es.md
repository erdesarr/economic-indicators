# economic-indicators

**[English](README.md) | Español**

ETL diario de 18 indicadores económicos con histórico rodante de 31 días,
variación día-vs-día, API REST de solo lectura (FastAPI), contrato JSON estático
en GitHub Pages y alertas vía Healthchecks.io. Corre en GitHub Actions.

- Python 3.12, `ruff` + `mypy` strict, `pytest` (cobertura mínima 80% en `src/etl`).
- SQLite (`data/indicadores.db`) commiteado por el workflow; git es el backup.
- Cero secretos en código: todo por variables de entorno / GitHub Secrets.
- Aprobación humana (`make validate`) escribe `plausible_range`, fixtures golden
  y `docs/APPROVALS.md`.

## Estructura

```
config/indicators.yaml        catálogo declarativo del ETL (añadir indicadores aquí)
config/form_updater.yaml      SHEET_ID público y ajustes del form updater
config/sheet_config.csv       snapshot normalizado del libro (lo escribe el sync)
config/certs/                 intermedio TLS pinneado (fix de cadena Banrep)
src/etl/                      core: config, http, normalize, storage, variation,
                              pipeline, validate, contract, sources/, alerts/
src/form_updater/             sync de Google Sheet, scheduler, formatter, resolución,
                              cliente Playwright del formulario
src/api/                      FastAPI (SQLite solo lectura)
scripts/                      run_etl, validate_live, test_live, sync_config,
                              scheduler, run_form_updater, build_pages
tests/{unit,contract}/        tests offline; tests/golden/<fuente>/ fixtures
.github/workflows/            ci.yml, scrape.yml, sync_config.yml, scheduler.yml,
                              form_update.yml, pages.yml
data/indicadores.db           base SQLite commiteada
docs/                         CONTRACT, RUNBOOK, FORM_UPDATER, APPROVALS, api/v1
```

## Instalación

```bash
make install            # venv + pip install -e ".[dev]"
make install-browser    # Playwright Chromium (investing.com y formulario)
make check              # ruff + mypy
make test               # pytest unit+contract offline con cobertura
```

Sin GNU Make, ejecuta los comandos del `Makefile` con `.venv/Scripts/python.exe`
(Windows) o `.venv/bin/python` (POSIX).

## Indicadores (18)

| Fuente | Método | Indicadores |
|---|---|---|
| larepublica.co (portada, HTML) | HTML anclado por label | `dolar_oficial_hoy`, `dolar_oficial_manana`, `euro`, `cafe_colombian_milds`, `uvr`, `petroleo_brent`, `petroleo_wti` |
| larepublica.co (detalle, HTML) | HTML anclado por label | `dtf`, `msci_colcap` |
| suameca.banrep.gov.co (REST oficial) | API JSON | `ibr_overnight`, `ibr_1_mes`, `ibr_3_meses`, `ibr_6_meses`, `ibr_12_meses` |
| api.bcb.gov.br SGS 10813 (PTAX compra) | API JSON | `usd_brl_compra` |
| es.investing.com (Cloudflare) | Playwright `channel="chromium"` | `gas_ttf_nl` (TFAc1), `gas_henry_micro` (MNDc1), `lng_jkm` (JKMc1) |

**IBR (decisión del dueño):** `value` es la serie **nominal** tal como se publica
(`% nominal anual`); la barra efectiva queda en `value_published` (`% E.A.`).
No se convierte nada entre ambas. El label diurno `DÓLAR OFICIAL HOY` tiene como
alternativa nocturna `DÓLAR OFICIAL` (mismo card).

## Comandos

```bash
make validate    # scrape en vivo + tabla rich + aprobación interactiva y/n
make scrape      # un run del ETL (DB + artifacts/run_report.json)
make test        # tests offline unit+contract (incluye contrato Playwright/Flask)
make test-live   # smoke en vivo manual (no se usa en CI)
make api         # FastAPI en http://127.0.0.1:8000/docs
make pages       # genera docs/api/v1

# Form updater
python scripts/sync_config.py                    # lee el libro y actualiza el snapshot
python scripts/scheduler.py --dry-run            # decide sin disparar
python scripts/run_form_updater.py --mode dry-run # tabla repr + sources (no escribe)
```

## Form updater (Google Sheet → formulario web)

Módulo `src/form_updater/`. El libro lo editan 3 personas; se comparte como
**Cualquiera con el vínculo → Lector** (los permisos aplican a **ambas**
pestañas). El **Historial de versiones** de Sheets es la red de seguridad contra
ediciones accidentales: `Archivo → Historial de versiones → Ver historial`.
Restaurar una versión anterior no requiere tocar el repo.

### Pestaña `indicators_config` (8 columnas)

| Col | Nombre | Semántica |
|---|---|---|
| A | `indicador` | slug del catálogo del ETL (otro valor = fila inválida) |
| B | `prefijo` | texto literal antes del valor; vacío = nada; respeta espacios finales (`"US$ "`) |
| C | `sufijo` | texto literal después del valor; vacío = nada; respeta espacios iniciales (`" /MMBtu"`) |
| D | `mostrar_variacion` | checkbox |
| E | `simbolo_variacion` | par `SUBIDA|BAJADA` (`+|-`, `^|v`, `↑|↓`); token único = subidas con ese token y bajadas `-` + warning; vacío = `+|-` |
| F | `fallback` | slug de **otra** variable del catálogo; vacío = sin fallback |
| G | `id_indicador` | id del registro en el formulario (`IndicadorID`) |
| H | `activo` | FALSE = pausa la **escritura** de esa fila (no la extracción) |

Columna opcional 9: `umbral_variacion_pct` por fila (override del global).
Los prefijos/sufijos pueden venir con backticks visuales (`` `$ ` ``); el sync
quita un backtick inicial y uno final. El símbolo usa pipe ASCII (**U+007C**).

### Pestaña `ajustes` (clave-valor)

| clave | ejemplo |
|---|---|
| `horario_etl` | `0 21 * * *` |
| `horario_updater` | `30 21 * * *` |
| `umbral_variacion_pct` | `30` |

Se busca por `clave` (inmune a ordenamientos). Clave desconocida → warning.
Clave faltante → último valor válido commiteado + warning + ping `/fail` del
check sync. Valores inválidos (cron/umbral) se ignoran con warning.

### Cadena de resiliencia con guard de variación

Por fila activa: `[primary, fallback (si configurado), forward_fill]`.
- El guard aplica a candidatos **frescos** (`status=ok`): si existe día previo y
  `|variation_pct| > umbral` → se descarta con el motivo
  `variación X% excede umbral Y%` y pasa al siguiente.
- `forward_fill` **nunca** se descarta (copia del día previo; variación ~0).
- Sin día previo el guard no aplica.
- La variación mostrada corresponde a la variable cuyo valor se usó
  (primary/fallback); para forward_fill se usa la variación del valor arrastrado.
- Si se usó fallback/forward_fill o hubo descartes, el ping `/fail` incluye la
  tabla con motivo y variación observada. Sin candidato → fila omitida + aviso.

### Formateo

```
valor_final = prefijo + numero_es_CO(value) + sufijo
si mostrar_variacion y variation_pct != null:
    valor_final += " " + (token_subida si var>=0 si no token_bajada)
                   + numero_es_CO(abs(variation_pct), 2) + "%"
```
Miles con punto, decimales con coma. Valor: hasta 4 decimales (sin ceros
sobrantes). Variación: exactamente 2 decimales. Siempre un espacio antes del
bloque de variación. `prefijo`/`sufijo` literales.

### Scheduler dinámico

`sync_config.yml` (cron `7 * * * *` UTC) lee ambas pestañas y escribe
`config/sheet_config.csv`; si gviz falla no commitea y pingea `/fail` (el repo
conserva la última config válida). `scheduler.yml` (cron **`0 * * * *` UTC,
horario en punto**) lee el snapshot y compara cada horario con croniter en
America/Bogota:
- `horario_etl` → dispara `scrape.yml`.
- `horario_updater` → dispara `form_update.yml` con `mode=write`.

Una ventana que no cae en punto se ejecuta en la siguiente corrida horaria
(retraso máx ~59 min); el **catch-up** de 8 h cubre retrasos mayores. El estado
`config/scheduler_state.json` es origin-aware (natural vs forzado) y, si ambos
slots están debidos, espera a que el scrape termine antes de disparar el
updater. Un cambio de horario en la hoja tarda hasta ~1 h en aplicarse (sync
horario + scheduler horario); si urge, dispara `sync_config.yml` y
`scheduler.yml` a mano. El sync horario ejecuta el watchdog natural-only.
**Nunca** se reescriben workflows YAML.

### Gate de activación

El cron del scheduler y el de `form_update` permanecen **desactivados** hasta:
(1) libro público como Lector; (2) `FORM_URL` y `HEALTHCHECKS_URL` confirmados;
(3) `SHEET_ID` en `config/form_updater.yaml`; (4) fallbacks aprobados;
(5) dry-run con `repr()` revisado; (6) dispatch real con 3 indicadores
verificado en vivo y `tests/golden/webform/records.json` aprobado.

## Secretos

| Secret | Requerido | Uso |
|---|---|---|
| `HEALTHCHECKS_URL` | sí | ping URL completa del check `scrape` |
| `HEALTHCHECKS_URL_SYNC` | sí | ping URL completa del check `sync` |
| `HEALTHCHECKS_URL_SCHEDULER` | sí | ping URL completa del check `scheduler` |
| `HEALTHCHECKS_URL_FORM_UPDATER` | sí | ping URL completa del check `form-updater` |
| `FORM_URL` | sí | base del formulario, sin querystring |

Cada valor debe ser la **URL completa** (`https://hc-ping.com/<uuid>` del check
correspondiente), no un uuid suelto. Si falta un secret, solo se desactiva ese
check con warning en el log y el job continúa. Los secrets SMTP están retirados.

**Regla de schedules:** el periodo de cada check en Healthchecks debe reflejar
la frecuencia de pings de éxito de su job (sync horario con grace ~60 min;
scrape diario 21:00 Bogotá; scheduler horario con grace ~2 h; form-updater
diario grace 30 min). Un `/fail` explícito sigue alertando de inmediato.

## Alertas (Healthchecks)

- `scrape`: `/start`, success con resumen o `/fail` con tabla si `failures>0`;
  `if: failure()` → `/fail` con `crash en paso <nombre>`.
- `sync`: `/start` + success/fail por corrida horaria; datos stale nunca alertan
  como fallo si el ETL ya lo hizo.
- `scheduler`: success por corrida; `/fail` si crashea; cron inválido → `/fail`
  del check sync.
- `form-updater`: `/start`, success con resumen o `/fail` con tabla.

## Runbook

- **Smoke valida el output del ETL, no re-scrapea:** lee
  `artifacts/run_report.json` y exige los 18 con `ok`/`forward_filled`/`manual`.
  Evita falsos negativos si GitHub retrasa el cron (el run cae después de
  medianoche y ya no existe el bloque "mañana").
- **Forward-fill:** un indicador fallido copia la última lectura con
  `status='forward_filled'`, `reason` y `base_gap=1`; sin histórico previo no se
  inventa fila (warning "sin histórico para forward-fill").
- **Dato stale:** `source_date` repetida en dos runs `ok` → warning en el cuerpo
  del ping (success).
- **investing.com 403:** reintentos con backoff; bloqueo persistente = fallo
  controlado (forward-fill), nunca dato inventado.
- **Banrep TLS:** el server omite el intermedio; está pinneado en
  `config/certs/` y `tests/unit/test_certs.py` falla si vence en <30 días.
- **BCB SGS:** ventana acotada de fechas (HTTP 406 si no).
- **Cron:** `scrape` corre 21:00 Bogotá (`0 2 * * *` UTC; Colombia sin DST).
  GitHub puede retrasar los crons; el smoke basado en reporte lo tolera.
- Procedimientos detallados: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## API

### FastAPI

```bash
make api
# GET /api/v1/indicators
# GET /api/v1/indicators/{slug}/latest
# GET /api/v1/indicators/{slug}/history?from=&to=
# GET /api/v1/indicators/{slug}/variation
# /docs con ejemplos de datos reales
```

```bash
docker build -t indicadores-api .
docker run --rm -p 8000:8000 -v "$PWD/data:/app/data:ro" indicadores-api
```

### Contrato estático (GitHub Pages)

`pages.yml` genera y publica `docs/api/v1/`:

```
https://<user>.github.io/<repo>/api/v1/indicators.json
https://<user>.github.io/<repo>/api/v1/latest.json
https://<user>.github.io/<repo>/api/v1/indicators/<slug>/latest.json
https://<user>.github.io/<repo>/api/v1/indicators/<slug>/history.json
https://<user>.github.io/<repo>/api/v1/indicators/<slug>/variation.json
```

Detalle campo a campo: [docs/CONTRACT.md](docs/CONTRACT.md).

## Aprobaciones

`docs/APPROVALS.md` registra cada aprobación humana (valores, run ids, hashes de
fixtures) y los cambios de decisión (SMTP → Healthchecks, override de umbral,
golden del formulario y write real).
