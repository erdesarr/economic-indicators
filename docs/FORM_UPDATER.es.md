# Form updater — actualización del formulario desde Google Sheet

**[English](FORM_UPDATER.md) | Español**

El form updater lee los últimos datos del ETL y escribe valores formateados en el
formulario público GlobalNews "Indicadores" (ASP.NET FormView). La configuración
la editan 3 personas en un libro público de Google Sheets; nunca se reescriben
workflows YAML.

## Pipeline

```
Google Sheet (indicators_config + ajustes)
        │  config_refresh.yml (onChange de Apps Script: sync + write)
        │  sync_config.yml   (respaldo 6 h, gviz CSV)
        ▼
config/sheet_config.csv  (snapshot normalizado, commiteado)
        │  scheduler.yml (reloj Apps Script primario; respaldo 6 h)
        ├── horario_etl      → gh workflow run scrape.yml
        └── horario_updater  → gh workflow run form_update.yml -f mode=write
                                   │  Playwright + golden records
                                   ▼
                            formulario GlobalNews
```

## Esquema del libro

### `indicators_config` (8 columnas + override opcional)

| Col | Nombre | Semántica |
|---|---|---|
| A | `indicador` | slug del catálogo del ETL; otro valor = fila inválida (se omite con warning) |
| B | `prefijo` | texto literal antes del valor; vacío = nada; espacios finales preservados (`"US$ "`) |
| C | `sufijo` | texto literal después del valor; espacios iniciales preservados (`" /MMBtu"`) |
| D | `mostrar_variacion` | checkbox booleano |
| E | `simbolo_variacion` | par `SUBIDA|BAJADA` (`+|-`, `^|v`, `↑|↓`); token único = subida con ese token y bajada `-` + warning; vacío = `+|-` |
| F | `fallback` | slug de otra variable del catálogo; vacío = sin fallback (NO es URL) |
| G | `id_indicador` | id del registro (`IndicadorID`); se acepta el header legacy `numero_control` |
| H | `activo` | FALSE pausa la **escritura** de la fila, no la extracción |

Columna opcional I: `umbral_variacion_pct` por fila (override del global; vacía =
hereda el global).

Booleanos bilingües (`true/verdadero/1/si/sí/x` vs `false/falso/0/no/vacío`;
otro valor → warning + False). Los prefijos/sufijos pueden traer backticks
visuales (`` `$ ` ``); el sync quita un backtick inicial y uno final. El
separador del símbolo debe ser el pipe ASCII **U+007C**.

### `ajustes` (clave-valor)

| clave | valor |
|---|---|
| `horario_etl` | cron de `scrape.yml`, p. ej. `0 21 * * *` |
| `horario_updater` | cron de `form_update.yml`, p. ej. `30 21 * * *` |
| `umbral_variacion_pct` | guard global de variación, p. ej. `30` |

Se busca por `clave` (inmune a ordenamientos). Clave desconocida → warning.
Clave faltante → último snapshot commiteado; si no existe, default bootstrap de
`config/form_updater.yaml`; además warning y ping `/fail` del check sync. Los
crons soportan `m h * * *` y `m h * * dow` y se validan con croniter. Valores
inválidos se ignoran con warning (el scheduler conserva el último válido).

## Sync

`sync_config.yml` corre cada **6 h** (`0 */6 * * *` UTC) como respaldo del
snapshot y on-demand. El camino primario es `config_refresh.yml`, despachado por
el `onChange` de Apps Script: ejecuta el mismo sync y luego el form updater en
write. El sync lee ambas pestañas vía gviz (`.../gviz/tq?tqx=out:csv&sheet=...`)
con httpx (10 s, 3 reintentos), normaliza, valida y escribe
`config/sheet_config.csv` (una fila por indicador + filas de settings con
`clave`/`valor`). Solo commitea si el snapshot cambia. Si gviz falla no commitea
y pingea `/fail` del check sync; el repo conserva la última configuración
válida.

`SHEET_ID` vive en `config/form_updater.yaml` (no es sensible). El libro se
comparte como "Cualquiera con el vínculo → Lector"; 3 personas son editoras por
correo; **ambas pestañas** quedan cubiertas por los permisos del libro. El
**Historial de versiones** de Sheets es la red de seguridad.

## Cadena de resolución (con guard de variación)

Por fila ACTIVA los candidatos son `[primary, fallback, forward_fill]`:

1. **primary** fresco (`status=ok`) y, si existe día previo,
   `|variation_pct| <= umbral` → se usa (`source=primary`).
2. Si no, **fallback** (si está configurado) fresco y dentro del umbral → se usa
   (`source=fallback`). La variación mostrada es la del fallback.
3. Si no, **forward_fill** → la lectura previa almacenada
   (`source=forward_filled`). El guard nunca lo descarta.
4. Si no hay candidato, la fila se omite, se reporta y el ping del form-updater
   va a `/fail`.

Un candidato fresco se descarta con el motivo
`variación X% excede umbral Y%` (se loguea y se incluye en el body del `/fail`).
Sin día previo el guard no aplica. `activo=FALSE` pausa solo la escritura; la
variable sigue disponible como fallback de otras filas (el resolvedor lee el
contrato del ETL, no la hoja).

## Formateo

```
valor_final = prefijo + numero_es_CO(value) + sufijo
si mostrar_variacion y variation_pct != null:
    simbolo = subida si variation_pct >= 0 si no bajada
    valor_final += " " + simbolo + numero_es_CO(abs(variation_pct), 2) + "%"
```

