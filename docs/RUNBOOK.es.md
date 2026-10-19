# Runbook

**[English](RUNBOOK.md) | Español**

Procedimientos operativos para el ETL diario, el sync del libro y el form updater.

## 1. Diagnosticar un ping `/fail` de Healthchecks

1. Abre el panel de Healthchecks y lee el cuerpo del check. Contiene la tabla
   markdown de fallos (indicador, fuente, error, valor forward-fill, status) o
   `crash en paso <paso>`.
2. Abre el run de GitHub Actions enlazado (el ETL incluye `GITHUB_RUN_URL` en el
   cuerpo del ping). Revisa el log del paso que falló:
   - `Run ETL` → imprime los errores por indicador.
   - `Healthchecks report ping` → el reporte del run tenía fallos.
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
   `reason`; el ETL ya pingueó `/fail`. No re-ejecutes solo para silenciarlo.
5. Si necesitas re-ejecutar: Actions → **Scrape** → Run workflow, o
   `python scripts/run_etl.py`. Verifica `smoke: 18/18` en el log.

## 2. Revisar o restaurar el Historial de versiones del libro

1. Abre el libro, `Archivo → Historial de versiones → Ver historial de versiones`.
2. Selecciona la versión anterior a la mala edición; el panel derecho muestra
   el diff.
3. `Restaurar esta versión` (cubre ambas pestañas; los permisos aplican a todo
   el libro).
4. El siguiente `sync_config.yml` (cada hora, `7 * * * *` UTC) lo recoge. Para
   actuar de inmediato: Actions → **Sync config** → Run workflow.
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
2. Espera el sync horario o dispara **Sync config**.
3. El updater deja de escribir ese registro, pero la variable sigue disponible
   como fallback de otras filas (el resolvedor lee el contrato del ETL).
4. Para reanudar, vuelve a `activo=TRUE`.

## 6. Activar el scheduler dinámico (gate)

No descomentes `schedule` en `scheduler.yml` hasta:
1. libro público como Lector (ambas pestañas);
2. `FORM_URL` y todos los secrets `HEALTHCHECKS_URL*` creados;
3. `SHEET_ID` en `config/form_updater.yaml`;
4. tabla de fallbacks aprobada;
5. dry-run con `repr()` y sources aprobado;
6. write real de 3 registros verificado en vivo y `records.json` aprobado.
Tras un dispatch del scheduler verde (scrape 18/18 → form-updater escribe →
pings success), descomenta `*/5 * * * *` y haz push.

## 7. Otras notas operativas

- **Retrasos de cron:** GitHub puede retrasar workflows programados por horas.
  El smoke del scrape valida `artifacts/run_report.json` (no re-scrapea), así que
  un forward-fill válido no pone el run rojo. El ping `/fail` del ETL es la
  fuente de alerta.
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

## 8. Retrasos del scheduler y watchdog del form updater (2026-10-19)

- GitHub throttlea los crons de alta frecuencia: el scheduler `*/5` corrió en
  realidad cada 2-5 h, así que la ventana de 5 min solía perder los slots
  21:00/21:30 de Bogotá (corridas observadas a las 23:51 y 01:56 Bogotá, ambas
  con "no window match").
- El scheduler ahora hace catch-up: una ocurrencia de hasta `catchup_hours` (8)
  sigue siendo debida; `config/scheduler_state.json` deduplica por fecha de
  ocurrencia; si ambos slots caen en una corrida atrasada, dispara el ETL,
  espera a que termine el scrape y luego dispara el updater (nunca a la vez).
- Watchdog: el sync horario ejecuta `scripts/watchdog.py`. Si la última
  escritura en `config/form_update_state.json` tiene más de 26 h o las últimas 2
  corridas escribieron 0 valores, envía un `/fail` explícito al check
  form-updater (y el job de sync queda rojo). El periodo/grace del check de
  Healthchecks sigue siendo la alarma de "sin ping en N horas".
- Prueba manual: Actions -> Scheduler -> Run workflow con `force=etl|updater`, o
  `now=<ISO8601>` para simular una ventana. La pestaña `ajustes` manda.
- No agregues un cron propio a `scrape.yml`/`form_update.yml`: el scheduler es el
  único disparador automático (evita dobles corridas y saltarse la hoja).
