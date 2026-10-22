# Runbook

**[English](RUNBOOK.md) | EspaÃ±ol**

Procedimientos operativos para el ETL diario, el sync del libro y el form updater.

## 1. Diagnosticar un ping `/fail` de Healthchecks

1. Abre el panel de Healthchecks y lee el cuerpo del check. Contiene la tabla
   markdown de fallos (indicador, fuente, error, valor forward-fill, status) o
   `crash en paso <paso>`.
2. Abre el run de GitHub Actions enlazado (el ETL incluye `GITHUB_RUN_URL` en el
   cuerpo del ping). Revisa el log del paso que fallÃ³:
   - `Run ETL` â†’ imprime los errores por indicador.
   - `Healthchecks report ping` â†’ el reporte del run tenÃ­a fallos.
   - `Smoke ...` â†’ el reporte del ETL estaba incompleto (faltan indicadores o
     algÃºn status `failed`).
3. Clasifica y actÃºa:
   - **Label not found** (La RepÃºblica cambiÃ³ su HTML): ejecuta
     `python scripts/validate_live.py --print-only`, revisa el fragmento crudo,
     ajusta `config/indicators.yaml` (`visible_label`/alternativas) o el parser,
     y aÃ±ade un test golden.
   - **investing.com 403**: Cloudflare; el ETL reintenta con backoff y aplica
     forward-fill. Revisa los screenshots del artifact (`raw-artifacts-<run>`).
   - **TLS Banrep**: el intermedio pinneado venciÃ³ o cambiÃ³ la cadena; mira
     `config/certs/README.md` y renueva el pin (el test falla en CI <30 dÃ­as).
   - **BCB 406**: problema de ventana de fechas; revisa `build_query_url`.
   - **gviz/sync**: el libro puede haberse vuelto privado o renombrado; verifica
     el permiso y los nombres de pestaÃ±as; el sync conserva el Ãºltimo snapshot.
   - **Cron del scheduler invÃ¡lido**: corrige el valor en `ajustes`; el cuerpo
     del `/fail` nombra la clave. El scheduler ignora ese horario.
4. El forward-fill es dato vÃ¡lido: la fila queda `status='forward_filled'` con
   `reason`; el ETL ya pingueÃ³ `/fail`. No re-ejecutes solo para silenciarlo.
5. Si necesitas re-ejecutar: Actions â†’ **Scrape** â†’ Run workflow, o
   `python scripts/run_etl.py`. Verifica `smoke: 18/18` en el log.

## 2. Revisar o restaurar el Historial de versiones del libro

1. Abre el libro, `Archivo â†’ Historial de versiones â†’ Ver historial de versiones`.
2. Selecciona la versiÃ³n anterior a la mala ediciÃ³n; el panel derecho muestra
   el diff.
3. `Restaurar esta versiÃ³n` (cubre ambas pestaÃ±as; los permisos aplican a todo
   el libro).
4. El siguiente `sync_config.yml` (cada hora, `7 * * * *` UTC) lo recoge. Para
   actuar de inmediato: Actions â†’ **Sync config** â†’ Run workflow.
5. Confirma `sync summary` en el log y el nuevo commit de
   `config/sheet_config.csv`.

## 3. Regenerar `tests/golden/webform/records.json`

1. Actions â†’ **Form update** â†’ Run workflow con `mode=capture` (opcional
   `slugs` para un subconjunto; vacÃ­o = todos los activos).
2. El workflow lee el `Nombre` de cada `id_indicador`, escribe
   `tests/golden/webform/records.json` y lo commitea como bot.
3. Revisa el mapeo (id â†’ Nombre visible) en el log/commit y apruÃ©balo (registra
   la aprobaciÃ³n en `docs/APPROVALS.md`).
4. El modo write rechaza cualquier registro cuyo `Nombre` no coincida con este
   golden.

## 4. Ejecutar un dry-run manual

1. Actions â†’ **Form update** â†’ Run workflow con `mode=dry-run` y `slugs`
   opcional (p. ej. `40,35,41`). No escribe nada.
2. Lee la tabla del log (valores con `repr()` para ver espacios/sÃ­mbolos):
   `id | indicador | source | value | variation | formatted | reason`.
3. `source=primary` es el camino feliz; `fallback`/`forward_filled` y
   `discarded: ... excede umbral ...` explican degradaciones.
4. Alternativa local:
   `python scripts/run_form_updater.py --mode dry-run --slugs 40,35,41`.
5. Solo tras revisar el dry-run, ejecuta `mode=write` (y durante el gate, solo
   el subconjunto aprobado).

## 5. Pausar un indicador (`activo=FALSE`)

1. En `indicators_config`, marca `activo=FALSE` en la fila. No borres la fila.
2. Espera el sync horario o dispara **Sync config**.
3. El updater deja de escribir ese registro, pero la variable sigue disponible
   como fallback de otras filas (el resolvedor lee el contrato del ETL).
4. Para reanudar, vuelve a `activo=TRUE`.

## 6. Activar el scheduler dinÃ¡mico (gate)

No descomentes `schedule` en `scheduler.yml` hasta:
1. libro pÃºblico como Lector (ambas pestaÃ±as);
2. `FORM_URL` y todos los secrets `HEALTHCHECKS_URL*` creados;
3. `SHEET_ID` en `config/form_updater.yaml`;
4. tabla de fallbacks aprobada;
5. dry-run con `repr()` y sources aprobado;
6. write real de 3 registros verificado en vivo y `records.json` aprobado.
Tras un dispatch del scheduler verde (scrape 18/18 â†’ form-updater escribe â†’
pings success), descomenta `0 * * * *` y haz push.

