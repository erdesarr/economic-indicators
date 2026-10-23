# Runbook

**[English](RUNBOOK.md) | Español**

Procedimientos operativos para el ETL diario, el sync del libro y el form updater.

## 1. Diagnosticar un ping `/fail` de Healthchecks

1. Abre el panel de Healthchecks y lee el cuerpo del check. Contiene la tabla
   markdown de fallos (indicador, fuente, error, valor forward-fill, status), la
   lista de forward-fills esperados o `crash en paso <paso>`.
2. Abre el run de GitHub Actions enlazado (el ETL incluye `GITHUB_RUN_URL` en el
   cuerpo del ping). Revisa el log del paso que falló:
   - `Run ETL` → imprime los errores por indicador.
   - `Healthchecks report ping` → el reporte del run tenía un fallo inesperado
     (dentro del `publication_window` del indicador). Los forward-fills
     esperados aparecen como warnings en el body del success.
   - `Smoke ...` → el reporte del ETL estaba incompleto (faltan indicadores o
     algún status `failed`).
3. Clasifica y actúa:
   - **Label not found** (La República cambió su HTML): ejecuta
     `python scripts/validate_live.py --print-only`, revisa el fragmento crudo,
     ajusta `config/indicators.yaml` (`visible_label`/alternativas) o el parser,
     y añade un test golden.
   - **investing.com 403**: Cloudflare; el ETL reintenta con backoff y aplica
     forward-fill. Revisa los screenshots del artifact (`raw-artifacts-<run>`).
   - **TLS Banrep**: el intermedio pinneado venció o cambió la cadena; mira
     `config/certs/README.md` y renueva el pin (el test falla en CI <30 días).
   - **BCB 406**: problema de ventana de fechas; revisa `build_query_url`.
   - **gviz/sync**: el libro puede haberse vuelto privado o renombrado; verifica
     el permiso y los nombres de pestañas; el sync conserva el último snapshot.
   - **Cron del scheduler inválido**: corrige el valor en `ajustes`; el cuerpo
     del `/fail` nombra la clave. El scheduler ignora ese horario.
4. El forward-fill es dato válido: la fila queda `status='forward_filled'` con
   `reason`. El check del scrape va a `/fail` solo si el fallo está dentro de su
   `publication_window`; fuera de la ventana el ping es success con warning. No
   re-ejecutes solo para silenciar un warning esperado; dentro de la ventana
   trátalo como fuente caída.
5. Si necesitas re-ejecutar: Actions → **Scrape** → Run workflow, o
   `python scripts/run_etl.py`. Verifica `smoke: 18/18` en el log.

### Check del scrape: forward-fill esperado vs inesperado

Cada indicador tiene `publication_window` (Bogotá `HH:MM-HH:MM`) en
`config/indicators.yaml`. `dolar_oficial_manana` es `15:00-20:00`; el resto usa
`00:00-23:59` por defecto.

- Fallo/forward-fill **dentro** de la ventana → `/fail` con tabla markdown (la
  fuente está caída cuando debería estar publicada).
- Fallo/forward-fill **fuera** de la ventana → `/success` con
  `Aviso: forward-fill esperado fuera de ventana de publicación` y la lista.
  Es lo normal para `dolar_oficial_manana` antes de 15:00 o después de 20:00.
- Todo fresco → `/success`; dato stale → `/success` con warning; crash →
  `/fail` vía `if: failure()`.
- El smoke sigue aceptando `forward_filled` como presencia (no re-scrapea).

Para probar la política sin esperar la hora, dispara **Scrape** con las test
aids `etl_now` (ISO8601 con offset, p. ej. `2026-10-22T16:00:00-05:00`) y
opcionalmente `etl_force_fail` (`dolar_oficial_manana`); ambas vacías en
producción. `etl_force_fail` marca el slug como fallido para probar la decisión
dentro/fuera de ventana de forma determinista. El run report guarda la
clasificación en `artifacts/run_report.json` (`expected`, `publication_window`,
`run_at`) y el paso `Healthchecks report ping` imprime la decisión y el body.

## 2. Revisar o restaurar el Historial de versiones del libro

1. Abre el libro, `Archivo → Historial de versiones → Ver historial de versiones`.
2. Selecciona la versión anterior a la mala edición; el panel derecho muestra
   el diff.
