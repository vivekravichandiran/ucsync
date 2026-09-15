# 📦 Object Support Matrix

Per-object: is it **created** on the target? what **governance** is applied? and the notes that
matter. This is the quick reference behind [Architecture › Scope](ARCHITECTURE.md#-scope--what-is-and-isnt-migrated).

> **The tool migrates UC structure + governance only — never table data.** Every table is recreated
> as an empty, fully-governed shell for the data-migration utility to fill.

## ✅ Migrated

| Object | Created on target? | Governance applied | Notes |
|--------|--------------------|--------------------|-------|
| **Storage credential** | ✅ MI, from an access-connector id (`create_storage_credentials`) | grants | Secret never exported; non-MI → `MANUAL_ACTION_REQUIRED`. Same name as source. |
| **External location** | ✅ (`create_external_locations`) | grants | Same name; URL path-rewritten to target; credential from the mapping. |
| **Catalog** | ✅ same name (`create_catalogs`) | grants, tags, ABAC | `MANAGED LOCATION` path-rewritten (kept, not stripped). Rename only via `catalog_mapping_json`. |
| **Schema** | ✅ same name (`create_schemas`) | grants, tags, ABAC | `MANAGED LOCATION` from `object_locations.csv` or the catalog root. |
| **Volume (managed)** | ✅ (`create_volumes`) | grants, tags | Files not copied (unless `copy_volume_data=true`). |
| **External volume** | ✅ (`create_volumes`) | grants, tags | `CREATE EXTERNAL VOLUME` at the path-rewritten target location; its covering EL created too (create mode). Files not copied unless `copy_volume_data=true`. |
| **Function** (incl. mask/filter UDFs) | ✅ (`create_functions`) | grants | Reassembled from `information_schema.routines`+`.parameters` over the warehouse: SQL scalar UDFs, **SQL table-valued functions** (`RETURNS TABLE(...)`), and **Python UDFs** (`LANGUAGE PYTHON … AS $$…$$`). Created **before** the masks/policies that reference them. |
| **Managed table** (full definition, no data) | ✅ (`create_tables`) | tags, ABAC, classic masks/filters (**INLINE**), grants | Full fidelity via `SHOW CREATE` **on the SQL warehouse**: columns/types/nullability, comments, inline `MASK`/`WITH ROW FILTER`, `TBLPROPERTIES`, partitioning, clustering, constraints (PK/CHECK), generated & identity columns. A failed capture is a **hard `FAILURE`** (`DDL_CAPTURE_FAILED`) — no synthesized fallback. Data out of scope. |
| **External table** | ✅ (`create_tables`) | grants | Path rewritten; requires a location mapping. `SHOW CREATE` on the warehouse (hard-fail, as above). |
| **View / dynamic view** | ✅ (`create_views`) | grants, tags | Definition from `SHOW CREATE`. **Created on the SQL warehouse** (`import_warehouse_id`) — classic Spark errors on a view over a masked/row-filtered table. Fails naturally if a referenced object isn't present. |
| **Metric view** | ✅ (`create_views`) | grants | YAML definition replayed. |
| **Governed tag definitions** | ✅ `CREATE GOVERNED TAG` (Phase 0, `apply_tags`) | — | Recreated on target from the source's captured definitions, **idempotent** (already-present → `SKIP_EXISTING`), best-effort. Falls back to a prerequisite if it can't be captured/created (→ `GOVERNANCE_PREREQ_MISSING`). |
| **Governed tags** (assignments) | — | ✅ `SET TAGS` at catalog/schema/table/column/volume | Applied after the definition (Phase 0) exists. |
| **ABAC policies** | ✅ per securable (`create_abac_policies`) | ✅ | `CREATE POLICY` verbatim incl. source `EXCEPT`; column mask + row filter. Runs on `import_warehouse_id`. |
| **Classic column masks / row filters** | — (ride **INLINE** in `CREATE TABLE`) | ✅ (`apply_masks_row_filters` / inline) | Applied atomically, fail-closed. Rejected on CHECK-constraint tables (a UC limitation). |
| **Grants / ownership** | — | ✅ replayed as-is; **ownership last** | Identities assumed present on target; additive on re-run. |

## ⚠️ Report-only / conditional

| Object | Handling |
|--------|----------|
| **Materialized view** | Report-only by default (DLT/SDP pipeline would re-spin). Migrated only with `migrate_materialized_views=true`. |
| **Streaming table** | Always report-only (pipeline-managed). **No DDL captured** — never `SHOW CREATE`d (nothing to import), so no export work is wasted. |
| **Volume file contents** | Definitions migrate; **bytes** only when `copy_volume_data=true` (recorded in `uc_sync_volume_files`). |
| **Registered models, vector-search indexes, online tables, monitors, UC secrets** (Tier-A) | **Inventoried & reported** (`in_scope_for_migration=false`), not migrated. No DDL captured. |
| **Monitor metric tables** (a monitor's `*_profile_metrics` / `*_drift_metrics`) | **Report-only** — plain Delta tables a Lakehouse monitor owns; recreating the monitor regenerates them, so they are **not** migrated as empty copies. Detected from each monitor's declared metric-table names (its own **Monitor Metric Tables** report tab). *If an older tool version had migrated one, the next run self-heals its state row; the orphan copy on target must be dropped by hand — see [Runbook › manual actions](RUNBOOK.md#-handling-failures--manual-actions).* |
| **Connections / shares / recipients / providers** | Inventory-only, flagged `MANUAL` — carry remote secrets/endpoints; recreate by hand. |

## ❌ Out of scope entirely

- **Table data** — the data-migration utility (Delta Share + Deep Clone).
- **Identities/principals** — the workspace-migration utility (must pre-exist on target). *(Governed-tag
  **definitions** are recreated by this tool in Phase 0 — see the Migrated table above.)*
- **Tier-B/C metastore-scoped objects** — Lakehouse Federation connections & foreign catalogs, service
  credentials, workspace bindings, storage credentials not referenced by a migrated external location,
  Delta Sharing shares/recipients/providers, clean rooms. Out of a catalog-scoped principal's reach —
  their report sheets are intentionally absent.
- **Non-UC / workspace-level assets** (workspace secret scopes, vector-search *endpoints*,
  model-serving endpoints) — the workspace-migration utility.
- **Removals** — additive-only: reported (`deleted_in_source`), never applied.

---

## 📚 Related

- 🏗️ Scope & the fail-closed model → **[Architecture](ARCHITECTURE.md#-scope--what-is-and-isnt-migrated)**
- ⚙️ The `create_*` / `apply_*` toggles → **[Configuration Guide](CONFIGURATION_GUIDE.md#create--apply-toggles)**
- 📋 Run it → **[Runbook](RUNBOOK.md)**
