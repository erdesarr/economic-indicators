# Form updater â€” actualizaciÃ³n del formulario desde Google Sheet

**[English](FORM_UPDATER.md) | EspaÃ±ol**

El form updater lee los Ãºltimos datos del ETL y escribe valores formateados en el
formulario pÃºblico GlobalNews "Indicadores" (ASP.NET FormView). La configuraciÃ³n
la editan 3 personas en un libro pÃºblico de Google Sheets; nunca se reescriben
workflows YAML.

## Pipeline

```
Google Sheet (indicators_config + ajustes)
        â”‚  sync_config.yml (horario, gviz CSV)
        â–¼
config/sheet_config.csv  (snapshot normalizado, commiteado)
        â”‚  scheduler.yml (0 * * * *, horario; ventanas America/Bogota)
        â”œâ”€â”€ horario_etl      â†’ gh workflow run scrape.yml
        â””â”€â”€ horario_updater  â†’ gh workflow run form_update.yml -f mode=write
                                   â”‚  Playwright + golden records
                                   â–¼
                            formulario GlobalNews
```

## Esquema del libro

### `indicators_config` (8 columnas + override opcional)

| Col | Nombre | SemÃ¡ntica |
|---|---|---|
| A | `indicador` | slug del catÃ¡logo del ETL; otro valor = fila invÃ¡lida (se omite con warning) |
| B | `prefijo` | texto literal antes del valor; vacÃ­o = nada; espacios finales preservados (`"US$ "`) |
| C | `sufijo` | texto literal despuÃ©s del valor; espacios iniciales preservados (`" /MMBtu"`) |
| D | `mostrar_variacion` | checkbox booleano |
| E | `simbolo_variacion` | par `SUBIDA|BAJADA` (`+|-`, `^|v`, `â†‘|â†“`); token Ãºnico = subida con ese token y bajada `-` + warning; vacÃ­o = `+|-` |
| F | `fallback` | slug de otra variable del catÃ¡logo; vacÃ­o = sin fallback (NO es URL) |
| G | `id_indicador` | id del registro (`IndicadorID`); se acepta el header legacy `numero_control` |
| H | `activo` | FALSE pausa la **escritura** de la fila, no la extracciÃ³n |

Columna opcional I: `umbral_variacion_pct` por fila (override del global; vacÃ­a =
hereda el global).

Booleanos bilingÃ¼es (`true/verdadero/1/si/sÃ­/x` vs `false/falso/0/no/vacÃ­o`;
otro valor â†’ warning + False). Los prefijos/sufijos pueden traer backticks
visuales (`` `$ ` ``); el sync quita un backtick inicial y uno final. El
separador del sÃ­mbolo debe ser el pipe ASCII **U+007C**.

### `ajustes` (clave-valor)

| clave | valor |
|---|---|
| `horario_etl` | cron de `scrape.yml`, p. ej. `0 21 * * *` |
| `horario_updater` | cron de `form_update.yml`, p. ej. `30 21 * * *` |
| `umbral_variacion_pct` | guard global de variaciÃ³n, p. ej. `30` |

Se busca por `clave` (inmune a ordenamientos). Clave desconocida â†’ warning.
Clave faltante â†’ Ãºltimo snapshot commiteado; si no existe, default bootstrap de
`config/form_updater.yaml`; ademÃ¡s warning y ping `/fail` del check sync. Los
crons soportan `m h * * *` y `m h * * dow` y se validan con croniter. Valores
invÃ¡lidos se ignoran con warning (el scheduler conserva el Ãºltimo vÃ¡lido).

## Sync

`sync_config.yml` corre cada hora (`7 * * * *` UTC) y on-demand. Lee ambas
pestaÃ±as vÃ­a gviz (`.../gviz/tq?tqx=out:csv&sheet=...`) con httpx (10 s, 3
reintentos), normaliza, valida y escribe `config/sheet_config.csv` (una fila por
indicador + filas de settings con `clave`/`valor`). Solo commitea si el snapshot
cambia. Si gviz falla no commitea y pingea `/fail` del check sync; el repo
conserva la Ãºltima configuraciÃ³n vÃ¡lida.

`SHEET_ID` vive en `config/form_updater.yaml` (no es sensible). El libro se
comparte como "Cualquiera con el vÃ­nculo â†’ Lector"; 3 personas son editoras por
correo; **ambas pestaÃ±as** quedan cubiertas por los permisos del libro. El
**Historial de versiones** de Sheets es la red de seguridad.

## Cadena de resoluciÃ³n (con guard de variaciÃ³n)

Por fila ACTIVA los candidatos son `[primary, fallback, forward_fill]`:

1. **primary** fresco (`status=ok`) y, si existe dÃ­a previo,
   `|variation_pct| <= umbral` â†’ se usa (`source=primary`).
2. Si no, **fallback** (si estÃ¡ configurado) fresco y dentro del umbral â†’ se usa
   (`source=fallback`). La variaciÃ³n mostrada es la del fallback.
3. Si no, **forward_fill** â†’ la lectura previa almacenada
   (`source=forward_filled`). El guard nunca lo descarta.
4. Si no hay candidato, la fila se omite, se reporta y el ping del form-updater
   va a `/fail`.

Un candidato fresco se descarta con el motivo
`variaciÃ³n X% excede umbral Y%` (se loguea y se incluye en el body del `/fail`).
Sin dÃ­a previo el guard no aplica. `activo=FALSE` pausa solo la escritura; la
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
  (sin ceros sobrantes), variaciÃ³n exactamente 2 decimales.