3. `Restaurar esta versión` (cubre ambas pestañas; los permisos aplican a todo
   el libro).
4. El `onChange` de Apps Script (o el siguiente respaldo de `sync_config.yml`
   cada 6 h) lo recoge. Para actuar de inmediato: Actions → **Sync config** →
   Run workflow.
5. Confirma `sync summary` en el log y el nuevo commit de
   `config/sheet_config.csv`.

## 3. Regenerar `tests/golden/webform/records.json`

1. Actions → **Form update** → Run workflow con `mode=capture` (opcional
   `slugs` para un subconjunto; vacío = todos los activos).
2. El workflow lee el `Nombre` de cada `id_indicador`, escribe
   `tests/golden/webform/records.json` y lo commitea como bot.
3. Revisa el mapeo (id → Nombre visible) en el log/commit y apruébalo (registra
   la aprobación en `docs/APPROVALS.md`).
4. El modo write rechaza cualquier registro cuyo `Nombre` no coincida con este
   golden.

## 4. Ejecutar un dry-run manual

1. Actions → **Form update** → Run workflow con `mode=dry-run` y `slugs`
   opcional (p. ej. `40,35,41`). No escribe nada.
2. Lee la tabla del log (valores con `repr()` para ver espacios/símbolos):
   `id | indicador | source | value | variation | formatted | reason`.
3. `source=primary` es el camino feliz; `fallback`/`forward_filled` y
   `discarded: ... excede umbral ...` explican degradaciones.
4. Alternativa local:
   `python scripts/run_form_updater.py --mode dry-run --slugs 40,35,41`.
5. Solo tras revisar el dry-run, ejecuta `mode=write` (y durante el gate, solo
   el subconjunto aprobado).

## 5. Pausar un indicador (`activo=FALSE`)

1. En `indicators_config`, marca `activo=FALSE` en la fila. No borres la fila.
2. El `onChange` de Apps Script despacha `config_refresh.yml` (sync + write); si
   el reloj no está disponible, espera el respaldo de `sync_config.yml` (6 h) o
   dispara **Sync config** a mano.
3. El updater deja de escribir ese registro, pero la variable sigue disponible
   como fallback de otras filas (el resolvedor lee el contrato del ETL).
4. Para reanudar, vuelve a `activo=TRUE`.

## 6. Activación del scheduler (gate histórico, ya activo)

El gate está cerrado: libro público como Lector, `FORM_URL` y todos los secrets
`HEALTHCHECKS_URL*` creados, `SHEET_ID` configurado, fallbacks/dry-run
aprobados y write real de 3 registros + `tests/golden/webform/records.json`
aprobados. El scheduler está activo.

El schedule de GitHub es ahora solo el **respaldo de 6 h** (`0 */6 * * *`); el
camino primario puntual es el reloj Apps Script (sección 9). No agregues un cron
a `scrape.yml` ni a `form_update.yml`.

## 7. Otras notas operativas

- **Retrasos de cron:** GitHub puede retrasar workflows programados por horas.
  El smoke del scrape valida `artifacts/run_report.json` (no re-scrapea), así que
  un forward-fill válido no pone el run rojo. El ping `/fail` del ETL es la
  fuente de alerta; los forward-fills esperados fuera de su ventana son
  warnings en el success, no fallos.
- **Dato stale:** `source_date` repetida en dos runs `ok` genera un warning en
  el cuerpo del ping de éxito.
- **Pages:** `pages.yml` despliega `docs/api/v1` tras cada Scrape exitoso o
  manual. GitHub Pages no permite cabeceras custom de caché (por defecto
  `max-age=600`); usa FastAPI si necesitas `no-cache`.
- **Base de datos:** `data/indicadores.db` la commitean los workflows; git es el
  backup. La poda conserva 31 días naturales y hace `VACUUM` en cada run.
- **Pin TLS:** `tests/unit/test_certs.py` falla en CI cuando el intermedio
  pinneado de Banrep vence en <30 días; renuévalo desde la URL AIA y actualiza el
  comentario de cabecera.

## 8. Scheduler, reloj Apps Script y watchdog del form updater

- Historia: GitHub throttleó los crons `*/5` y horarios (corridas cada 2-5 h),
  así que el scheduler horario de 2026-10-20 seguía sin ser puntual. Desde
  2026-10-22 el **camino primario es el reloj Apps Script** (sección 9).