- `numero_es_CO`: miles con punto, decimales con coma; valor hasta 4 decimales
  (sin ceros sobrantes), variación exactamente 2 decimales.
- Siempre un espacio antes del bloque de variación; prefijo/sufijo literales.
- Ejemplos: `US$ 3,118 /MMBtu -4,09%`, `R$ 5,082 +0,76%`, `11,409%`.

## Fuente de datos y freshness

`run_form_updater.py` reconstruye el contrato JSON desde
`data/indicadores.db` (`latest.json` + `history.json` por indicador). Si la
lectura más nueva local no es de hoy (America/Bogota), intenta el contrato
commiteado en `raw.githubusercontent.com` (`CONTRACT_RAW_BASE` o
`https://raw.githubusercontent.com/<repo>/main/docs/api/v1`). Si el remoto
también está viejo o inaccesible, aborta: **nunca escribe datos viejos**.

## Modelo del formulario (verificado en vivo)

- URL: `FORM_URL` (base sin querystring) + `?IndicadorID={id_indicador}`.
- Un único textbox de valor:
  `ctl00$ContentPlaceHolder1$FormView1$ctl04$ctl02$__valor$TextBox1`.
- `Nombre` y `Formato` no se modifican; `Formato` es el placeholder `{0}`.
- Antes de escribir, el `Nombre` visible debe coincidir con
  `tests/golden/webform/records.json` (id → Nombre). Si no coincide, no se
  escribe y se reporta `/fail`.
- "Actualizar" es un `<a>` con `WebForm_DoPostBackWithOptions`; tras el postback
  el sitio redirige a `List.aspx`. El updater vuelve al registro y relee el valor
  para verificar la persistencia. Loguea valor anterior/nuevo (rollback manual)
  y guarda screenshots como artifacts.

## Modos

| Modo | ¿Escribe? | Uso |
|---|---|---|
| `dry-run` | no | tabla con `repr()` de valor, variación, texto formateado, source y motivos de descarte; sin browser |
| `write` | sí | actualiza el formulario real; Healthchecks `/start`, success o `/fail` con tabla |
| `capture` | no | lee `Nombre` de los ids activos y guarda `tests/golden/webform/records.json` |
| `probe` | no | volcado diagnóstico de inputs/clickables + HTML (artifacts) |

CLI: `python scripts/run_form_updater.py --mode dry-run [--slugs 40,35,41]`.
Workflow: Actions → **Form update** → Run workflow con `mode`/`slugs`.

## Healthchecks

| Check | Disparo | Éxito |
|---|---|---|
| `form-updater` | modo write | cada run; `/fail` con tabla de fallos |
| `sync` | respaldo 6 h + `config_refresh` | success por corrida; `/fail` si gviz falla o falta/está inválido un ajuste |
| `scheduler` | reloj Apps Script (primario) + respaldo 6 h | success por corrida; `/fail` si crashea |
| `appscript-clock` | cada tick de 5 min | success; `/fail` ante error de lectura/auth/dispatch; trigger muerto = sin ping |

Si falta `HEALTHCHECKS_URL_*` solo se desactiva ese check (warning, sin crash).

## Tests

- `tests/unit/test_form_sheet.py`: parseo gviz, booleanos, backticks, pipe del
  símbolo, filas inválidas, overrides, round-trip del snapshot.
- `tests/unit/test_resolution.py`: los cinco caminos del guard.
- `tests/unit/test_formatter.py`: números es-CO, prefijo/sufijo, símbolos,
  variación oculta/nula.
- `tests/unit/test_updater.py`: plan/sources, freshness + fallback raw.
- `tests/unit/test_webform.py`: match del golden y no escritura si no coincide.
- `tests/contract/test_webform_playwright.py`: Playwright contra un FormView
  Flask mock (VIEWSTATE/EVENTVALIDATION, querystring, fill, clic, redirect,
  verificación).

## Dedup por origen, watchdog solo natural y readiness de Pages (2026-10-20)

- `config/scheduler_state.json` guarda por clave `{origin: fecha-de-ocurrencia}`
  con `origin` = `natural` (ventana del cron) o `forced` (manual/`--force`/
  `--now` de prueba). El dedup solo bloquea una repetición del MISMO
  origen+ocurrencia: un forzado/de prueba nunca veta la ventana natural, y una
  natural no bloquea la de mañana.
- `config/form_update_state.json` registra cada write con su origen. El watchdog
  cuenta solo writes `natural`: un write forzado/de prueba no reinicia el reloj
  de 26 h ni enmascara un updater natural silencioso.
- Antes de escribir, el updater hace polling del `latest.json` desplegado en
  Pages (cada 30 s, hasta ~5 min) hasta que tenga las lecturas de hoy y un
  `generated_at` reciente. Si sigue stale, el run falla con un `/fail` explícito
  del form-updater. Opción elegida: polling (no un gap de 30 min en la hoja),
  para que el gap ETL→updater de 15 min sea seguro aunque Pages esté desplegando.

Tokens de dedup: natural = fecha Bogota; forzado/prueba = timestamp exacto de
ocurrencia. Los vetos naturales se loguean como WARNING y se incluyen en el body
del ping del scheduler (`VETOED_NATURAL=...`); los forzados/de prueba nunca vetan
la ventana natural.