- Siempre un espacio antes del bloque de variaciÃ³n; prefijo/sufijo literales.
- Ejemplos: `US$ 3,118 /MMBtu -4,09%`, `R$ 5,082 +0,76%`, `11,409%`.

## Fuente de datos y freshness

`run_form_updater.py` reconstruye el contrato JSON desde
`data/indicadores.db` (`latest.json` + `history.json` por indicador). Si la
lectura mÃ¡s nueva local no es de hoy (America/Bogota), intenta el contrato
commiteado en `raw.githubusercontent.com` (`CONTRACT_RAW_BASE` o
`https://raw.githubusercontent.com/<repo>/main/docs/api/v1`). Si el remoto
tambiÃ©n estÃ¡ viejo o inaccesible, aborta: **nunca escribe datos viejos**.

## Modelo del formulario (verificado en vivo)

- URL: `FORM_URL` (base sin querystring) + `?IndicadorID={id_indicador}`.
- Un Ãºnico textbox de valor:
  `ctl00$ContentPlaceHolder1$FormView1$ctl04$ctl02$__valor$TextBox1`.
- `Nombre` y `Formato` no se modifican; `Formato` es el placeholder `{0}`.
- Antes de escribir, el `Nombre` visible debe coincidir con
  `tests/golden/webform/records.json` (id â†’ Nombre). Si no coincide, no se
  escribe y se reporta `/fail`.
- "Actualizar" es un `<a>` con `WebForm_DoPostBackWithOptions`; tras el postback
  el sitio redirige a `List.aspx`. El updater vuelve al registro y relee el valor
  para verificar la persistencia. Loguea valor anterior/nuevo (rollback manual)
  y guarda screenshots como artifacts.

## Modos

| Modo | Â¿Escribe? | Uso |
|---|---|---|
| `dry-run` | no | tabla con `repr()` de valor, variaciÃ³n, texto formateado, source y motivos de descarte; sin browser |
| `write` | sÃ­ | actualiza el formulario real; Healthchecks `/start`, success o `/fail` con tabla |
| `capture` | no | lee `Nombre` de los ids activos y guarda `tests/golden/webform/records.json` |
| `probe` | no | volcado diagnÃ³stico de inputs/clickables + HTML (artifacts) |

CLI: `python scripts/run_form_updater.py --mode dry-run [--slugs 40,35,41]`.
Workflow: Actions â†’ **Form update** â†’ Run workflow con `mode`/`slugs`.

## Healthchecks

| Check | Disparo | Ã‰xito |
|---|---|---|
| `form-updater` | modo write | cada run; `/fail` con tabla de fallos |
| `sync` | sync horario | success por corrida; `/fail` si gviz falla o falta/estÃ¡ invÃ¡lido un ajuste |
| `scheduler` | horario (`0 * * * *`); ventanas no-en-punto en la siguiente corrida (máx ~59 min) | success por corrida; `/fail` si crashea |

Si falta `HEALTHCHECKS_URL_*` solo se desactiva ese check (warning, sin crash).

## Tests

- `tests/unit/test_form_sheet.py`: parseo gviz, booleanos, backticks, pipe del
  sÃ­mbolo, filas invÃ¡lidas, overrides, round-trip del snapshot.
- `tests/unit/test_resolution.py`: los cinco caminos del guard.
- `tests/unit/test_formatter.py`: nÃºmeros es-CO, prefijo/sufijo, sÃ­mbolos,
  variaciÃ³n oculta/nula.
- `tests/unit/test_updater.py`: plan/sources, freshness + fallback raw.
- `tests/unit/test_webform.py`: match del golden y no escritura si no coincide.
- `tests/contract/test_webform_playwright.py`: Playwright contra un FormView
  Flask mock (VIEWSTATE/EVENTVALIDATION, querystring, fill, clic, redirect,
  verificaciÃ³n).

## Dedup por origen, watchdog solo natural y readiness de Pages (2026-10-20)

- `config/scheduler_state.json` guarda por clave `{origin: fecha-de-ocurrencia}`
  con `origin` = `natural` (ventana del cron) o `forced` (manual/`--force`/
  `--now` de prueba). El dedup solo bloquea una repeticiÃ³n del MISMO
  origen+ocurrencia: un forzado/de prueba nunca veta la ventana natural, y una
  natural no bloquea la de maÃ±ana.
- `config/form_update_state.json` registra cada write con su origen. El watchdog
  cuenta solo writes `natural`: un write forzado/de prueba no reinicia el reloj
  de 26 h ni enmascara un updater natural silencioso.
- Antes de escribir, el updater hace polling del `latest.json` desplegado en
  Pages (cada 30 s, hasta ~5 min) hasta que tenga las lecturas de hoy y un
  `generated_at` reciente. Si sigue stale, el run falla con un `/fail` explÃ­cito
  del form-updater. OpciÃ³n elegida: polling (no un gap de 30 min en la hoja),
  para que el gap ETLâ†’updater de 15 min sea seguro aunque Pages estÃ© desplegando.

Tokens de dedup: natural = fecha Bogota; forzado/prueba = timestamp exacto de
ocurrencia. Los vetos naturales se loguean como WARNING y se incluyen en el body
del ping del scheduler (`VETOED_NATURAL=...`); los forzados/de prueba nunca vetan
la ventana natural.
