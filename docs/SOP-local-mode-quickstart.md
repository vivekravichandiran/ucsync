# SOP — UC Sync LOCAL Mode (step by step)

Scope: same workspace / same metastore, catalog-to-catalog copy (e.g. `ril_sandbox` → `ril_sandbox_ucsync_local`).
Use this SOP end-to-end via the **job wrapper** (recommended) or directly via the
**main notebook** widgets (manual). Both paths are documented below with exact
values, followed by the SQL to check status/state.

> LOCAL mode never touches `source_*` / `target_*` workspace URL or OAuth
> widgets — leave them blank. The notebook's current-workspace context is used
> for both "source" and "target".

---

## 0. One-time setup

Run once per target workspace, in the target workspace SQL editor:

```sql
CREATE CATALOG IF NOT EXISTS classic_stable_target_vk;
CREATE SCHEMA IF NOT EXISTS classic_stable_target_vk.uc_sync_ops;
CREATE VOLUME IF NOT EXISTS classic_stable_target_vk.uc_sync_ops.uc_exports;
```

Do **not** hand-create `uc_sync_audit` / `uc_sync_state`. `AuditService.ensure_table()`
and `SyncStateService.ensure_table()` create them (and auto-upgrade older schemas)
on first write.

If you need a per-object external-storage mapping, upload the CSV once:

```text
/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv
```

Columns: `source_location,target_location,target_external_location,target_credential`
(see `docs/configuration.md` §"External location and table path mapping").

---

## 1. Recommended path — LOCAL job wrapper (minimal parameters)

Notebook:

```text
/Workspace/Users/<you>/UCSync/notebooks/UC_Sync_Local_Create_Jobs
```

This creates one Databricks Job per stage (or one end-to-end job) that runs
`UC_Sync_Main` for you — you never touch the 25+ widgets on the main notebook.

### 1.1 Widgets — exact values

| Widget | Required | Value |
|--------|----------|-------|
| `stages` | Yes | `INVENTORY` \| `EXPORT` \| `IMPORT` \| `ALL` \| `SYNC` |
| `catalog_mapping_json` | Yes | `{"ril_sandbox":"ril_sandbox_ucsync_local"}` |
| `location_mapping_csv_path` | Required if you have external tables/volumes | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv` |
| `catalogs` | Recommended | `ril_sandbox` |
| `schemas` | Optional | `ril_sandbox.ucsync_local_01` (blank = all schemas in `catalogs`) |
| `dry_run` | Yes | `true` first, then `false` |
| `run_now` | Yes | `false` to only create jobs, `true` to create **and** trigger them immediately |
| `existing_cluster_id` | Optional | blank = new single-node job cluster per job; or an existing cluster id, e.g. `0813-072811-phmehy1u` |
| `job_name_prefix` | Optional | `UC-Sync-Local` (default) |
| `notebook_path` | Optional | `/Workspace/Users/<you>/UCSync/notebooks/UC_Sync_Main` |
| `import_package_path` | Only for a repeat `IMPORT` of an already-migrated package | blank on first run |

`stages=ALL` creates 3 jobs: `<prefix>-Inventory`, `<prefix>-Export`, `<prefix>-Import`.
`stages=SYNC` creates one job named exactly `<prefix>` that runs inventory → export → import → validate in one notebook run.

### 1.2 Run order

1. Set widgets above with `dry_run=true`, `run_now=false`. **Run All.**
2. Confirm the exit JSON lists 3 (or 1) created jobs with the expected `job_id`s.
3. From **Jobs → Run now** (or set `run_now=true` and rerun the wrapper):
   - `<prefix>-Inventory` first.
   - Read the result JSON / report (see §3). If it looks right, run `<prefix>-Export`.
   - Take `migrated_workspace_path` from the Export result and pass it as the
     `import_package_path` job parameter when you run `<prefix>-Import`
     (Jobs UI → **Run now with different parameters**).
4. Repeat steps 1–3 with `dry_run=false` for the real run.
5. Query status with the SQL in §4.

---

## 2. Manual path — main notebook widgets directly

Use this if you want a single ad-hoc run without creating a Job, or need
`mode=SYNC`/`VALIDATE`/`COMPARE` in one shot.

Notebook: `/Workspace/Users/<you>/UCSync/notebooks/UC_Sync_Main`

### 2.0 Always-set widgets (every stage)

| Widget | Value |
|--------|-------|
| `execution_mode` | `LOCAL` |
| `catalog_mapping_json` | `{"ril_sandbox":"ril_sandbox_ucsync_local"}` |
| `catalogs` | `ril_sandbox` |
| `schemas` | blank, or e.g. `ril_sandbox.ucsync_local_01` |
| `components` | `ALL` |
| `include_parents` | `true` |
| `exclude_object_types` | `MODEL` |
| `exclude_regex` | `.*_TEMP$` |
| `location_mapping_csv_path` | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv` |
| `export_volume_path` | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports` |
| `report_volume_path` | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports` |
| `audit_table` | `classic_stable_target_vk.uc_sync_ops.uc_sync_audit` |
| `state_table` | `classic_stable_target_vk.uc_sync_ops.uc_sync_state` |
| `dry_run` | `true` then `false` |
| `source_*` / `target_*` (workspace URL, OAuth scope/keys) | **leave blank** |

