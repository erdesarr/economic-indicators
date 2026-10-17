# Runbook

**English | [Español](RUNBOOK.es.md)**

Operational procedures for the daily ETL, the sheet sync and the form updater.

## 1. Diagnose a Healthchecks `/fail` ping

1. Open the Healthchecks dashboard and read the check body. It contains either
   the markdown failure table (indicator, source, error, forward-fill value,
   status) or `crash en paso <step>`.
2. Open the linked GitHub Actions run (`GITHUB_RUN_URL` is in the ping body for
   ETL failures). Inspect the failing step log:
   - `Run ETL` → the per-indicator errors are printed.
   - `Healthchecks report ping` → the run report had failures.
   - `Smoke ...` → the ETL report was incomplete (missing indicators or a
     `failed` status).
3. Classify and act:
   - **Label not found** (La República changed its HTML): run
     `python scripts/validate_live.py --print-only`, inspect the raw fragment,
     update `config/indicators.yaml` (`visible_label`/alternatives) or the
     source parser, add a golden test.
   - **investing.com 403**: Cloudflare; the ETL retries with backoff and
     forward-fills. Check the artifact screenshots (`raw-artifacts-<run>`).
   - **Banrep TLS**: the pinned intermediate expired or the chain changed; see
     `config/certs/README.md` and renew the pin (the cert test fails CI <30 days).
   - **BCB 406**: bounded date window issue; check `build_query_url`.
   - **gviz/sync failure**: the book may be private or renamed; verify the share
     setting and tab names; `sync_config.yml` keeps the last valid snapshot.
   - **Scheduler cron invalid**: fix the `ajustes` value; the `/fail` body names
     the setting. The scheduler ignores the invalid schedule.
4. Forward-fill is valid data: the DB row is `status='forward_filled'` with
   `reason`; the ETL already pinged `/fail`. Do not re-run just to silence it.
5. Re-run manually if needed: Actions → **Scrape** → Run workflow, or
   `python scripts/run_etl.py`. Then verify `smoke: 18/18` in the log.

## 2. Review or restore the Google Sheet version history

1. Open the book, `File → Version history → See version history`.
2. Select the version before the bad edit; the right panel shows the diff.
3. `Restore this version` (both tabs are covered; permissions apply to the whole
   book).
4. The next `sync_config.yml` run (hourly `7 * * * *` UTC) picks it up. To act
   immediately: Actions → **Sync config** → Run workflow.
5. Confirm `sync summary` in the log and the new `config/sheet_config.csv`
   commit.

## 3. Regenerate `tests/golden/webform/records.json`

1. Actions → **Form update** → Run workflow with `mode=capture` (optionally
   `slugs` to capture a subset; empty = all active records).
2. The workflow reads `Nombre` for each `id_indicador`, writes
   `tests/golden/webform/records.json` and commits it as the bot.
3. Review the mapping (id → visible Nombre) in the run log/commit and approve it
   (record the approval in `docs/APPROVALS.md`).
4. The real write mode refuses any record whose `Nombre` does not match this
   golden file.

## 4. Run a manual dry-run

1. Actions → **Form update** → Run workflow with `mode=dry-run` and optional
   `slugs` (e.g. `40,35,41`). Nothing is written.
2. Read the log table (values in `repr()` so spaces/symbols are visible):
   `id | indicador | source | value | variation | formatted | reason`.
3. `source=primary` is the happy path; `fallback`/`forward_filled` and
   `discarded: ... excede umbral ...` explain degradations.
4. Local alternative:
   `python scripts/run_form_updater.py --mode dry-run --slugs 40,35,41`.
5. Only after reviewing the dry-run, run `mode=write` (and only for the
   approved subset during gate validation).

## 5. Pause an indicator (`activo=FALSE`)

1. In `indicators_config`, set the row's `activo` checkbox to FALSE. Do not
   delete the row.
2. Wait for the hourly sync or dispatch **Sync config**.
3. The updater stops writing that record but the variable remains available as
   a fallback for other rows (the resolver reads the ETL contract).
4. To resume, set `activo=TRUE`.

## 6. Activate the dynamic scheduler (gate)

Do not uncomment `schedule` in `scheduler.yml` until:
1. the book is public as Reader (both tabs);
2. `FORM_URL` and all `HEALTHCHECKS_URL*` secrets exist;
3. `SHEET_ID` is in `config/form_updater.yaml`;
4. the fallback table is approved;
5. a dry-run with `repr()` and sources is approved;
6. a real write of 3 records was verified live and `records.json` is approved.
After a green scheduler dispatch (scrape 18/18 → form-updater writes → success
pings), uncomment `*/5 * * * *` and push.

## 7. Other operational notes

- **Cron delays:** GitHub may delay scheduled workflows by hours. The scrape
  smoke validates `artifacts/run_report.json` (not a re-scrape), so valid
  forward-filled data does not turn the run red. The ETL's `/fail` ping is the
  alert source.
- **Stale data:** `source_date` repeated across two `ok` runs triggers a warning
  in the success ping body.
- **Pages:** `pages.yml` deploys `docs/api/v1` after every successful Scrape
  run or manually. Custom cache headers are not configurable on GitHub Pages
  (default `max-age=600`); use FastAPI if `no-cache` is required.
- **Database:** `data/indicadores.db` is committed by the workflows; git is the
  backup. Pruning keeps 31 natural days and runs `VACUUM` after each ETL run.
- **Cert pin:** `tests/unit/test_certs.py` fails CI when the pinned Banrep
  intermediate expires in <30 days; renew it from the AIA URL and update the
  header comment.
