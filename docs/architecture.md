# UC Sync Architecture

## Purpose

Databricks-native Unity Catalog **metadata** synchronization:

- cross-workspace/metastore region migration; and
- same-workspace source-catalog → target-catalog local copy.

Execution is **notebook + job only**.

Verified pair: see [feasibility.md](feasibility.md).

## Execution topology

```
Target Workspace (preferred host)
|
+-- Databricks Job
|     |
|     +-- notebooks/UC_Sync_Main   (thin orchestrator)
|           |
|           +-- src/uc_sync/*      (implementation)
|
+-- Source WorkspaceClient (OAuth/PAT from secret scope)
+-- Target WorkspaceClient (local context or secret scope)
+-- UC Volume  /Volumes/.../uc_exports/run_<id>/
+-- Delta audit table  <catalog>.<schema>.uc_sync_audit
+-- XLSX + branded HTML reports under the UC Volume run path
```

## Modes

| Mode | Behavior |
|------|----------|
| `INVENTORY` | Discover + filter + write audit (`DISCOVERED`/`INVENTORIED`) |
| `EXPORT` | Canonical manifest + DDL/metadata/grants/bindings → UC Volume |
| `IMPORT` | Dependency plan → create/skip/update on target |
| `SYNC` | Inventory → diff → export delta → import → validate |
| `COMPARE` | Source vs target definition/hash comparison |
| `VALIDATE` | Post-import existence + definition checks |

Default import posture: `DRY_RUN=true`, no destructive ops unless `allow_destructive_operations=true`.

## Package layout

```
repo/
  notebooks/UC_Sync_Main.*
  src/uc_sync/
    config.py          # widgets + YAML merge (widgets win)
    auth.py            # secret scope → OAuth/PAT clients
    workspace_client.py
    models.py          # UCObject canonical model
    inventory.py
    export.py
    import_engine.py
    dependency.py
    audit.py
    validation.py
    mapping.py
    security.py        # redaction
    reporting.py       # XLSX + Databricks-branded HTML
    adapters/          # per-object-type adapters
  configs/example.yaml
  docs/
  tests/
  resources/jobs/      # DAB/job stub
```

Notebook responsibilities only: widgets, config load, auth, preflight, mode dispatch, `display()` summaries, `dbutils.notebook.exit(json)`.

## Canonical object model

DDL is **not** canonical. Each securable becomes a `UCObject`:

- identity: `object_type`, `catalog`, `schema`, `name`, `full_name`, `object_id`
- ownership/time: `owner`, `created_at`, `last_modified_at`, `last_modified_source`
- payload: `definition` (normalized JSON), `properties`, `tags`, `grants`, `bindings`, `dependencies`
- lineage for sync: `source_definition_hash`

DDL files under `ddl/` are generated from this model for human/SQL replay.

## Export package

```
/Volumes/<...>/uc_exports/run_<run_id>/
  manifest.json
  inventory/
  ddl/
  metadata/
  grants/
  bindings/
  validation/
  checksums/
  logs/
```

Must survive cluster termination (UC Volume only — not `/tmp` or DBFS tmp).

Each run also writes inventory/export/import stage reports and one final
detailed XLSX/HTML summary beneath `run_<run_id>/reports/`.

## Local execution topology

`execution_mode=LOCAL` uses the notebook's current-workspace context for both
inventory and import. No workspace URLs or credential parameters are supplied.
`catalog_mapping_json` or `catalog_mapping_path` defines source → target
catalogs. Managed tables are metadata-only copies (`CREATE TABLE ... LIKE`);
physical rows are not copied.

## Dependency order (import)

```
Storage Credential / Service Credential
  -> External Location
    -> Catalog (with mapped managed storage_root)
      -> Schema
        -> Volume | Table | Function | Model (metadata)
          -> View | Dynamic View
            -> Materialized View (schedule preserved; no auto refresh)
      -> Grants (after object exists)
      -> Workspace Bindings (remapped workspace IDs)
```

Store `dependency_level` and `import_order` on audit rows.

## Mapping layer (mandatory for region move)

| Mapping | Why |
|---------|-----|
| `storage_credentials` | Secrets never exported; target creds pre-provisioned |
| `external_locations` | Source ADLS URLs invalid in target region |
| `managed_storage` | Target requires explicit catalog `storage_root` under Default Storage accounts |
| `principals` | Groups/SPs differ across workspaces |
| `workspaces` | Binding IDs must remap |

## Audit lifecycle

Single managed Delta table `uc_sync_audit`. Per-object independent timestamps:

`inventory_created_at` → `export_created_at` → `import_created_at` → `validation_created_at`

Never overwrite earlier stage timestamps. Append/merge by `run_id` + `operation_id`.

## Failure semantics

| Outcome | Job result |
|---------|------------|
| All success | SUCCESS + exit JSON |
| Partial object failures | `COMPLETED_WITH_WARNINGS` (job success unless configured strict) |
| Preflight / auth / volume missing | FAILED (raise) |

## Non-goals

- Physical data migration / Deep Clone / CTS file copy
- Exporting secrets, tokens, cloud keys
- Blind reuse of source storage URLs or workspace IDs
- Standalone CLI/VM/Docker runtime
