# Configuration

## Precedence

1. Notebook widgets / job base_parameters (highest)
2. YAML/JSON config file on UC Volume or Repo `configs/`
3. Built-in defaults (`dry_run=true`, mode=`INVENTORY`)

## Widget parameters

| Widget | Example |
|--------|---------|
| `execution_mode` | `LOCAL` or `CROSS_WORKSPACE` |
| `mode` | `INVENTORY` \| `EXPORT` \| `IMPORT` \| `SYNC` \| `COMPARE` \| `VALIDATE` |
| `catalog_mapping_json` | `{"source_catalog":"target_catalog"}` |
| `catalog_mapping_path` | `/Volumes/.../catalog-mapping.json` |
| `location_mapping_csv_path` | `/Volumes/.../location-mapping.csv` |
| `source_workspace_url` | `https://adb-....azuredatabricks.net` |
| `source_oauth_secret_scope` | `uc-migration` |
| `source_client_id_secret_key` | `source-client-id` |
| `source_client_secret_key` | `source-client-secret` |
| `target_workspace_url` | `https://adb-....azuredatabricks.net` |
| `target_oauth_secret_scope` | `uc-migration` |
| `target_client_id_secret_key` | `target-client-id` |
| `target_client_secret_key` | `target-client-secret` |
| `export_volume_path` | `/Volumes/migration/uc_exports` |
| `report_volume_path` | `/Volumes/migration/uc_exports` |
| `catalogs` | `ril_sandbox,ril_curated` |
| `schemas` | `ril_sandbox.edge` |
| `components` | `ALL` \| `tables` \| `tables_views` \| `tables+views` \| `dynamic_views` |
| `include_object_types` | `TABLE,VIEW` (optional explicit override) |
| `include_parents` | `true` (auto-include CATALOG/SCHEMA with leaf components) |
| `exclude_object_types` | `MODEL` |
| `include_regex` | `^ril_.*` |
| `exclude_regex` | `.*_TEMP$` |
| `dry_run` | `true` \| `false` |
| `config_path` | `/Volumes/.../configs/bu001.yaml` |
| `audit_table` | `classic_stable_target_vk.uc_sync_ops.uc_sync_audit` |

## Components

Use `components` to run one component or a combination:

| Value | Includes |
|-------|----------|
| `ALL` | Everything (default) |
| `tables` | managed + external + streaming tables |
| `views` | views + dynamic views |
| `dynamic_views` | dynamic views only |
| `materialized_views` / `mvs` | materialized views |
| `tables_views` / `tables+views` | tables + views + dynamic views |
| `tables_views_mvs` | tables + views + MVs |
| `volumes` / `functions` / `models` | respective types |
| `data_objects` | tables, views, MVs, volumes, functions |
| `TABLE,VIEW,FUNCTION` | explicit object types |
| `tables+dynamic_views+functions` | mixed presets |

`include_parents=true` (default) also keeps `CATALOG` and `SCHEMA` so import can create containers for the selected leaf objects.

## Mapping keys (YAML)

See `configs/example.yaml` for `storage_credentials`, `external_locations`, `managed_storage`, `principals`, `workspaces`.

## External location and table path mapping

Use `location_mapping_csv_path` for external storage migration. The CSV is
evaluated by longest matching `source_location` prefix, so a table under the
source root keeps its relative suffix under the target root.

```csv
source_external_location,source_location,target_external_location,target_location,target_credential
source_data,abfss://src@srcacct.dfs.core.windows.net/data,target_data,abfss://dst@dstacct.dfs.core.windows.net/data,target_credential
```

- `source_location`, `target_location`, `target_external_location`, and
  `target_credential` are required.
- `source_external_location` is optional but recommended when a source external
  location root is broader than the mapped table root.
- `target_external_location_url` is optional and defaults to `target_location`.
  Set it when the table-prefix mapping is narrower than the target external
  location root.
- Target credentials must already exist. UCSync never copies credential secrets.
- Import first creates the mapped target external location with the mapped
  credential, then registers external tables with rewritten `LOCATION` paths.
- Unity Catalog rejects overlapping external locations. Map to an existing
  target location name when a broader target location already covers the URL;
  UCSync verifies its URL and credential before registering tables.
- Inventory reports expose `table_type`, `data_source_format`, and
  `storage_location` as dedicated columns.
- Validation compares the actual target table/location URL and external-location
  credential against the mapping.

The existing YAML `external_locations` and `storage_credentials` mappings remain
supported for backward compatibility.
