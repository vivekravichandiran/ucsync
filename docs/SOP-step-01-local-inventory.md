# Step 1 — LOCAL inventory (UC target workspace)

First action when starting a LOCAL-mode UC Sync run.

## Notebook

Open and run:

```text
/Workspace/Users/vivek.ravichandiran@databricks.com/UCSync/notebooks/UC_Sync_Main
```

(Or the equivalent Repos path: `/Repos/UCSync/notebooks/UC_Sync_Main`.)

## Widgets

| Widget | Value |
|--------|--------|
| `execution_mode` | `LOCAL` |
| `mode` | `INVENTORY` |
| `dry_run` | `true` |
| `catalog_mapping_json` | `{"ril_sandbox":"ril_sandbox_ucsync_local"}` |
| `catalogs` | `ril_sandbox` |
| `schemas` | _(blank = all schemas, or e.g. `ril_sandbox.ucsync_local_01`)_ |
| `components` | `ALL` |
| `include_parents` | `true` |
| `exclude_object_types` | `MODEL` |
| `exclude_regex` | `.*_TEMP$` |
| `export_volume_path` | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports` |
| `report_volume_path` | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports` |
| `audit_table` | `classic_stable_target_vk.uc_sync_ops.uc_sync_audit` |

Leave all `source_*` / `target_*` workspace URL and OAuth widgets **blank**. LOCAL mode uses the notebook’s current-workspace context.

## Run

1. Attach a running cluster in the UC target workspace.
2. Set the widgets above.
3. **Run All**.

## Where to find the inventory report

From the notebook exit JSON:

```text
reports.inventory.xlsx
reports.inventory.html
reports.inventory.xlsx_no_source_metadata
reports.inventory.html_no_source_metadata
```

Typical Volume path:

```text
/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/run_<YYYYMMDD_HHMMSS>/reports/inventory_report.xlsx
/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/run_<YYYYMMDD_HHMMSS>/reports/inventory_report.html
/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/run_<YYYYMMDD_HHMMSS>/reports/inventory_report_no_source_metadata.xlsx
/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/run_<YYYYMMDD_HHMMSS>/reports/inventory_report_no_source_metadata.html
```

## What to check

- Object counts by type in the run log (`CATALOG`, `SCHEMA`, `TABLE`, `EXTERNAL_TABLE`, …).
- External tables include `table_type`, `data_source_format`, and `storage_location`.
- Inventory XLSX includes storage sheets plus:
  - **Principals** — unique users / groups / service principals with grants on inventoried objects
  - **Object Permissions** — object type, object name, principal, privileges
- Companion `*_no_source_metadata.*` reports omit the bulky `source_metadata` column.
- Use those `storage_location` values next to build the location-mapping CSV.

## Next step

See `docs/SOP-uc-sync-runbook.md` §2.2 — extract unique table paths and external locations.
