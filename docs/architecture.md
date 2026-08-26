# Architecture

## Stages
```
01 Inventory  →  02 Export  →  03 Import
```
- **Inventory** (read-only, source): enumerate securables in scope over REST; attach
  grants (permissions API), governed tags + ABAC policies (via SQL:
  `information_schema.*_tags`, `abac_policy_definitions` + `DESCRIBE POLICY`). Writes
  `bundle/inventory.json`.
- **Export** (source): capture full-fidelity DDL (prefer `SHOW CREATE`, else
  synthesize) + grants + tags + ABAC + classic-policy artifacts; then path-rewrite to
  the target ADLS roots (`migrated/`). Writes `manifest.json` + `checksums/`.
- **Import** (target): replay `migrated/` in dependency order, honoring
  `create_*`/`apply_*`; idempotent and additive.

## Connectivity modes
- **direct** — read the source over REST from the current/target workspace. No bundle
  hand-off. (High-fidelity `SHOW CREATE` requires SQL access to the source; use airgap
  when the source is remote.)
- **airgap** — run 01+02 on the source, move `run_<id>/`, run 03 on the target.

## Run directory
```
run_<id>/
  bundle/inventory.json          # self-describing inventory
  export/run_<id>/               # ddl/ grants/ tags/ abac/ policies/ metadata/ checksums/
  migrated/                      # path-rewritten copy replayed by Import
  reports/  inventory.xlsx export.xlsx import.xlsx
  manifest.json  checksums/
```

## Dependency order (governance-aware, fail-closed)
storage credentials → external locations → catalogs → schemas → volumes →
**functions (before tables)** → **tables (classic column masks / row filters kept
INLINE in the CREATE — atomic)** → **governed tags** (fail → drop the table) →
**ABAC policies** (run on the SQL warehouse `import_warehouse_id`; fail → drop the
matched table[s]) → **drop sweep** (remove every table a governance step failed on)
→ **views / matviews** (a view on a dropped table simply fails to create) →
grants → ownership (last).

Two moves make protection early and atomic: functions are created *before* tables
so a table's inline `MASK` / `WITH ROW FILTER` clause resolves at `CREATE TABLE`
time (a missing function fails the CREATE — no unprotected table survives); and
views are created *after* governance + the drop sweep, so a view built on a
governed table that was dropped fails naturally, with no cascade code.

**Fail-closed (always on):** a *governed* table (≥1 mask, row filter, ABAC policy,
or governed tag) that cannot be fully protected never survives. A classic
mask/filter failure fails `CREATE TABLE` atomically; a governed-tag or ABAC failure
drops the freshly-created table (`DROP TABLE IF EXISTS`) and marks it `FAILURE`
(`error_code=PROTECTION_FAILED`) — the failure is written onto the object's result
in place, so it shows in the Tables sheet, the Issues sheet, `uc_sync_audit` and
`uc_sync_state`. Pre-existing tables are `SKIP_EXISTING` and never dropped.

**ABAC needs a SQL warehouse:** `CREATE POLICY` is rejected at parse on a classic
Spark cluster, so the ABAC phase runs on `import_warehouse_id`. If a bundle carries
any ABAC policy and `import_warehouse_id` is unset, those policies fail closed
(`error_code=ABAC_WAREHOUSE_REQUIRED`) and the tables they would have protected are
dropped. A bundle with no ABAC needs no warehouse.

## Modules (`src/uc_sync/`)
`config` (widget contract), `inventory`, `governance` (tags + ABAC reads/DDL),
`sql_ddl` + `rewrite` (DDL synthesis + replay sanitizers, path-only rewrite),
`export`, `migrate_export`, `package_import` (+ `import_engine`), `dependency`,
`mapping`, `location_mapping`, `audit` + `sync_state`, `reporting`, `auth` +
`workspace_client` + `security`.
