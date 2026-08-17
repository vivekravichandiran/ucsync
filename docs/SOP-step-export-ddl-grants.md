# Step — LOCAL export (DDL + grants)

Run after inventory when you need SHOW CREATE table DDLs and grant DDLs written to both the UC Volume and Workspace.

## Widgets

| Widget | Value |
|--------|--------|
| `execution_mode` | `LOCAL` |
| `mode` | `EXPORT` |
| `dry_run` | `false` |
| `catalog_mapping_json` | `{"ril_sandbox":"ril_sandbox_ucsync_local"}` |
| `catalogs` | `ril_sandbox` |
| `schemas` | _(scope as needed)_ |
| `components` | `ALL` |
| `include_parents` | `true` |
| `exclude_object_types` | `MODEL` |
| `exclude_regex` | `.*_TEMP$` |
| `export_volume_path` | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports` |
| `report_volume_path` | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports` |
| `audit_table` | `classic_stable_target_vk.uc_sync_ops.uc_sync_audit` |

`dry_run` must be `false` — dry-run inventorizes only and does **not** write DDL/grant files.

## Outputs

Volume package:

```text
/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/run_<id>/
  ddl/<OBJECT_TYPE>_<full__name>.sql
  ddl/all_tables.sql
  grants/<OBJECT_TYPE>_<full__name>.sql
  grants/all_grants.sql
  metadata/...
  inventory/objects.json
  manifest.json
```

Workspace mirror:

```text
/Workspace/Users/vivek.ravichandiran@databricks.com/UCSync/export_staging/<id>/
  ddl/...
  grants/...
```

## What is captured

- **Tables / standard views / MVs / streaming tables / functions:** `SHOW CREATE` via Spark SQL (falls back to inventory synthesis)
- **Metric views:** YAML definition exported as `CREATE OR REPLACE VIEW … WITH METRICS LANGUAGE YAML AS $$…$$` (`SHOW CREATE TABLE` is not supported for metric views)
- **Catalogs / schemas / volumes / external volumes / external locations / storage credentials:** synthesized `CREATE` DDL from inventory metadata
- **Grants:** `GRANT … ON … TO …` (and `ALTER … OWNER TO …`) from inventory privilege assignments

Aggregates:

```text
ddl/all_objects.sql   # every CREATE DDL
ddl/all_tables.sql    # table/view/function subset
grants/all_grants.sql
```

Exit JSON includes `export_path`, `export_workspace_path`, `ddl_files`, `grant_files`, and `ddl_by_source`.