## 7. Otras notas operativas

- **Retrasos de cron:** GitHub puede retrasar workflows programados por horas.
  El smoke del scrape valida `artifacts/run_report.json` (no re-scrapea), asÃ­ que
  un forward-fill vÃ¡lido no pone el run rojo. El ping `/fail` del ETL es la
  fuente de alerta.
- **Dato stale:** `source_date` repetida en dos runs `ok` genera un warning en
  el cuerpo del ping de Ã©xito.
- **Pages:** `pages.yml` despliega `docs/api/v1` tras cada Scrape exitoso o
  manual. GitHub Pages no permite cabeceras custom de cachÃ© (por defecto
  `max-age=600`); usa FastAPI si necesitas `no-cache`.
- **Base de datos:** `data/indicadores.db` la commitean los workflows; git es el
  backup. La poda conserva 31 dÃ­as naturales y hace `VACUUM` en cada run.
- **Pin TLS:** `tests/unit/test_certs.py` falla en CI cuando el intermedio
  pinneado de Banrep vence en <30 dÃ­as; renuÃ©valo desde la URL AIA y actualiza el
  comentario de cabecera.

## 8. Retrasos del scheduler y watchdog del form updater (2026-10-19)

- GitHub throttlea los crons de alta frecuencia: el scheduler `*/5` corriÃ³ en
  realidad cada 2-5 h, asÃ­ que la ventana de 5 min solÃ­a perder los slots
  21:00/21:30 de BogotÃ¡ (corridas observadas a las 23:51 y 01:56 BogotÃ¡, ambas
  con "no window match").
- El scheduler ahora hace catch-up: una ocurrencia de hasta `catchup_hours` (8)
  sigue siendo debida; `config/scheduler_state.json` deduplica con token
  origin-aware; si ambos slots caen en una corrida, dispara el ETL, espera a que
  termine el scrape y luego dispara el updater (nunca a la vez).
- Desde 2026-10-20 el cron es **horario en punto** (`0 * * * *` UTC): una
  ventana que no cae en punto se ejecuta en la siguiente corrida horaria
  (máx ~59 min). Un cambio de horario en la hoja tarda hasta ~1 h (sync horario
  + scheduler horario); si urge, dispara `sync_config.yml` y `scheduler.yml` a
  mano. Check `scheduler` de Healthchecks: periodo horario con **~2 h de grace**
  (lo configura el dueño en la UI; el código no puede).
- Watchdog: el sync horario ejecuta `scripts/watchdog.py`. Si la Ãºltima
  escritura en `config/form_update_state.json` tiene mÃ¡s de 26 h o las Ãºltimas 2
  corridas escribieron 0 valores, envÃ­a un `/fail` explÃ­cito al check
  form-updater (y el job de sync queda rojo). El periodo/grace del check de
  Healthchecks sigue siendo la alarma de "sin ping en N horas".
- Prueba manual: Actions -> Scheduler -> Run workflow con `force=etl|updater`, o
  `now=<ISO8601>` para simular una ventana. La pestaÃ±a `ajustes` manda.
- No agregues un cron propio a `scrape.yml`/`form_update.yml`: el scheduler es el
  Ãºnico disparador automÃ¡tico (evita dobles corridas y saltarse la hoja).

### Dedup por origen y readiness de Pages (2026-10-20)

- Los disparos forzados/de prueba se guardan como `origin=forced`; las ventanas
  naturales como `origin=natural`. Solo se deduplica la repeticiÃ³n del mismo
  origen+ocurrencia, asÃ­ que un forzado nunca se come la ventana natural (y el
  watchdog ignora los writes forzados).
- El updater hace polling del contrato desplegado en Pages (30 s, hasta 5 min)
  antes de escribir; un gap de 15 min entre ETL y updater es seguro. Si sigue
  stale tras el polling -> `/fail` explÃ­cito del form-updater.

### Detalle del token de dedup y grace de Healthchecks (2026-10-20)

- Las ocurrencias NATURALES se deduplican por **fecha America/Bogota** (doble
  natural el mismo dÃ­a se veta). Las forzadas/de prueba (`--force` o `--now`) se
  deduplican por **timestamp exacto de ocurrencia**, asÃ­ que un forzado ad-hoc
  temprano no bloquea el test de 11:35 y el test de 12:00 no re-ejecuta el ETL
  de 11:35.
- Cuando se veta una ocurrencia NATURAL, el scheduler imprime
  `WARNING: natural occurrence vetoed by dedup: ...` y agrega
  `VETOED_NATURAL=[...]` al body del ping success de Healthchecks. Un veto nunca
  es silencioso.
- Los periods/grace de Healthchecks deben reflejar la cadencia throttled (el
  dueÃ±o los configura en la UI; el cÃ³digo no puede): `scrape` y `form-updater`
  diarios con **~6 h de grace**, `scheduler` **~2 h**, `sync` **~2 h**. El
  watchdog del form-updater agrega el `/fail` explÃ­cito cuando no hay write
  natural en 26 h o dos corridas naturales seguidas escribieron 0.
