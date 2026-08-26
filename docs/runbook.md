# UC Governance Migration — Runbook

The operator guide. Migrates **Unity Catalog structure + governance** (metadata +
ACLs) from a source metastore (region 1) to a target metastore (region 2). It
**never moves table data** — it creates empty, fully-governed table shells; a
separate data-migration utility clones data via Delta Share + Deep Clone.

What it reproduces on the target, **under the same names as source**:
structure (storage credentials, external locations, catalogs, schemas, volumes,
functions, views), **full table definitions** (columns, types, nullability,
comments, `TBLPROPERTIES`, partitioning, clustering, constraints, generated &
identity columns — everything except data), **governed tags**, **ABAC policies**
(verbatim, incl. each policy's `EXCEPT` list), **classic column masks / row
filters**, and **grants/ownership**.

---

## 1. Decision tree — which scenario are you in?

```
Do you have a target metastore with working storage?
├─ No  → do the manual prerequisites first (§ docs/manual-actions.md)
└─ Yes → Has the target catalog already been created?
         ├─ No, starting from scratch ................... Scenario 1
         ├─ Catalog exists, want to (re)apply governance .. Scenario 2
         └─ Creds/external locations pre-created by hand .. Scenario 3
Scoping to one catalog/schema? ......................... Scenario 4 (combine with 1–3)
Source & target can't reach each other over network? .. Scenario 5 (airgap)
Job runs on the target, reads source remotely? ......... Scenario 6 (direct)
Want the catalog under a different name on target? ..... Scenario 7 (catalog rename)
Re-running to pick up new objects/grants/tags? ......... Scenario 8 (additive re-run)
```

Every run is three stages: **01 Inventory → 02 Export → 03 Import**. Inventory +
Export run where the **source** is readable; Import runs on the **target**.
Compute must be **Standard (USER_ISOLATION) or serverless** (masks/row filters are
not supported on single-user clusters).

---

## 0. Install the jobs (one time)

The utility is exactly four notebooks: **`00_Install_Jobs`** (the wrapper) plus the
three stages **`01_Inventory` / `02_Export` / `03_Import`**. You don't wire tasks by
hand — open `00_Install_Jobs`, fill the widgets once, pick the jobs to create in
`jobs_to_create`, and run. It stamps your values into the declarative specs under
`jobs/` and creates (or updates in place, by name) the selected Databricks Jobs:

| Job | Tasks | Runs on |
|---|---|---|
| **Airgap Inventory+Export (source)** | 01 → 02 | source workspace |
| **Airgap Import (target)** | 03 | target workspace |
| **End-to-end Dry Run** | 01 → 02 → 03 (`dry_run=true`) | one workspace |
| **End-to-end Live** | 01 → 02 → 03 (`dry_run=false`) | one workspace |

`run_id` chains automatically inside a job run (`{{job.run_id}}`). For the standalone
**Airgap Import** job it's a **job parameter** — set it at run time to the bundle-folder
id printed by the source Inventory+Export run. New jobs get a USER_ISOLATION job
cluster; set `existing_cluster_id` to reuse a cluster instead. The scenarios below
give the widget values; you can also run the three stage notebooks directly.

---

## 2. Scenarios (exact widget values)

Common widgets: `output_volume_path` (bundle + reports), `ops_catalog` +
`ops_schema` (audit/state tables), `run_id` (from Inventory; reuse for Export +
Import), `mapping_file_path` (storage-cred + location CSV, § configuration.md), and
`source_warehouse_id` (SQL warehouse — **required for export in every mode, including
airgap**; § configuration.md).

**`source_warehouse_id` (stages 01 + 02)** — a SQL warehouse on the **source**
workspace. **Required for export** in both modes: all full-fidelity DDL capture runs
on it (`SHOW CREATE` for the table/view family; functions from `information_schema`).
A classic Spark cluster is flaky on masked/row-filtered tables, and a failed capture
is a **hard failure** (`DDL_CAPTURE_FAILED`) with no synthesized fallback — re-run
once the warehouse is warm. It is also what stage 01 uses to read tags + ABAC.

**`import_warehouse_id` (stage 03)** — a SQL warehouse on the **target** workspace.
`CREATE POLICY` is rejected on a classic Spark cluster, so the ABAC phase runs on
this warehouse; the **view-creation phase** runs on it too (classic Spark errors on a
`CREATE VIEW` over a masked/row-filtered table). **Required whenever the bundle has
ABAC policies** — if unset, those fail closed and the tables they would have protected
are **dropped** (the fail-closed guarantee, not a bug) — and strongly recommended
whenever the bundle has views over masked tables. Not needed if the bundle has no
ABAC and no masked-table views.

**Fail-closed:** the import applies protection as early as possible and never lets a
partially-protected table survive. Classic masks / row filters ride **inline** in
the `CREATE TABLE` (a missing mask function fails the CREATE atomically); a
governed-tag or ABAC failure **drops** the freshly-created table and marks it
`FAILURE` (`PROTECTION_FAILED`) — visible in the Tables + Issues sheets, `uc_sync_audit`
and `uc_sync_state`. Pre-existing tables (`SKIP_EXISTING`) are never dropped. Views
are created last, so a view on a dropped table simply fails to create.

Import-only extras (all optional): `filter_tables` (import a subset of tables),
`catalog_mapping_json` (recreate a source catalog under a different target name — or
replicate into an existing one, Scenario 7b), `object_locations_path` (per-schema /
external-object target locations, § configuration.md), `run_as_spn` (run the import as a
target service principal so it owns everything).

### Scenario 1 — From scratch (new target metastore)
| Stage | Widget | Value |
|---|---|---|
| 01 Inventory | `connectivity_mode` | `airgap` (run on source) |
| | `catalogs` | `sales_prod` (or blank = whole metastore) |
| | `source_warehouse_id` | a source SQL warehouse (**required for ABAC**) |
| | `output_volume_path` | `/Volumes/ops/mig/out` |
| 02 Export | `mapping_file_path` | `/Volumes/ops/mig/mapping.csv` |
| | `source_warehouse_id` | same warehouse |
| 03 Import | all `create_*` | `true` |
| | all `apply_*` | `true` |
| | `import_warehouse_id` | a target SQL warehouse (**required if the bundle has ABAC**) |

**Success:** Import exit JSON `by_status` is all `SUCCESS`/`SKIP_EXISTING`; read
`import.xlsx`. Storage credential + external location are created from the mapping.
Any table dropped fail-closed shows as `FAILURE`/`PROTECTION_FAILED` in the Issues
sheet — check it if a count looks low.

### Scenario 2 — Catalog already exists on target
Same as Scenario 1 but Import: `create_catalogs=false` (± `create_schemas=false`).
Governance (tags/ABAC/masks/grants) is still applied to the existing catalog, and
tables the run creates inside it are still fail-closed protected (dropped if their
tag/ABAC fails). Still set `import_warehouse_id` if the bundle has ABAC policies —
otherwise those policies fail closed and their tables are dropped.

### Scenario 3 — Creds / external locations pre-created manually
Import: `create_storage_credentials=false`, `create_external_locations=false`.
The mapping file can be omitted if the catalog's managed path is already covered
by an existing target external location. Everything else creates as normal.

### Scenario 4 — Single-catalog (or single-schema) scope
Inventory: `catalogs=<one>` (± `schemas=<one>`). If the catalog is absent on
target it is created with the same name + mapped location (and its external
location + credential auto-created if `create_*` are on and the mapping provides
them). Only that subtree is migrated.

### Scenario 5 — Airgap (no network between regions)
1. Run **01 + 02** on the **source** workspace (`connectivity_mode=airgap`).
   **`source_warehouse_id` is required** — a SQL warehouse on the source. Export
   captures all DDL over it (`SHOW CREATE` + functions from `information_schema`); a
   classic job cluster is unreliable on masked tables and can't read
   `abac_policy_definitions`. Without it, export fails fast.
2. Move the whole `run_<id>/` directory to the target (download/upload the volume
   folder). Verify `manifest.json` + `checksums/` are present.
3. Run **03** on the **target** workspace pointing at the moved `run_<id>/`.

### Scenario 6 — Direct (job runs on target, reads source over a warehouse)
`connectivity_mode=direct`; set `source_workspace_url` + source SP creds +
`source_warehouse_id` so 01/02 read the source. For a same-workspace **local test**,
leave the source widgets blank (reads the current workspace) but still set
`source_warehouse_id` to a local warehouse for ABAC. Note: real region moves use two
metastores.

### Scenario 7 — Rename the catalog on the target
To land a source catalog under a different name, set (03 Import)
`catalog_mapping_json = {"<source_catalog>":"<target_catalog>"}`. Inventory/Export
still use the source name; the rename happens on import (every DDL/grant/tag/ABAC
statement is rewritten; hyphenated names and prefix look-alikes are safe). Storage
paths still come from the mapping CSV — the rename does not affect them. Combine with
`filter_tables` to replicate only some tables.

### Scenario 7b — Replicate into an **existing** catalog (existing-catalog mode)
When the mapped target catalog **already exists** on the target metastore, the import
auto-detects it and treats the catalog + its storage credential + external location as
**prerequisites** — it skips creating them and only replicates the schemas + objects
inside (existing schemas/objects are skipped). No mapping CSV is needed. Setup:
1. Pre-create the target catalog (any name) + its storage credential + external
   location; `catalog_mapping_json = {"<source>":"<existing_target>"}` (identity name is
   fine).
2. Grant the run principal `USE CATALOG` + `CREATE SCHEMA` on that catalog (or run as its
   owner) — otherwise schema creation fails `PERMISSION_DENIED`.
3. Schemas land under the **catalog root** by default. To place a schema (or an external
   table/volume) at a specific location, list it in `object_locations_path`
   (`schema,volume,table,location`; § configuration.md). External tables/volumes **must**
   be listed here in this mode, and every location must be covered by an existing EL, or
   they report `EXTERNAL_LOCATION_MISSING`.

### Scenario 8 — Re-run (additive, idempotent)
Re-run the same stages with the same widgets. New objects/grants/tags/policies at
source are **created/applied**; existing objects are **skipped** (`SKIP_EXISTING`);
**removals at source are never applied** (additive-only). Note this is a full
idempotent re-apply, **not** a computed delta: a *changed* existing table is not
re-altered, and source deletions are not propagated. True incremental (diff-driven)
sync is not yet implemented — the `uc_sync_state` table is the groundwork for it.

---

## 3. What success looks like
- **Inventory**: `by_type` counts match what you expect; `bundle/inventory.json` written.
- **Export**: `exported` == inventory count; `ddl/`, `grants/`, `tags/`, `abac/`,
  `policies/` populated under `run_<id>/export/…`; `migrated/` written.
- **Import**: `by_status` = `SUCCESS`/`SKIP_EXISTING` (plus `MANUAL_ACTION_REQUIRED`
  only for storage-credential secrets — expected). Any `FAILURE` prints its object
  + reason; `GOVERNANCE_PREREQ_MISSING` means a governed-tag definition or a
  referenced function is missing on the target (see § manual-actions.md).

## 4. Toggles (Import)
`create_*` gate **creation** per object family; when off, the object is assumed to
pre-exist and creation is skipped (grants still apply). `apply_tags`,
`create_abac_policies`, `apply_masks_row_filters`, `apply_grants` gate the
governance phases. All default **true**.

## 5. Ordering (automatic)
storage credentials → external locations → catalogs → schemas → volumes →
functions → tables → views → **governed tags → ABAC policies → classic masks/row
filters** → grants (last). You never sequence by hand.
