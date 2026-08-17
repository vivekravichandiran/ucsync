# UC Sync — Standard Operating Procedure (SOP)

Step-by-step runbook for Unity Catalog metadata synchronization with UCSync.
The procedure is split into **LOCAL mode** and **CROSS_WORKSPACE mode**.

> **Scope of this utility**
>
> - Syncs **metadata** (catalogs, schemas, tables, views, volumes, functions, external locations).
> - Does **not** copy physical table data / Parquet files.
> - Never copies storage-credential secrets. Target credentials must already exist.
> - External tables and external locations require a CSV location mapping.

---

## Table of contents

1. [Prerequisites (both modes)](#1-prerequisites-both-modes)
2. [Mode A — LOCAL (same workspace / same metastore)](#2-mode-a--local-same-workspace--same-metastore)
3. [Mode B — CROSS_WORKSPACE (source → target metastore)](#3-mode-b--cross_workspace-source--target-metastore)
4. [Shared appendix — credentials & Azure access connector](#4-shared-appendix--credentials--azure-access-connector)
5. [Shared appendix — location mapping CSV](#5-shared-appendix--location-mapping-csv)
6. [Shared appendix — component presets](#6-shared-appendix--component-presets)
7. [Shared appendix — report locations & exit JSON](#7-shared-appendix--report-locations--exit-json)
8. [Troubleshooting quick reference](#8-troubleshooting-quick-reference)

---



## 1. Prerequisites (both modes)



### 1.1 Deploy UCSync into the workspace

```bash
# From a machine with Databricks CLI / REST access to the run host
# Typical workspace path used in this repo:
# /Workspace/Users/<you>/UCSync
```

Required assets on the run host:


| Asset                | Path                                      |
| -------------------- | ----------------------------------------- |
| Main notebook        | `.../UCSync/notebooks/UC_Sync_Main`       |
| Job wrapper notebook | `.../UCSync/notebooks/UC_Sync_Create_Job` |
| Python package       | `.../UCSync/src/uc_sync/`                 |
| Config examples      | `.../UCSync/configs/`                     |




### 1.2 Create ops catalog objects (once)

Run in the **target** workspace SQL editor (adjust names):

```sql
CREATE CATALOG IF NOT EXISTS classic_stable_target_vk;
CREATE SCHEMA IF NOT EXISTS classic_stable_target_vk.uc_sync_ops;
CREATE VOLUME IF NOT EXISTS classic_stable_target_vk.uc_sync_ops.uc_exports;
```

Do **not** hand-create the audit table. `AuditService.ensure_table()` creates the
schema and the audit table on first write, using the canonical 36-column schema in
`src/uc_sync/audit.py`. A hand-written table with a different shape will break the
audit append.

The ops catalog must have working managed storage. If its Azure access connector is
missing or was recreated, table creation fails with
`NOT_FOUND.UC_AZURE_CREDENTIAL_NOT_FOUND` — see [troubleshooting.md](troubleshooting.md).



### 1.3 Job / notebook entry points


| How to run      | Command / action                                                                    |
| --------------- | ----------------------------------------------------------------------------------- |
| Databricks Job  | Create via `UC_Sync_Create_Job` notebook, then `Jobs → Run now`                     |
| Direct notebook | Open `UC_Sync_Main`, set widgets, Run All                                           |
| Python wrapper  | `create_uc_sync_job(...)` / `create_local_sync_job(...)` from `uc_sync.job_wrapper` |




### 1.4 Always dry-run first


| Widget    | First run | Real run |
| --------- | --------- | -------- |
| `dry_run` | `true`    | `false`  |


Dry-run inventorizes and plans statements; it does **not** create objects.

---



## 2. Mode A — LOCAL (same workspace / same metastore)

Use when source and target catalogs live in the **same** Databricks workspace / metastore (catalog-to-catalog copy).

### 2.0 LOCAL — required options


| Widget                                               | Required                               | Example                                                             |
| ---------------------------------------------------- | -------------------------------------- | ------------------------------------------------------------------- |
| `execution_mode`                                     | Yes                                    | `LOCAL`                                                             |
| `mode`                                               | Yes                                    | `INVENTORY` / `EXPORT` / `IMPORT` / `VALIDATE` / `SYNC` / `COMPARE` |
| `catalog_mapping_json` **or** `catalog_mapping_path` | Yes                                    | `{"ril_sandbox":"ril_sandbox_copy"}`                                |
| `catalogs`                                           | Recommended                            | `ril_sandbox`                                                       |
| `schemas`                                            | Optional                               | `ril_sandbox.edge` or bare `edge`                                   |
| `components`                                         | Optional                               | `ALL` (default)                                                     |
| `export_volume_path`                                 | Yes                                    | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports`                     |
| `report_volume_path`                                 | Yes                                    | same as export                                                      |
| `audit_table`                                        | Yes                                    | `classic_stable_target_vk.uc_sync_ops.uc_sync_audit`                           |
| `location_mapping_csv_path`                          | Required for external tables/locations | `/Volumes/.../location-mapping.csv`                                 |
| `dry_run`                                            | Yes                                    | `true` then `false`                                                 |
| Source/target workspace URL / OAuth widgets          | **Leave blank**                        | LOCAL uses the notebook’s current-workspace context                 |


**Catalog mapping JSON example** (`catalog_mapping_json`):

```json
{"ril_sandbox":"ril_sandbox_copy"}
```

Or upload a file and set `catalog_mapping_path`:

```json
{
  "ril_sandbox": "ril_sandbox_copy",
  "ril_curated": "ril_curated_copy"
}
```

---



### 2.1 LOCAL — Step 1: Inventory all objects

**Purpose:** Discover catalogs, schemas, tables (managed + external), views, volumes, functions, and related external locations in scope.

**Widget set:**


| Widget                 | Value                                |
| ---------------------- | ------------------------------------ |
| `execution_mode`       | `LOCAL`                              |
| `mode`                 | `INVENTORY`                          |
| `catalog_mapping_json` | `{"ril_sandbox":"ril_sandbox_copy"}` |
| `catalogs`             | `ril_sandbox`                        |
| `schemas`              | *(blank = all schemas)*              |
| `components`           | `ALL`                                |
| `include_parents`      | `true`                               |
| `dry_run`              | `true`                               |


**Run:**

1. Open `UC_Sync_Main` (or trigger the Job with the parameters above).
2. Run All / Run now.
3. Open the inventory report from the run JSON:

```text
summary["reports"]["inventory"]["xlsx"]
summary["reports"]["inventory"]["html"]
summary["reports"]["inventory"]["xlsx_no_source_metadata"]
summary["reports"]["inventory"]["html_no_source_metadata"]
```

Typical Volume path:

```text
/Volumes/<ops_catalog>/<ops_schema>/<volume>/run_<YYYYMMDD_HHMMSS>/reports/inventory_report.xlsx
/Volumes/<ops_catalog>/<ops_schema>/<volume>/run_<YYYYMMDD_HHMMSS>/reports/inventory_report_no_source_metadata.xlsx
```

**What to verify in the inventory XLSX/HTML:**


| Column / sheet       | Expect                                                                                                                               |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `object_type`        | `CATALOG`, `SCHEMA`, `TABLE`, `EXTERNAL_TABLE`, `VIEW`, `DYNAMIC_VIEW`, `FUNCTION`, `VOLUME`, `EXTERNAL_VOLUME`, `EXTERNAL_LOCATION` |
| `Principals` sheet   | Unique users / groups / service principals granted on inventoried objects                                                            |
| `Object Permissions` | `object_type`, `object_name`, `principal`, `principal_type`, `privileges`                                                            |
| `*_no_source_metadata` | Same reports with the bulky `source_metadata` column removed                                                                       |
| `table_type`         | `MANAGED`, `EXTERNAL`, `VIEW`, …                                                                                                     |
| `data_source_format` | e.g. `DELTA`                                                                                                                         |
| `storage_location`   | Full `abfss://…` path for every external table                                                                                       |
| `full_name`          | Qualified UC name                                                                                                                    |


Notebook / job logs also print:

```text
Inventoried: N
  CATALOG: 1
  SCHEMA: …
  EXTERNAL_TABLE: …
  EXTERNAL_LOCATION: …
```

---



### 2.2 LOCAL — Step 2: Extract unique table paths and external locations

Use the inventory report (or a small SQL/Python cell) to build the mapping inputs.

#### 2.2.1 Unique external table storage paths

From the inventory `Details` sheet / HTML `EXTERNAL_TABLE` section, collect distinct `storage_location` values.

**Python (notebook) example:**

```python
import json
from collections import Counter

# Paste rows from inventory JSON / openpyxl Details sheet
rows = [...]  # list of dicts with object_type, storage_location, full_name, table_type

external = [
    r for r in rows
    if r.get("object_type") == "EXTERNAL_TABLE" and r.get("storage_location")
]

unique_table_paths = sorted({str(r["storage_location"]).rstrip("/") for r in external})
print("unique_external_table_paths", len(unique_table_paths))
for path in unique_table_paths:
    print(path)

# Optional: longest common prefix helpers for CSV source_location roots
by_prefix = Counter(
    "/".join(path.split("/")[:6])  # adjust depth for your ADLS layout
    for path in unique_table_paths
)
print(by_prefix)
```



#### 2.2.2 Unique external locations

Inventory emits `EXTERNAL_LOCATION` rows when they cover inventoried external tables or appear in the mapping CSV.

Also list them via REST / SQL on the **same** workspace:

```sql
SHOW EXTERNAL LOCATIONS;
DESCRIBE EXTERNAL LOCATION classic_stable_target_vk;
```

```bash
# REST (Databricks CLI or curl)
curl -s -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  "$DATABRICKS_HOST/api/2.1/unity-catalog/external-locations" \
  | jq '.external_locations[] | {name, url, credential_name}'
```

Record for each location:


| Field             | Example                                   |
| ----------------- | ----------------------------------------- |
| `name`            | `classic_stable_target_vk`                |
| `url`             | `abfss://…@….dfs.core.windows.net/74056…` |
| `credential_name` | `classic_stable_target_vk`                |




#### 2.2.3 Unique storage credentials

```sql
SHOW STORAGE CREDENTIALS;
DESCRIBE STORAGE CREDENTIAL classic_stable_target_vk;
```

```bash
curl -s -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  "$DATABRICKS_HOST/api/2.1/unity-catalog/storage-credentials" \
  | jq '.storage_credentials[] | {name, read_only, azure_managed_identity}'
```

> Target credentials must already exist. UCSync never creates credential secrets.
> See [§4](#4-shared-appendix--credentials--azure-access-connector) for Azure Access Connector authorization.

---



### 2.3 LOCAL — Step 3: Build and upload mapping files



#### 2.3.1 Catalog mapping

**File:** `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/catalog-mapping.json`

```json
{
  "ril_sandbox": "ril_sandbox_copy"
}
```

Upload (Files API / CLI):

```bash
databricks fs cp ./catalog-mapping.json \
  dbfs:/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/catalog-mapping.json
```

Or REST:

```bash
curl -X PUT \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @catalog-mapping.json \
  "$DATABRICKS_HOST/api/2.0/fs/files/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/catalog-mapping.json?overwrite=true"
```

Widget:

```text
catalog_mapping_path = /Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/catalog-mapping.json
```



#### 2.3.2 Location mapping CSV

**File:** `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv`

Minimal example (new target location URL = table prefix root):

```csv
source_external_location,source_location,target_external_location,target_location,target_credential
source_data,abfss://src@srcacct.dfs.core.windows.net/data,target_data,abfss://dst@dstacct.dfs.core.windows.net/data,target_storage_credential
```

When an **existing broader** external location already covers the target root (Unity Catalog forbids overlapping locations), set `target_external_location_url` to the existing location URL and keep table prefixes under `target_location`:

```csv
source_external_location,source_location,target_external_location,target_location,target_credential,target_external_location_url
classic_stable_target_vk,abfss://unity-catalog-storage@acct.dfs.core.windows.net/74056…/ucsync/ucsync_local/tables,classic_stable_target_vk,abfss://unity-catalog-storage@acct.dfs.core.windows.net/74056…/ucsync/location_mapping/run1/tables,classic_stable_target_vk,abfss://unity-catalog-storage@acct.dfs.core.windows.net/74056…
```

Upload:

```bash
databricks fs cp ./location-mapping.csv \
  dbfs:/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv
```

Widget:

```text
location_mapping_csv_path = /Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv
```

Full column rules: [§5](#5-shared-appendix--location-mapping-csv).

---



### 2.4 LOCAL — Step 4: Create / verify target credentials (manual)

UCSync **does not** create storage credentials. Before IMPORT:

1. Confirm target credential exists (`SHOW STORAGE CREDENTIALS`).
2. Confirm Azure Access Connector MI has Storage Blob Data Contributor (or equivalent) on the target storage account / container.
3. Confirm the credential’s path allowlist covers `target_location` / `target_external_location_url`.

See [§4](#4-shared-appendix--credentials--azure-access-connector).

---



### 2.5 LOCAL — Step 5: Create external locations from the CSV (via IMPORT)

External locations are created (or verified) during **IMPORT** / **SYNC**, not during INVENTORY.

**Dry-run first:**


| Widget                      | Value                                |
| --------------------------- | ------------------------------------ |
| `execution_mode`            | `LOCAL`                              |
| `mode`                      | `IMPORT`                             |
| `components`                | `external_tables`                    |
| `include_parents`           | `true`                               |
| `location_mapping_csv_path` | `/Volumes/.../location-mapping.csv`  |
| `catalog_mapping_json`      | `{"ril_sandbox":"ril_sandbox_copy"}` |
| `dry_run`                   | `true`                               |


Then real run (`dry_run=false`).

**What IMPORT does for locations:**

1. Resolves each inventoried `EXTERNAL_LOCATION` / covering location via the CSV.
2. Runs (conceptually):

```sql
CREATE EXTERNAL LOCATION IF NOT EXISTS `target_data`
URL 'abfss://dst@dstacct.dfs.core.windows.net/data'
WITH (STORAGE CREDENTIAL `target_storage_credential`);
```

1. If the location already exists, verifies URL + credential match the CSV (NOOP on match; ERROR on conflict).
2. Then creates external tables with rewritten `LOCATION` paths.

**Verify:**

```sql
DESCRIBE EXTERNAL LOCATION target_data;
-- url and credential_name must match the CSV
```

---



### 2.6 LOCAL — Step 6: Migrate individual components

Always keep `include_parents=true` so catalogs/schemas are created when needed.


| Component run               | `components` value | Typical objects                       |
| --------------------------- | ------------------ | ------------------------------------- |
| Managed tables only         | `managed_tables`   | `TABLE` (+ parents)                   |
| External tables + locations | `external_tables`  | `EXTERNAL_TABLE`, `EXTERNAL_LOCATION` |
| Views                       | `views`            | `VIEW`, `DYNAMIC_VIEW`                |
| Dynamic views only          | `dynamic_views`    | `DYNAMIC_VIEW`                        |
| Functions                   | `functions`        | `FUNCTION`                            |
| Volumes                     | `volumes`          | `VOLUME`, `EXTERNAL_VOLUME`           |
| Managed volumes             | `managed_volumes`  | `VOLUME`                              |


**Example — functions only:**


| Widget                 | Value                                |
| ---------------------- | ------------------------------------ |
| `execution_mode`       | `LOCAL`                              |
| `mode`                 | `SYNC`                               |
| `components`           | `functions`                          |
| `include_parents`      | `true`                               |
| `catalog_mapping_json` | `{"ril_sandbox":"ril_sandbox_copy"}` |
| `dry_run`              | `false`                              |


**Example — external tables only (requires CSV):**


| Widget                      | Value                               |
| --------------------------- | ----------------------------------- |
| `mode`                      | `SYNC`                              |
| `components`                | `external_tables`                   |
| `location_mapping_csv_path` | `/Volumes/.../location-mapping.csv` |
| `dry_run`                   | `false`                             |


**Recommended per-component order:**

1. `managed_tables` (or schemas/catalogs via parents)
2. `external_tables` (creates/verifies locations + tables)
3. `volumes`
4. `functions`
5. `views` / `dynamic_views` (after base tables exist)
6. `VALIDATE`

After each component:

```text
mode = VALIDATE
components = <same as import>
dry_run = true
```

---



### 2.7 LOCAL — Step 7: Run all components together (example)

**One-shot SYNC (dry-run):**


| Widget                      | Value                                                                       |
| --------------------------- | --------------------------------------------------------------------------- |
| `execution_mode`            | `LOCAL`                                                                     |
| `mode`                      | `SYNC`                                                                      |
| `dry_run`                   | `true`                                                                      |
| `catalog_mapping_json`      | `{"ril_sandbox":"ril_sandbox_copy"}`                                        |
| `catalogs`                  | `ril_sandbox`                                                               |
| `schemas`                   | *(blank or* `ril_sandbox.edge`*)*                                           |
| `components`                | `ALL`                                                                       |
| `include_parents`           | `true`                                                                      |
| `exclude_object_types`      | `MODEL`                                                                     |
| `exclude_regex`             | `.*_TEMP$`                                                                  |
| `location_mapping_csv_path` | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv` |
| `export_volume_path`        | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports`                             |
| `report_volume_path`        | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports`                             |
| `audit_table`               | `classic_stable_target_vk.uc_sync_ops.uc_sync_audit`                                   |


**Real run:** same parameters with `dry_run=false`.

**Python job wrapper equivalent:**

```python
from uc_sync.job_wrapper import UCSyncJobParams, create_uc_sync_job

result = create_uc_sync_job(
    job_name="UC-Sync-Local-All",
    notebook_path="/Repos/UCSync/notebooks/UC_Sync_Main",  # or Workspace path
    params=UCSyncJobParams(
        execution_mode="LOCAL",
        mode="SYNC",
        dry_run="false",
        catalog_mapping_json='{"ril_sandbox":"ril_sandbox_copy"}',
        catalogs="ril_sandbox",
        components="ALL",
        include_parents="true",
        exclude_object_types="MODEL",
        exclude_regex=".*_TEMP$",
        location_mapping_csv_path="/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv",
        export_volume_path="/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports",
        report_volume_path="/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports",
        audit_table="classic_stable_target_vk.uc_sync_ops.uc_sync_audit",
    ),
    run_now=True,
    update_if_exists=True,
    existing_cluster_id="<cluster_id>",  # optional
)
print(result.job_id, result.run_id, result.run_page_url)
```

**Success criteria:**


| Check                           | Expect                                                          |
| ------------------------------- | --------------------------------------------------------------- |
| Exit `status`                   | `SUCCESS` or `COMPLETED_WITH_WARNINGS`                          |
| External tables without mapping | Failures with `LOCATION_MAPPING_MISSING` (fix CSV)              |
| Managed objects                 | Present in target catalog                                       |
| Validation                      | `MATCH` for imported objects; location URL/credential match CSV |


---



## 3. Mode B — CROSS_WORKSPACE (source → target metastore)

Use when source and target are **different** workspaces / metastores (often different Azure regions or subscriptions).

### 3.0 CROSS_WORKSPACE — required options


| Widget                                                      | Required                           | Example                                    |
| ----------------------------------------------------------- | ---------------------------------- | ------------------------------------------ |
| `execution_mode`                                            | Yes                                | `CROSS_WORKSPACE`                          |
| `mode`                                                      | Yes                                | `INVENTORY` … `SYNC`                       |
| `source_workspace_url`                                      | Yes                                | `https://adb-<source>.azuredatabricks.net` |
| `source_oauth_secret_scope`                                 | Yes                                | `uc-migration`                             |
| `source_client_id_secret_key`                               | Yes                                | `source-client-id`                         |
| `source_client_secret_key`                                  | Yes                                | `source-client-secret`                     |
| `target_workspace_url`                                      | Yes                                | `https://adb-<target>.azuredatabricks.net` |
| `target_oauth_secret_scope`                                 | Yes                                | `uc-migration`                             |
| `target_client_id_secret_key`                               | Yes                                | `target-client-id`                         |
| `target_client_secret_key`                                  | Yes                                | `target-client-secret`                     |
| `catalogs`                                                  | Recommended                        | `ril_sandbox`                              |
| `catalog_mapping_json`                                      | Recommended when renaming catalogs | `{"ril_sandbox":"ril_sandbox"}`            |
| `location_mapping_csv_path`                                 | Required for external storage      | `/Volumes/.../location-mapping.csv`        |
| `export_volume_path` / `report_volume_path` / `audit_table` | Yes                                | Target-side Volume + audit table           |
| `dry_run`                                                   | Yes                                | `true` then `false`                        |


**Host the Job in the target workspace.** Source auth is read from the secret scope.

### 3.0.1 Configure secret scope (once)

```bash
# Create scope (Databricks-backed or Azure Key Vault-backed)
databricks secrets create-scope uc-migration

databricks secrets put-secret uc-migration source-client-id
databricks secrets put-secret uc-migration source-client-secret
databricks secrets put-secret uc-migration target-client-id
databricks secrets put-secret uc-migration target-client-secret
```

Grant the Job run-as identity `READ` on the scope.

Optional YAML (`config_path`) can hold the same URLs/keys — see `configs/example.yaml`.

---



### 3.1 CROSS_WORKSPACE — Step 1: Inventory (from source)


| Widget                        | Value                                                        |
| ----------------------------- | ------------------------------------------------------------ |
| `execution_mode`              | `CROSS_WORKSPACE`                                            |
| `mode`                        | `INVENTORY`                                                  |
| `source_workspace_url`        | `https://adb-<source>.azuredatabricks.net`                   |
| `source_oauth_secret_scope`   | `uc-migration`                                               |
| `source_client_id_secret_key` | `source-client-id`                                           |
| `source_client_secret_key`    | `source-client-secret`                                       |
| `target_*`                    | Target workspace OAuth widgets (required by mode validation) |
| `catalogs`                    | `ril_sandbox`                                                |
| `components`                  | `ALL`                                                        |
| `dry_run`                     | `true`                                                       |


Inventory reads the **source** metastore and writes reports to the **target** Volume.

Verify the inventory report includes `storage_location`, `table_type`, and `data_source_format` for every external table.

---



### 3.2 CROSS_WORKSPACE — Step 2: Unique table paths and external locations

Same extraction as LOCAL (§2.2), but:

- Inventory paths are **source** ADLS URLs (west region, source storage account, …).
- Target URLs **must** be different for a region move — never reuse source `abfss://` roots on the target metastore.

Also inventory source locations/credentials on the **source** workspace:

```bash
# Against SOURCE host
curl -s -H "Authorization: Bearer $SOURCE_TOKEN" \
  "$SOURCE_HOST/api/2.1/unity-catalog/external-locations" \
  | jq '.external_locations[] | {name, url, credential_name}'

curl -s -H "Authorization: Bearer $SOURCE_TOKEN" \
  "$SOURCE_HOST/api/2.1/unity-catalog/storage-credentials" \
  | jq '.storage_credentials[] | {name}'
```

And confirm target credential/location capacity on the **target** workspace:

```bash
# Against TARGET host
curl -s -H "Authorization: Bearer $TARGET_TOKEN" \
  "$TARGET_HOST/api/2.1/unity-catalog/storage-credentials" \
  | jq '.storage_credentials[] | {name, azure_managed_identity}'
```

---



### 3.3 CROSS_WORKSPACE — Step 3: Upload mapping files (target Volume)

Upload catalog + location mappings to a Volume **in the target workspace** (Job host).

**Catalog mapping** (optional if catalog names stay identical):

```json
{"ril_sandbox":"ril_sandbox"}
```

**Location mapping CSV (region move example):**

```csv
source_external_location,source_location,target_external_location,target_location,target_credential
classic_stable_westus3_vk,abfss://unity-catalog-storage@dbstoragem73….dfs.core.windows.net/7405618912789045,classic_stable_target_vk,abfss://unity-catalog-storage@dbstorageisbf….dfs.core.windows.net/7405609958717235,classic_stable_target_vk
```

Upload:

```bash
databricks --profile uc-target fs cp ./location-mapping.csv \
  dbfs:/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv
```

Widgets:

```text
location_mapping_csv_path = /Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv
catalog_mapping_json      = {"ril_sandbox":"ril_sandbox"}
```

YAML alternative (`config_path` pointing at `configs/example.yaml`) can include:

```yaml
location_mapping:
  csv_path: "/Volumes/classic_stable_target_vk/uc_sync_ops/config/location-mapping.csv"

storage_credentials:
  classic_stable_westus3_vk:
    target_credential: classic_stable_target_vk

external_locations:
  classic_stable_westus3_vk:
    target_name: classic_stable_target_vk
    target_url: "abfss://…target…"
    target_storage_credential: classic_stable_target_vk
```

CSV path / widgets override YAML when both are set.

---



### 3.4 CROSS_WORKSPACE — Step 4: Target credentials & Azure Access Connector

Before IMPORT on the target metastore:

1. Create (or reuse) a **target** storage credential backed by an Azure Access Connector / managed identity.
2. Grant the MI **Storage Blob Data Contributor** on the target storage account/container.
3. Grant the MI **Reader** on the Access Connector resource itself (required by UC when registering the credential).
4. Ensure path filters / allowlists cover every `target_location` you will register.
5. Never copy source credential secrets into the CSV or audit logs.

Commands and ARM roles: [§4](#4-shared-appendix--credentials--azure-access-connector).

---



### 3.5 CROSS_WORKSPACE — Step 5: Create external locations from CSV


| Widget                      | Value                               |
| --------------------------- | ----------------------------------- |
| `execution_mode`            | `CROSS_WORKSPACE`                   |
| `mode`                      | `IMPORT`                            |
| `components`                | `external_tables`                   |
| `location_mapping_csv_path` | `/Volumes/.../location-mapping.csv` |
| `dry_run`                   | `true` → then `false`               |


Import order (automatic):

1. Verify mapped **target credential** exists (no secret copy).
2. `CREATE EXTERNAL LOCATION IF NOT EXISTS … URL '<target_url>' WITH (STORAGE CREDENTIAL …)`.
3. `CREATE TABLE … LOCATION '<rewritten_target_path>'` for each external table.

If Unity Catalog returns `LOCATION_OVERLAP`, either:

- Map `target_external_location` to the **existing** broader location name, and set `target_external_location_url` to that location’s URL, **or**
- Choose a non-overlapping `target_location` under an uncovered prefix.

---



### 3.6 CROSS_WORKSPACE — Step 6: Individual component migration

Same `components` values as LOCAL ([§6](#6-shared-appendix--component-presets)).

**Example — managed tables only:**


| Widget                      | Value             |
| --------------------------- | ----------------- |
| `execution_mode`            | `CROSS_WORKSPACE` |
| `mode`                      | `SYNC`            |
| `components`                | `managed_tables`  |
| `include_parents`           | `true`            |
| `dry_run`                   | `false`           |
| Source/target OAuth widgets | filled            |


**Example — views after tables:**

```text
components = views
mode       = SYNC
```

**Example — functions:**

```text
components = functions
```

Validate after each wave:

```text
mode = VALIDATE
components = <same>
dry_run = true
```

---



### 3.7 CROSS_WORKSPACE — Step 7: All components together (example)


| Widget                        | Value                                                                       |
| ----------------------------- | --------------------------------------------------------------------------- |
| `execution_mode`              | `CROSS_WORKSPACE`                                                           |
| `mode`                        | `SYNC`                                                                      |
| `dry_run`                     | `false`                                                                     |
| `source_workspace_url`        | `https://adb-<source>.azuredatabricks.net`                                  |
| `source_oauth_secret_scope`   | `uc-migration`                                                              |
| `source_client_id_secret_key` | `source-client-id`                                                          |
| `source_client_secret_key`    | `source-client-secret`                                                      |
| `target_workspace_url`        | `https://adb-<target>.azuredatabricks.net`                                  |
| `target_oauth_secret_scope`   | `uc-migration`                                                              |
| `target_client_id_secret_key` | `target-client-id`                                                          |
| `target_client_secret_key`    | `target-client-secret`                                                      |
| `catalogs`                    | `ril_sandbox`                                                               |
| `catalog_mapping_json`        | `{"ril_sandbox":"ril_sandbox"}`                                             |
| `components`                  | `ALL`                                                                       |
| `include_parents`             | `true`                                                                      |
| `exclude_object_types`        | `MODEL`                                                                     |
| `location_mapping_csv_path`   | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv` |
| `export_volume_path`          | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports`                             |
| `report_volume_path`          | `/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports`                             |
| `audit_table`                 | `classic_stable_target_vk.uc_sync_ops.uc_sync_audit`                                   |
| `config_path`                 | *(optional)* `/Volumes/.../configs/bu001.yaml`                              |


**Python:**

```python
from uc_sync.job_wrapper import UCSyncJobParams, create_uc_sync_job

result = create_uc_sync_job(
    job_name="UC-Sync-XWS-All",
    params=UCSyncJobParams(
        execution_mode="CROSS_WORKSPACE",
        mode="SYNC",
        dry_run="false",
        source_workspace_url="https://adb-<source>.azuredatabricks.net",
        source_oauth_secret_scope="uc-migration",
        source_client_id_secret_key="source-client-id",
        source_client_secret_key="source-client-secret",
        target_workspace_url="https://adb-<target>.azuredatabricks.net",
        target_oauth_secret_scope="uc-migration",
        target_client_id_secret_key="target-client-id",
        target_client_secret_key="target-client-secret",
        catalogs="ril_sandbox",
        catalog_mapping_json='{"ril_sandbox":"ril_sandbox"}',
        components="ALL",
        location_mapping_csv_path="/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv",
        export_volume_path="/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports",
        report_volume_path="/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports",
        audit_table="classic_stable_target_vk.uc_sync_ops.uc_sync_audit",
    ),
    run_now=True,
    update_if_exists=True,
)
```

**Post-run checks:**

```sql
-- On TARGET
SHOW TABLES IN ril_sandbox.<schema>;
DESCRIBE EXTENDED ril_sandbox.<schema>.<external_table>;
-- Location property must show rewritten target abfss path

DESCRIBE EXTERNAL LOCATION classic_stable_target_vk;
```

Compare against validation report: expected vs actual location and credential.

---



## 4. Shared appendix — credentials & Azure access connector



### 4.1 List credentials (REST)

```bash
export DATABRICKS_HOST="https://adb-<workspace>.azuredatabricks.net"
export DATABRICKS_TOKEN="dapi..."

curl -s -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  "$DATABRICKS_HOST/api/2.1/unity-catalog/storage-credentials/<credential_name>" \
  | jq '{
      name,
      read_only,
      azure_managed_identity,
      path_filters
    }'
```

Useful fields:


| Field                                        | Use                                                          |
| -------------------------------------------- | ------------------------------------------------------------ |
| `azure_managed_identity.access_connector_id` | Full Azure resource ID of the Access Connector               |
| `path_filters.allowlist.path_prefixes`       | Allowed `abfss://` prefixes for this credential              |
| `read_only`                                  | Must be `false` to create writable external locations/tables |




### 4.2 Azure Access Connector authorization checklist


| #   | Action                                                                             | Owner              |
| --- | ---------------------------------------------------------------------------------- | ------------------ |
| 1   | Create Databricks Access Connector in the target subscription/RG                   | Azure admin        |
| 2   | Assign MI **Storage Blob Data Contributor** on target storage account or container | Azure admin        |
| 3   | Assign MI **Reader** on the Access Connector resource itself                       | Azure admin        |
| 4   | Register UC storage credential pointing at that Access Connector                   | Metastore admin    |
| 5   | Put credential name into CSV `target_credential`                                   | Migration engineer |
| 6   | Confirm allowlist covers every `target_location`                                   | Migration engineer |


Example Azure CLI (illustrative):

```bash
ACCESS_CONNECTOR_ID="/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Databricks/accessConnectors/<name>"
STORAGE_ID="/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<account>"

# Storage data plane
az role assignment create \
  --assignee-object-id <managed-identity-principal-object-id> \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" \
  --scope "$STORAGE_ID"

# Required by Unity Catalog when registering the credential
az role assignment create \
  --assignee-object-id <managed-identity-principal-object-id> \
  --assignee-principal-type ServicePrincipal \
  --role "Reader" \
  --scope "$ACCESS_CONNECTOR_ID"
```



### 4.3 Create storage credential (manual SQL / REST)

```sql
CREATE STORAGE CREDENTIAL target_storage_credential
WITH AZURE_MANAGED_IDENTITY
  ABFS_ACCESS_CONNECTOR_ID = '/subscriptions/.../accessConnectors/...'
COMMENT 'Target MI for UC Sync external storage';
```

Or REST `POST /api/2.1/unity-catalog/storage-credentials` with `azure_managed_identity.access_connector_id`.

> If you see `UC_CREDENTIAL_MI_IS_MISSING_READER_ROLE`, fix step 3 above — UCSync cannot work around that.



### 4.4 What UCSync will / will not do


| Action                                                    | UCSync                     |
| --------------------------------------------------------- | -------------------------- |
| Read source credential **names**                          | Yes (inventory metadata)   |
| Copy client secrets / keys                                | **Never**                  |
| Create target storage credential                          | **No** (manual / platform) |
| Create / verify external location using mapped credential | **Yes** (IMPORT)           |
| Create external tables at rewritten paths                 | **Yes** (IMPORT)           |


---



## 5. Shared appendix — location mapping CSV



### 5.1 Columns


| Column                         | Required | Description                                                                                   |
| ------------------------------ | -------- | --------------------------------------------------------------------------------------------- |
| `source_location`              | Yes      | Source ADLS/S3/GCS root (longest-prefix match)                                                |
| `target_location`              | Yes      | Target root used to rewrite table paths                                                       |
| `target_external_location`     | Yes      | Target UC external location **name**                                                          |
| `target_credential`            | Yes      | Existing target storage credential **name**                                                   |
| `source_external_location`     | No       | Source location name (recommended)                                                            |
| `target_external_location_url` | No       | Defaults to `target_location`. Set when the UC location root is broader than the table prefix |


Aliases accepted: `source_url`, `target_url`, `target_storage_credential`, `target_name`, …

### 5.2 Rewrite rule

```text
source_table_path = source_location + relative_suffix
target_table_path = target_location + relative_suffix
```

Example:

```text
source_location = abfss://src@acct/data
table path      = abfss://src@acct/data/tables/orders
target_location = abfss://dst@acct/data
rewritten       = abfss://dst@acct/data/tables/orders
```

Longest matching `source_location` wins when multiple rows apply.

### 5.3 Sample files

Repo sample: `configs/location-mapping.csv`

```csv
source_external_location,source_location,target_external_location,target_location,target_credential
source_external_data,abfss://source@sourceaccount.dfs.core.windows.net/data,target_external_data,abfss://target@targetaccount.dfs.core.windows.net/data,target_storage_credential
```

Widget / YAML:

```text
location_mapping_csv_path = /Volumes/.../location-mapping.csv
```

```yaml
location_mapping:
  csv_path: "/Volumes/.../location-mapping.csv"
```

---



## 6. Shared appendix — component presets


| `components` value                     | Includes                                                   |
| -------------------------------------- | ---------------------------------------------------------- |
| `ALL`                                  | No type filter (everything discoverable)                   |
| `tables`                               | managed + external + streaming tables + external locations |
| `managed_tables`                       | managed tables                                             |
| `external_tables`                      | external tables + external locations                       |
| `streaming_tables`                     | streaming tables                                           |
| `views`                                | views + dynamic views                                      |
| `dynamic_views`                        | dynamic views only                                         |
| `materialized_views` / `mvs`           | materialized views                                         |
| `tables_views` / `tables+views`        | tables + views (+ locations)                               |
| `volumes`                              | managed + external volumes                                 |
| `managed_volumes` / `external_volumes` | respective volume types                                    |
| `functions`                            | SQL functions                                              |
| `data_objects`                         | tables, views, MVs, volumes, functions, locations          |
| `TABLE,VIEW,FUNCTION`                  | explicit CSV of object types                               |
| `tables+functions+views`               | combined presets                                           |


`include_parents=true` (default) always keeps `CATALOG` + `SCHEMA` with leaf selections.

Undiscoverable presets (`models`, `grants`, `bindings`, `storage` credentials, sharing, federation) currently resolve to parents-only with a warning — do not use them for production migration until those adapters are implemented.

---



## 7. Shared appendix — report locations & exit JSON



### 7.1 Exit JSON (notebook / job output)

```json
{
  "run_id": "20260815_073615",
  "mode": "SYNC",
  "status": "SUCCESS",
  "inventory": 81,
  "exported": 81,
  "imported": 61,
  "validated": 81,
  "failures": 20,
  "inventory_by_object_type": {"TABLE": 10, "EXTERNAL_TABLE": 10},
  "object_types_not_discoverable": [],
  "object_types_with_no_matches": [],
  "reports": {
    "inventory": {"xlsx": "/Volumes/.../reports/inventory_report.xlsx", "html": "..."},
    "export": {"xlsx": "...", "html": "..."},
    "import": {"xlsx": "...", "html": "..."},
    "validation": {"xlsx": "...", "html": "..."},
    "final": {"xlsx": ".../uc_sync_detailed_report.xlsx", "html": ".../uc_sync_summary.html"}
  }
}
```



### 7.2 Modes vs stages


| `mode`                 | Stages executed                          |
| ---------------------- | ---------------------------------------- |
| `INVENTORY`            | inventory                                |
| `EXPORT`               | inventory + export                       |
| `IMPORT`               | inventory + import                       |
| `COMPARE` / `VALIDATE` | inventory + validation                   |
| `SYNC`                 | inventory + export + import + validation |




### 7.3 Import report columns for storage migration

Confirm these columns exist for external objects:

- `table_type`
- `storage_location` / `source_location`
- `target_location`
- `target_external_location`
- `target_credential`
- `status` / `error_code`

---



## 8. Troubleshooting quick reference


| Symptom                                    | Likely cause                                                     | Action                                                                                                  |
| ------------------------------------------ | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Inventory only catalog/schema              | Wrong `components`, bare schema filter, or undiscoverable preset | Use `ALL` or a discoverable preset; use `catalog.schema` or bare schema (both accepted); check warnings |
| External tables `LOCATION_MAPPING_MISSING` | CSV missing / prefix mismatch                                    | Add matching `source_location` root                                                                     |
| `LOCATION_OVERLAP` on create location      | Target URL under existing location                               | Reuse existing location name + `target_external_location_url`                                           |
| `EXTERNAL_STORAGE_MAPPING_CONFLICT`        | Existing target URL/credential ≠ CSV                             | Fix CSV or drop/recreate conflicting object                                                             |
| `UC_CREDENTIAL_MI_IS_MISSING_READER_ROLE`  | MI lacks Reader on Access Connector                              | Azure role assignment (§4.2)                                                                            |
| Catalog create needs managed location      | Metastore has no default storage                                 | Provide managed storage mapping / `MANAGED LOCATION`                                                    |
| Cross-workspace auth failure               | Bad secret scope keys / SP                                       | Fix secrets; re-run dry-run INVENTORY                                                                   |
| Reports only in Workspace staging          | Volume publish failed                                            | Check Volume ACLs; reports still under `/Workspace/.../report_staging/`                                 |


---



## Quick decision guide

```text
Same workspace, catalog A → catalog B?
  └─ Yes → Mode A LOCAL
       1) INVENTORY
       2) Extract unique paths / locations
       3) Upload catalog + location mappings
       4) Ensure target credential exists
       5) IMPORT/SYNC external_tables (creates locations)
       6) Component waves or components=ALL
       7) VALIDATE

Different workspaces / regions?
  └─ Yes → Mode B CROSS_WORKSPACE
       0) Secret scope + host Job on TARGET
       1) INVENTORY (reads SOURCE)
       2) Extract SOURCE paths; design TARGET URLs
       3) Upload mappings to TARGET Volume
       4) Provision TARGET credential + Access Connector roles
       5) IMPORT locations + external tables via CSV
       6) Component waves or components=ALL
       7) VALIDATE on TARGET
```

---



## Related docs


| Doc                                | Topic                         |
| ---------------------------------- | ----------------------------- |
| `docs/configuration.md`            | Widget / YAML reference       |
| `docs/permissions.md`              | Privilege matrix              |
| `docs/job-deployment.md`           | Job wrapper & DAB             |
| `docs/uc-object-support-matrix.md` | Per-object support level      |
| `docs/architecture.md`             | Design overview               |
| `configs/example.yaml`             | Cross-workspace sample config |
| `configs/location-mapping.csv`     | CSV sample                    |


