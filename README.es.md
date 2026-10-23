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

### Caminos de scheduling (reloj Apps Script + respaldo 6 h)

```
Reloj Apps Script (5 min, ajustes EN VIVO)
  └─ ventana debida ──> scheduler.yml ──> scrape.yml ──> form_update.yml
  └─ onChange EDIT (throttle 5 min) ──> config_refresh.yml
                                          └─ sync de la hoja + write del form

GitHub schedule 0 */6 * * * ──> scheduler.yml   (respaldo, catch-up 8 h)
GitHub schedule 0 */6 * * * ──> sync_config.yml (respaldo + watchdog)
```

El Apps Script es un **dispatcher fino**: el scheduler sigue siendo el único
cerebro (ventanas, catch-up, dedup, frescura y orden ETL→updater). El reloj solo
lee `ajustes` en vivo para saber cuándo despachar y reenvía las ediciones.

- `scheduler.yml` y `sync_config.yml` corren cada **6 h** como respaldo
  automático; el catch-up de 8 h cubre una ventana perdida y el dedup por
  ocurrencia evita dobles disparos entre el reloj y el respaldo.
- `config_refresh.yml` = sync inmediato + form updater en `write` (con todas las
  salvaguardas: golden records, frescura, guard de umbral, verificación
  post-write y pings). Es el camino de "alguien editó la hoja, refresca ya".
- El sync de 6 h ejecuta el watchdog natural-only. Los cambios de horario en la
  hoja se reflejan en el siguiente tick (el reloj lee la hoja en vivo); la
  edición del formulario se refresca vía `config_refresh`.
- **Cuota:** 2 ventanas/día + ediciones ocasionales + 4 respaldos de 6 h dejan
  Actions en <~30 runs/día (ETL ~4 min, updater ~3 min), muy por debajo de los
  2,000 min/mes del plan gratis. El reloj hace 288 ticks/día pero solo lee la
  hoja y pingea; su runtime queda bajo la cuota diaria de Apps Script.
- **Healthchecks:** `appscript-clock` 5 min / 30 min grace; `scheduler` y `sync`
  6 h / 2 h grace; `scrape` y `form-updater` sin cambio.

Instalación/autorización: [RUNBOOK §9](docs/RUNBOOK.es.md). El PAT es
fine-grained, solo este repo, **Actions: write**, y vive en ScriptProperties
(visible para quien edite el script; rota al cambiar integrantes). Si
`apps_script/Code.gs` cambia en el repo, hay que re-pegarlo en el editor.

### Gate de activación

Los gates del scheduler/form updater están aprobados (ver
`docs/APPROVALS.md`); el reloj Apps Script es el camino primario y el schedule
de GitHub cada 6 h es el respaldo. **Nunca** se reescriben workflows YAML.

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
la frecuencia de pings de éxito de su job: `appscript-clock` **5 min / 30 min
grace**; `scheduler` **6 h / 2 h grace**; `sync` **6 h / 2 h grace**; `scrape`
en el slot de la hoja (actualmente 19:00 Bogotá, grace diario); `form-updater`
diario grace 30 min. Un `/fail` explícito sigue alertando de inmediato.

## Alertas (Healthchecks)

- `scrape`: `/start`; `/fail` con tabla **solo para fallos inesperados**
  (forward-fill/fallo DENTRO del `publication_window` del indicador). Un
  forward-fill FUERA de la ventana es esperado (la fuente aún no publica) y va
  como warning en el body del success, p. ej. `dolar_oficial_manana` antes de
  15:00 o después de 20:00. `if: failure()` → `/fail` con
  `crash en paso <nombre>`. Por eso el check puede quedar verde con
  forward-fills esperados; rojo significa fuente caída en su ventana o crash.
- `appscript-clock`: ping cada tick de 5 min; `/fail` si el reloj no puede leer
  la hoja o despachar (trigger muerto = sin ping).
- `sync`: `/start` + success/fail por corrida (respaldo 6 h y config_refresh);
  datos stale nunca alertan como fallo si el ETL ya lo hizo.
- `scheduler`: success por corrida; `/fail` si crashea; cron inválido → `/fail`
  del check sync.
- `form-updater`: `/start`, success con resumen o `/fail` con tabla.

## Runbook

- **Smoke valida el output del ETL, no re-scrapea:** lee
  `artifacts/run_report.json` y exige los 18 con `ok`/`forward_filled`/`manual`.
  Evita falsos negativos si GitHub retrasa el cron (el run cae después de
  medianoche y ya no existe el bloque "mañana").
- **Forward-fill y ventanas de publicación:** un indicador fallido copia la
  última lectura con `status='forward_filled'`, `reason` y `base_gap=1`. El ping
  del scrape va a `/fail` solo si el fallo ocurre dentro de su
  `publication_window` en Bogotá (por defecto `00:00-23:59`;
  `dolar_oficial_manana` es `15:00-20:00`). Fuera de la ventana es un warning
  esperado en el body del success. Sin histórico previo no se inventa fila
  (warning "sin histórico para forward-fill"). El smoke sigue aceptando
  `forward_filled` como presencia.
- **Dato stale:** `source_date` repetida en dos runs `ok` → warning en el cuerpo
  del ping (success).
- **investing.com 403:** reintentos con backoff; bloqueo persistente = fallo
  controlado (forward-fill), nunca dato inventado.
- **Banrep TLS:** el server omite el intermedio; está pinneado en
  `config/certs/` y `tests/unit/test_certs.py` falla si vence en <30 días.
- **BCB SGS:** ventana acotada de fechas (HTTP 406 si no).
- **Cron:** `scrape.yml` no tiene cron propio; `scheduler.yml` lo dispara en el
  `horario_etl` de la hoja (actualmente 19:00 Bogotá). El scheduler se dispara
  principalmente por el reloj Apps Script; el schedule de GitHub es el respaldo
  de 6 h. El `schedule` antiguo de `scrape.yml` sigue **comentado** para evitar
  doble corrida.
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