### 2.1 Step 1 — `mode=INVENTORY`

Same as §2.0. Nothing else changes. **Run All.**

Check the exit JSON: `reports.inventory.html` / `.xlsx`.

### 2.2 Step 2 — `mode=EXPORT`

Same widgets as §2.0 with `mode=EXPORT`. This runs inventory + export + migrate
(regex rewrite of DDL to the mapped catalog/paths) and writes
`export_migrated_staging/<run_id>` — the source of truth for import.

Note `migrated_workspace_path` from the exit JSON; you need it for Step 3.

### 2.3 Step 3 — `mode=IMPORT`

Same widgets as §2.0 with `mode=IMPORT`, plus:

| Widget | Value |
|--------|-------|
| `import_package_path` | the `migrated_workspace_path` printed in Step 2 |

This executes DDL/grants, writes audit rows, upserts the state table, and
generates the import comparison report (Summary / Success / Failures / Manual
Action Required, HTML + XLSX).

### 2.4 One-shot alternative — `mode=SYNC`

Same widgets as §2.0 with `mode=SYNC` and no `import_package_path` (it chains
inventory → export → migrate → import → validate automatically in one run).

---

## 3. Where to find results

Every run prints/returns an exit JSON (`dbutils.notebook.exit(...)`) with a
`reports` block. Typical Volume path:

```text
/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/run_<YYYYMMDD_HHMMSS>/reports/
  inventory_report.html / .xlsx
  export_report.html / .xlsx
  import_report.html / .xlsx
  import_comparison_report.html / .xlsx   <- Summary / Success / Failures / Manual
  uc_sync_summary.html                    <- final combined report (SYNC only)
  uc_sync_detailed_report.xlsx
```

Each report also has a `*_no_source_metadata` twin with the bulky
`source_metadata` column stripped.

---

## 4. SQL — read status and state

Run in the **target** workspace SQL editor / warehouse. Table names below use
the LOCAL-mode defaults; swap in your own `audit_table` / `state_table` values
if you changed the widget.

### 4.1 Audit table — `classic_stable_target_vk.uc_sync_ops.uc_sync_audit`

**Latest run status by stage:**

```sql
SELECT run_id, operation_mode, status, count(*) AS objects
FROM classic_stable_target_vk.uc_sync_ops.uc_sync_audit
WHERE run_id = (
  SELECT run_id FROM classic_stable_target_vk.uc_sync_ops.uc_sync_audit
  ORDER BY created_at DESC LIMIT 1
)
GROUP BY run_id, operation_mode, status
ORDER BY operation_mode, status;
```

**All failures for a specific run:**

```sql
SELECT full_name, object_type, operation_mode, status, error_code, error_message
FROM classic_stable_target_vk.uc_sync_ops.uc_sync_audit
WHERE run_id = '<run_id>'
  AND status = 'FAILURE'
ORDER BY object_type, full_name;
```

**Objects needing manual action:**

```sql
SELECT full_name, object_type, ddl_path, error_message
FROM classic_stable_target_vk.uc_sync_ops.uc_sync_audit
WHERE run_id = '<run_id>'
  AND status = 'MANUAL_ACTION_REQUIRED';
```

**Full history for one object across every stage:**

