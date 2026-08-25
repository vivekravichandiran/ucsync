# Configuration

## Widgets (canonical contract)

| Widget | Values | Stage(s) | Purpose |
|---|---|---|---|
| `stage` | `INVENTORY` \| `EXPORT` \| `IMPORT` | all | Which stage runs (set by each notebook). |
| `connectivity_mode` | `direct` (default) \| `airgap` | 01, 02 | `direct` = job runs on the target and reads the source over a SQL warehouse; `airgap` = run 01+02 on the source, move the bundle, run 03 on the target. |
| `catalogs` | csv or blank | 01 | Scope. Blank = whole metastore. This is the **source** scope — there is no separate "target catalog" input. |
| `schemas` | csv `catalog.schema` or blank | 01 | Scope within the catalog(s). |
| `output_volume_path` | `/Volumes/<c>/<s>/<vol>` | all | Bundle + reports root. |
| `ops_catalog`, `ops_schema` | names | all | Where the audit/state tables (`uc_sync_audit`, `uc_sync_state`) are created + written, on the workspace each stage runs on. |
| `run_id` | string | 02, 03 | Ties Export/Import to the Inventory run. |
| `mapping_file_path` | `/Volumes/…csv` | 02 | Storage-credential + location mapping (below). |
| `source_workspace_url` + source SP credentials | — | 01, 02 | Remote source (blank = current workspace). Credential widgets below. Redacted from artifacts. |
| `source_warehouse_id` | warehouse id | 01, 02 | **SQL warehouse for governance reads (tags + ABAC).** Required for a remote source. **Also required in airgap-on-source to capture ABAC** — classic job-cluster Spark cannot read `information_schema.abac_policy_definitions`, so without a warehouse ABAC policies (and Policy-Matched-Columns) come back **empty** (tags still work). Point it at any warehouse on the workspace that owns the objects. |
| `filter_tables` | csv or blank | 03 | Import only a subset of **tables** (`catalog.schema.table` or bare table). Blank = all. Catalog/schema scoping is done upstream via `catalogs`/`schemas`; the catalogs/schemas/functions/volumes a selected table needs still come along. |
| `catalog_mapping_json` | JSON or blank | 03 | Replicate a source catalog under a **different target name**, e.g. `{"src_cat":"tgt_cat"}`. Blank = keep source names. Rewrites the catalog name in every replayed statement (DDL, grants, tags, ABAC, masks). Does **not** affect storage paths — those always come from the mapping CSV. |
| `run_as_spn` | SP application id or blank | 03 | Run the import as a target **service principal** (so migrated securables are owned by it). Blank = run as the installing user. Applies only to the import/e2e jobs. |
| `create_storage_credentials`, `create_external_locations`, `create_catalogs`, `create_schemas`, `create_volumes`, `create_functions`, `create_tables`, `create_views`, `create_abac_policies` | bool (default true) | 03 | Gate **creation** per object family. Off = assume pre-existing, skip create, still govern. |
| `apply_grants`, `apply_tags`, `apply_masks_row_filters` | bool (default true) | 03 | Gate governance application. |
| `dry_run` | bool | 03 | Plan only, no mutations. |

**Catalog names default to the source name.** Every securable is recreated under
its source name unless you set `catalog_mapping_json` to rename the *catalog* on the
target. Schema/table/etc. names are always preserved.

### Source credentials (remote source only; blank = current workspace)

The service-principal **client id** is always plaintext (`source_client_id`) — it is
not a secret. For the **secret**, pick one route:

- **Secret scope (recommended):** name `source_secret_scope` + `source_secret_key`;
  the value is read via `dbutils.secrets.get` and never leaves the workspace.
- **Direct value:** paste `source_client_secret` straight into the widget. Convenient
  for one-off runs, but it is **plaintext** in widget values and job `base_parameters`
  (not redacted like a `{{secrets/…}}` reference) — avoid for shared/scheduled jobs.

The plaintext `source_client_secret` wins when both are supplied.

## Mapping file (single CSV)

Because securable **names are never mapped**, you supply only the *path* rewrite and
the *target access-connector id*. The storage credential and external location are
recreated under their **source names** — you do not name them here.

```csv
source_location,target_location,target_access_connector_id
abfss://uc@src.dfs.core.windows.net,abfss://uc@tgt.dfs.core.windows.net,/subscriptions/…/accessConnectors/tgt-connector
```

- **`source_location` → `target_location`** — longest-prefix match rewrites every
  derived ADLS path (catalog/schema managed roots, external-location URLs,
  external-table + external-volume paths).
- **`target_access_connector_id`** — the target-region Databricks access connector the
  recreated storage credential is bound to (needed only when
  `create_storage_credentials=true`). This is resolved **per credential** from the row
  matching that credential's source storage location, so an enterprise layout with one
  connector **per catalog** works: give each source-account row its own connector. (A
  single shared connector across all rows also works — just repeat the same id.)
- Optional legacy columns `target_external_location` / `target_credential` are still
  read for the direct-import path and validation, but are **not required** — leave them
  out. If the target creds/ELs already exist and cover the path, set
  `create_storage_credentials=false` + `create_external_locations=false`; the mapping
  file is then only needed for the path rewrite.

### What you create on the target (and what the utility creates)

| You create by hand | The utility creates |
|---|---|
| ADLS storage account + container(s) at `target_location` | Storage credential (**source name**) → bound to your `target_access_connector_id` |
| Databricks **access connector** (managed identity) + `Storage Blob Data Contributor` on that storage; give its id | External location (**source name**) → `target_location`, using that credential |
| — | Catalogs/schemas/volumes/tables/… at the rewritten paths |

So: **create target ADLS + access connector, hand over the connector id and the
path mapping — that's it.** Names, credential, and EL are carried from source.

## Compute
Masks and row filters require **Standard (USER_ISOLATION)** or **serverless**
compute — never single-user/assigned clusters (they reject
`ROW_COLUMN_ACCESS_POLICIES_NOT_SUPPORTED_ON_ASSIGNED_CLUSTERS`).