- El scheduler conserva el cerebro: una ocurrencia de hasta `catchup_hours` (8)
  sigue siendo debida; `config/scheduler_state.json` deduplica con token
  origin-aware; si ambos slots caen en una corrida, dispara el ETL, espera a que
  termine el scrape y luego dispara el updater (nunca a la vez).
- Respaldo: `scheduler.yml` y `sync_config.yml` corren cada **6 h**
  (`0 */6 * * *` UTC = 00/06/12/18 UTC; el respaldo de 19:00 Bogotá coincide
  con la ventana del ETL). El catch-up de 8 h cubre una ventana perdida. El
  respaldo y el reloj se deduplican por ocurrencia, así que ambos pueden
  dispararse sin duplicar ETL/updater.
- Ediciones de la hoja: el `onChange` instalable (EDIT, throttle 5 min) despacha
  `config_refresh.yml`, que sincroniza la hoja y refresca el formulario de
  inmediato. El `sync_config.yml` de 6 h es el respaldo de snapshot/watchdog.
- Watchdog: el sync de 6 h ejecuta `scripts/watchdog.py`. Si la última escritura
  natural en `config/form_update_state.json` tiene más de 26 h o las últimas 2
  corridas naturales escribieron 0 valores, envía un `/fail` explícito al check
  form-updater (y el job de sync queda rojo). Los writes forzados
  (manual/test/onChange) no resetean el reloj.
- Prueba manual: Actions -> Scheduler -> Run workflow con `force=etl|updater`, o
  `now=<ISO8601>` para simular una ventana. La pestaña `ajustes` manda. Apps
  Script tiene su propio `testDispatch()` manual.
- No agregues un cron propio a `scrape.yml`/`form_update.yml`: el scheduler es el
  único disparador automático de ETL/updater (evita dobles corridas y saltarse
  la hoja).

### Dedup por origen y readiness de Pages (2026-10-20)

- Los disparos forzados/de prueba se guardan como `origin=forced`; las ventanas
  naturales como `origin=natural`. Solo se deduplica la repetición del mismo
  origen+ocurrencia, así que un forzado nunca se come la ventana natural (y el
  watchdog ignora los writes forzados).
- El updater hace polling del contrato desplegado en Pages (30 s, hasta 5 min)
  antes de escribir; un gap de 15 min entre ETL y updater es seguro. Si sigue
  stale tras el polling -> `/fail` explícito del form-updater.

### Detalle del token de dedup y grace de Healthchecks (2026-10-20)

- Las ocurrencias NATURALES se deduplican por **fecha America/Bogota** (doble
  natural el mismo día se veta). Las forzadas/de prueba (`--force` o `--now`) se
  deduplican por **timestamp exacto de ocurrencia**, así que un forzado ad-hoc
  temprano no bloquea el test de 11:35 y el test de 12:00 no re-ejecuta el ETL
  de 11:35.
- Cuando se veta una ocurrencia NATURAL, el scheduler imprime
  `WARNING: natural occurrence vetoed by dedup: ...` y agrega
  `VETOED_NATURAL=[...]` al body del ping success de Healthchecks. Un veto nunca
  es silencioso.
- Periods/grace de Healthchecks (el dueño los configura en la UI; el código no
  puede): `appscript-clock` **5 min / 30 min grace**; `scheduler`
  **6 h / 2 h grace**; `sync` **6 h / 2 h grace**; `scrape` y `form-updater`
  sin cambio. Un trigger de Apps Script muerto = sin ping del reloj; el respaldo
  de 6 h cubre el lado de Actions mientras alerta. El watchdog del form-updater
  agrega el `/fail` explícito cuando no hay write natural en 26 h o dos corridas
  naturales seguidas escribieron 0.

## 9. Reloj Apps Script (camino primario puntual, instalado por el dueño)

El script container-bound está versionado en [`apps_script/Code.gs`](../apps_script/Code.gs).
Es un dispatcher fino: lee la pestaña `ajustes` en vivo solo para decidir
**cuándo** llamar a GitHub; el scheduler sigue decidiendo ventanas, catch-up,
dedup, frescura y el orden ETL→updater.

### Instalación / reinstalación