```sql
SELECT run_id, operation_mode, status, error_code, created_at
FROM classic_stable_target_vk.uc_sync_ops.uc_sync_audit
WHERE full_name = 'ril_sandbox.ucsync_local_01.managed_table_01'
ORDER BY created_at;
```

**Overall run summary (last 10 runs):**

```sql
SELECT
  run_id,
  min(created_at) AS started_at,
  count(*) AS total_rows,
  sum(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS success,
  sum(CASE WHEN status = 'FAILURE' THEN 1 ELSE 0 END) AS failures,
  sum(CASE WHEN status = 'MANUAL_ACTION_REQUIRED' THEN 1 ELSE 0 END) AS manual,
  sum(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) AS pending
FROM classic_stable_target_vk.uc_sync_ops.uc_sync_audit
GROUP BY run_id
ORDER BY started_at DESC
LIMIT 10;
```

`status` values: `SUCCESS`, `FAILURE`, `PENDING`, `MANUAL_ACTION_REQUIRED`.
`operation_mode` values: `INVENTORY`, `EXPORT`, `MIGRATE`, `IMPORT`, `VALIDATE`.

### 4.2 State table — `classic_stable_target_vk.uc_sync_ops.uc_sync_state`

One row per object (merged on `source_full_name` + `object_type`) — this is
what future incremental syncs read.

**Current state of every object:**

```sql
SELECT object_type, source_full_name, target_full_name, last_sync_status,
       last_sync_at, last_synced_by, batch_id
FROM classic_stable_target_vk.uc_sync_ops.uc_sync_state
ORDER BY object_type, source_full_name;
```

**Objects out of sync (failed or never synced):**

```sql
SELECT object_type, source_full_name, target_full_name, last_sync_status,
       error_code, error_message, last_sync_at
FROM classic_stable_target_vk.uc_sync_ops.uc_sync_state
WHERE last_sync_status != 'SUCCESS'
ORDER BY last_sync_at DESC;
```

**Objects synced in the most recent batch:**

```sql
SELECT object_type, source_full_name, last_sync_status, last_sync_at
FROM classic_stable_target_vk.uc_sync_ops.uc_sync_state
WHERE batch_id = (
  SELECT batch_id FROM classic_stable_target_vk.uc_sync_ops.uc_sync_state
  ORDER BY last_sync_at DESC LIMIT 1
)
ORDER BY object_type, source_full_name;
```

**Drift check — source changed since last successful sync** (compares the
`source_definition_hash`/`source_last_modified_at` recorded at last sync against
a fresh inventory row; run after a new `INVENTORY`):

```sql
SELECT s.source_full_name, s.last_sync_status, s.last_sync_at,
       s.source_definition_hash AS synced_hash,
       a.source_definition_hash AS latest_hash
FROM classic_stable_target_vk.uc_sync_ops.uc_sync_state s
JOIN classic_stable_target_vk.uc_sync_ops.uc_sync_audit a
  ON a.full_name = s.source_full_name AND a.object_type = s.object_type
WHERE a.operation_mode = 'INVENTORY'
  AND a.run_id = '<latest_inventory_run_id>'
  AND (s.source_definition_hash IS NULL
       OR s.source_definition_hash != a.source_definition_hash);
```

`last_sync_status` values: `SUCCESS`, `FAILURE`, `PENDING`, `MANUAL_ACTION_REQUIRED`.

---

## 5. Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `LOCAL mode requires catalog_mapping_json or catalog_mapping_path` | mapping widget left blank | set `catalog_mapping_json` |
| `LOCATION_OVERLAP` on import | managed/external table path collides on re-run | supply/verify `location_mapping_csv_path`; UCSync also auto-skips if the object already exists at the mapped path |
| `SCHEMA_NOT_FOUND` on views | resolved against default catalog, not target | already handled — `PackageImportEngine` issues `USE CATALOG` / `USE SCHEMA` before each statement |
| Audit table shows only `NULL`/failures | old schema before the unified `status` column | `AuditService.ensure_table()` auto-adds `status` and backfills it on next run |
| `MANUAL_ACTION_REQUIRED` on storage credentials | credential DDL isn't safely re-runnable via SQL | expected — create/verify the credential via REST/API or admin SQL console, then re-run import |

See `docs/troubleshooting.md` and `docs/SOP-uc-sync-runbook.md` §8 for more.
