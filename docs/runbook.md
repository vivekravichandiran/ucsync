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
Same workspace, quick local test? ...................... Scenario 6 (direct/local)
Re-running to pick up new grants/tags/policies? ........ Scenario 7 (incremental)
```

Every run is three stages: **01 Inventory → 02 Export → 03 Import**. Inventory +
Export run where the **source** is readable; Import runs on the **target**.
Compute must be **Standard (USER_ISOLATION) or serverless** (masks/row filters are
not supported on single-user clusters).

---

## 2. Scenarios (exact widget values)

Common widgets: `output_volume_path` (bundle + reports), `ops_catalog` +
`ops_schema` (audit/state tables), `run_id` (from Inventory; reuse for Export +
Import), `mapping_file_path` (storage-cred + location CSV, § configuration.md).

### Scenario 1 — From scratch (new target metastore)
| Stage | Widget | Value |
|---|---|---|
| 01 Inventory | `connectivity_mode` | `airgap` (run on source) |
| | `catalogs` | `sales_prod` (or blank = whole metastore) |
| | `output_volume_path` | `/Volumes/ops/mig/out` |
| 02 Export | `mapping_file_path` | `/Volumes/ops/mig/mapping.csv` |
| 03 Import | all `create_*` | `true` |
| | all `apply_*` | `true` |

**Success:** Import exit JSON `by_status` is all `SUCCESS`/`SKIP_EXISTING`; read
`import.xlsx`. Storage credential + external location are created from the mapping.

### Scenario 2 — Catalog already exists on target
Same as Scenario 1 but Import: `create_catalogs=false` (± `create_schemas=false`).
Governance (tags/ABAC/masks/grants) is still applied to the existing catalog.

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
2. Move the whole `run_<id>/` directory to the target (download/upload the volume
   folder). Verify `manifest.json` + `checksums/` are present.
3. Run **03** on the **target** workspace pointing at the moved `run_<id>/`.

### Scenario 6 — Direct / local (same workspace)
`connectivity_mode=direct`, leave the source SP widgets blank (reads the current
workspace). Note: real region moves use two metastores; same-metastore is a test
convenience (source and target names are identical, so use two workspaces/metastores
for an actual migration).

### Scenario 7 — Incremental re-run
Re-run the same three stages with the same widgets. New objects/grants/tags/
policies at source are **applied**; unchanged objects are **skipped**
(`SKIP_EXISTING`); **removals at source are reported, never applied** (additive-only).

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
