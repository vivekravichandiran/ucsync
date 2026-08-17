# Local Same-Workspace Mode

`execution_mode=LOCAL` copies Unity Catalog metadata from source catalogs to
mapped target catalogs in the workspace running the notebook. No workspace URL,
OAuth scope, client ID, or client secret parameters are required.

The Job/notebook identity still needs Unity Catalog read/create permissions.

## Catalog mapping

Pass either:

### Inline Job parameter

```json
{"ril_sandbox":"ril_sandbox_copy","ril_curated":"ril_curated_copy"}
```

Use widget/job parameter `catalog_mapping_json`.

### JSON in a Unity Catalog Volume

```json
{
  "catalogs": {
    "ril_sandbox": "ril_sandbox_copy",
    "ril_curated": "ril_curated_copy"
  }
}
```

Store it at a persistent path such as:

```text
/Volumes/classic_stable_target_vk/uc_sync_ops/config/local-catalog-mapping.json
```

Set `catalog_mapping_path` to that path. Inline JSON takes precedence.

If `catalogs` is blank, mapping keys become the source catalog selection.

## Metadata-copy behavior

| Object | Local behavior |
|--------|----------------|
| Catalog | `CREATE CATALOG IF NOT EXISTS` |
| Schema | `CREATE SCHEMA IF NOT EXISTS` |
| Managed table | `CREATE TABLE target LIKE source` (empty target; no data copy) |
| View / function | `SHOW CREATE`, rewrite catalog references, create target |
| Managed volume | `CREATE VOLUME IF NOT EXISTS` |
| External table / volume | `MANUAL_ACTION_REQUIRED` to avoid unsafe duplicate storage registration |

`dry_run=true` remains the default. Physical table data is not copied.

## Component selection

Pass `components` (or `include_object_types`) to scope the run:

```text
components = tables
components = tables_views
components = tables+views
components = dynamic_views
components = tables+functions+volumes
components = ALL
```

Catalog/schema parents are included automatically unless `include_parents=false`.

# Reports

Reports are written below:

```text
<report_volume_path>/run_<run_id>/reports/
```

Files:

- `inventory_report.xlsx` and `inventory_report.html`
- `export_report.xlsx` and `export_report.html`
- `import_report.xlsx` and `import_report.html`
- `uc_sync_detailed_report.xlsx`
- `uc_sync_summary.html`

Each XLSX has Summary, Details, and Errors sheets. The final workbook also has
one sheet per executed stage. Error rows are highlighted.

HTML uses the supplied Databricks inventory report's logo, sidebar layout,
colors, summary cards, searchable details, status badges, and pagination.

Every export/import error is also appended to the configured managed Delta
audit table.
