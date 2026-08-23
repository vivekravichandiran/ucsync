# Configuration

## Widgets (canonical contract)

| Widget | Values | Stage(s) | Purpose |
|---|---|---|---|
| `stage` | `INVENTORY` \| `EXPORT` \| `IMPORT` | all | Which stage runs (set by each notebook). |
| `connectivity_mode` | `direct` (default) \| `airgap` | 01 | `direct` = read source over REST/current workspace; `airgap` = run 01+02 on source, move bundle, run 03 on target. |
| `catalogs` | csv or blank | 01 | Scope. Blank = whole metastore. |
| `schemas` | csv `catalog.schema` or blank | 01 | Scope within the catalog(s). |
| `output_volume_path` | `/Volumes/<c>/<s>/<vol>` | all | Bundle + reports root. |
| `ops_catalog`, `ops_schema` | names | all | Audit/state tables (`uc_sync_audit`, `uc_sync_state`). |
| `run_id` | string | 02, 03 | Ties Export/Import to the Inventory run. |
| `mapping_file_path` | `/Volumes/…csv` | 02 | Storage-credential + location mapping (below). |
| `source_workspace_url` + source SP credentials | — | 01 | Direct-mode remote source (blank = current workspace). Two credential routes below. Redacted from artifacts. |
| `create_storage_credentials`, `create_external_locations`, `create_catalogs`, `create_schemas`, `create_volumes`, `create_functions`, `create_tables`, `create_views`, `create_abac_policies` | bool (default true) | 03 | Gate **creation** per object family. Off = assume pre-existing, skip create, still govern. |
| `apply_grants`, `apply_tags`, `apply_masks_row_filters` | bool (default true) | 03 | Gate governance application. |
| `dry_run` | bool | 03 | Plan only, no mutations. |

**Catalog names are never mapped** — every securable is recreated under its source
name. There is no catalog-mapping widget.

### Source credentials (remote/direct only; blank = current workspace)

Two routes — pick one:

- **Secret scope (recommended):** `source_oauth_secret_scope` + `source_client_id_secret_key`
  + `source_client_secret_key` name the scope and the *keys* holding the SP client id /
  secret; values are read via `dbutils.secrets.get` and never leave the workspace.
- **Direct values:** paste `source_client_id` + `source_client_secret` (or a PAT in
  `source_token`) straight into the widgets. Convenient for one-off runs, but these are
  **plaintext** in widget values and job `base_parameters` (not redacted like a
  `{{secrets/…}}` reference) — avoid for shared/scheduled jobs.

Direct values win when both are supplied.

## Mapping file (single CSV)

Header (columns; `source_external_location` optional):

```csv
source_location,target_location,target_external_location,target_credential
abfss://uc@src.dfs.core.windows.net,abfss://uc@tgt.dfs.core.windows.net,el-target,cred-target
```

- Longest-prefix match on `source_location` rewrites every derived ADLS path
  (catalog/schema managed roots, external-location URLs, external-table paths).
- `target_credential` / `target_external_location` are used when the utility
  creates the target storage credential + external location. If those already
  exist on the target and cover the mapped path, set
  `create_storage_credentials=false` + `create_external_locations=false` and the
  mapping file is optional.

## Compute
Masks and row filters require **Standard (USER_ISOLATION)** or **serverless**
compute — never single-user/assigned clusters (they reject
`ROW_COLUMN_ACCESS_POLICIES_NOT_SUPPORTED_ON_ASSIGNED_CLUSTERS`).