1. Abre el libro → **Extensiones → Apps Script**.
2. Reemplaza el contenido del editor con `apps_script/Code.gs` (o pega el archivo
   actualizado tras un cambio) y guarda.
3. En **Configuración del proyecto → Propiedades del script**, define:
   - `GH_PAT`: PAT fine-grained, **solo este repo**, **Actions: write** (lo
     necesita el endpoint de dispatch).
   - `APPSCRIPT_HEALTHCHECKS_URL`: URL completa de ping del check
     `appscript-clock`.
   - Opcional: `SCHEDULER_DISPATCH_URL`, `CONFIG_REFRESH_DISPATCH_URL`,
     `SHEET_ID`. `setupTriggers()` completa las dos URLs y `SHEET_ID` con los
     valores por defecto si faltan.
4. Ejecuta `setupTriggers()` una vez y autoriza los scopes (hoja de cálculo,
   requests externos, triggers). Borra los triggers previos
   `clockTick`/`onSheetChange` y crea:
   - `clockTick` time-driven cada 5 min;
   - `onSheetChange` instalable (`EDIT`) sobre este libro.
5. Ejecuta `clockTick()` a mano para probar la lectura de la hoja + ping de
   liveness, y `testDispatch()` para probar el PAT/URL (esto despacha un
   scheduler real; el scheduler decide si hay ventana).
6. Confirma que `appscript-clock` recibe el ping en Healthchecks.

`setupTriggers()` es idempotente. Si `Code.gs` cambia en el repo, **vuelve a
pegarlo** en el editor; el repo es la fuente de verdad, pero Apps Script no lee
el repo.

### Comportamiento

- `clockTick`: lee `ajustes` en vivo; soporta el formato diario `m h * * *` (el
  actual). Si la hora de Bogotá cae en `[ventana, ventana+15 min)` y
  `${fecha}|${HH:mm}` no se despachó hoy, envía un `workflow_dispatch` a
  `scheduler.yml` (máx 1 por tick; ese run cubre las ventanas debidas) y marca
  la clave. Fuera de ventana no despacha nada. **Siempre** pingea
  `appscript-clock` (success, o `/fail` con errores de auth/lectura/dispatch).
- `onSheetChange`: solo `EDIT`, con throttle a un dispatch de
  `config_refresh.yml` cada 5 min. `config_refresh` sincroniza las pestañas en
  vivo y ejecuta el form updater en write (todas las salvaguardas existentes).
- Otros formatos de cron (semanal, etc.) no los despacha el reloj; el respaldo
  de 6 h usa croniter y catch-up (8 h), así que igual corren.
- El scheduler lee el **snapshot commiteado**, no la hoja viva. Una edición
  refresca el snapshot vía `config_refresh`; un cambio de horario se refleja en
  el siguiente tick del reloj tras ese sync.

### Modos de fallo y cobertura del respaldo

- **Trigger muerto / Apps Script deshabilitado:** sin ping de
  `appscript-clock`; Healthchecks alerta a los 30 min. El respaldo de 6 h de
  scheduler/sync mantiene el ETL y el formulario (catch-up 8 h).
- **Autorización vencida o scope revocado:** el trigger loguea el error y el
  reloj pingea `/fail` (si aún puede hacer fetch); vuelve a ejecutar
  `setupTriggers()` y reautoriza.
- **PAT rotado/revocado o sin Actions:write:** el dispatch devuelve 401/403; el
  reloj pingea `/fail` (o lo deja en el body del tick). Actualiza `GH_PAT` en
  ScriptProperties; el respaldo de 6 h cubre mientras tanto.
- **Libro/pestaña renombrados o `SHEET_ID` incorrecto:** la lectura falla; el
  reloj pingea `/fail`. Corrige la propiedad/pestaña; el respaldo de 6 h sigue.
- **Acceso a ScriptProperties:** el PAT es visible para quien pueda editar el
  proyecto de Apps Script. Rótalo al cambiar integrantes. Nunca pongas el PAT
  en el repo ni en la hoja.
- **Cuota de Actions:** 2 ventanas/día + ediciones + cuatro respaldos de 6 h;
  esperado <~30 runs/día, muy por debajo de 2,000 min/mes. Los 288 ticks/día
  del reloj solo leen la hoja y pingean.
