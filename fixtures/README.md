# ai27 UC-migration fixture recreation

Recreates the 3 source test catalogs (`ai27_uc_gov_src`, `ai27_uc_finance`,
`ai27_uc_sales`) — and the Azure storage layer beneath them — from scratch after
the org-wide biweekly cleanup destroys the workspace, catalogs **and** Azure RGs.

Everything here is built from a **live capture** of the source (see
`capture/capture.json` + `capture/governance.json`), so it reflects the real
objects, masks, row filters, tags, ABAC policies and grants — not the older
hand-written `testing/fx_*.sql` (which were stale: wrong storage, missing external
objects / negative fixtures / `region_filter`).

## Prerequisites

- `az` CLI logged into the `azure-sandbox-field-eng` subscription
  (`az login`; `az account set -s edd4cc45-85c7-4aec-8bf5-648062d519bf`).
- `databricks` CLI with the `source_ws` (and `target_ws`) profiles.
- A source SQL warehouse (default `eb2659cbee25f7d0`).

## One command

```bash
python3 fixtures/recreate.py all
```

Runs, in order: **azure → storage → catalogs → objects → negative**. Re-runnable
(idempotent): `az create` and `CREATE … IF NOT EXISTS` are safe to repeat.

Individual stages / dry run:

```bash
python3 fixtures/recreate.py --dry-run all        # print every statement, run nothing
python3 fixtures/recreate.py objects              # just re-apply the object SQL
python3 fixtures/recreate.py azure --azure-scope target   # only target-side Azure infra
```

## Files

| File | What it does |
|---|---|
| `config.env` | All names/ids/regions/warehouses. **Edit here only.** |
| `10_provision_azure.sh` | RGs, ADLS Gen2 accounts (HNS) + `data` container, access connectors, `Storage Blob Data Contributor` role per connector MI. Source + target. |
| `20_uc_storage.sh` | UC storage credentials + external locations (source). |
| `30_catalogs.sql` | `CREATE CATALOG` (managed on account root) + `CREATE SCHEMA` (managed at `<root>/<schema>`). |
| `40_gov_src.sql` | gov_src: 6 UDFs, classic + ABAC masks, 2 row filters, tag-driven ABAC, column tags, 4 views, managed volume, data, grants. |
| `41_finance.sql` | finance: managed + external tables, managed + external volumes, inline mask on `invoices_ext.vendor`, ABAC schema policy, tags, data, grants. |
| `42_sales.sql` | sales: classic mask, partitioned table, view, external volume, data, grants. |
| `50_negative.sql` | fail-closed negative fixtures + their `ai_27.sec.mask_ext` prerequisite. |
| `recreate.py` | Orchestrator: substitutes `{{…}}` placeholders and runs the stages. |
| `capture/` | Live-capture scripts + the JSON snapshots the SQL was authored from. Re-run `capture_source.py` / `capture_governance.py` before a cleanup to refresh ground truth. |

## Target side

`recreate.py azure` provisions the target Azure infra (accounts + connectors in
`ai27-uc-tgt-rg`, eastus2). The **target UC storage credentials, external
locations and catalogs are created by the migration itself** from
`mappings/ai27_target_mapping.csv` — not by this bundle. Target ops tables
(`uc_sync_audit`/`uc_sync_state`) + config volume are (re)created by notebook
`00_Install_Jobs` / the ops-tables setup.

## `ai27_ucsync_testcatalog` — the single comprehensive test bed

A newer, self-contained catalog holding **every UC object type + every ACL combination**,
built for BYO-mode E2E (target catalog/schemas/credential/EL are pre-created too). It has
its own dedicated Azure stack and lives entirely under the `tc_*` stages.

```bash
python3 fixtures/recreate.py testcat          # full build (source + target BYO shell)
python3 fixtures/recreate.py tc_governed      # a single stage
python3 fixtures/recreate.py --dry-run testcat
```

`testcat` runs, in order: **tc_azure → tc_storage → tc_catalog → tc_target_byo →
tc_functions → tc_core → tc_governed → tc_external → tc_files → tc_metric → tc_acl →
tc_negative → tc_reportonly**.

| File | What it does |
|---|---|
| `11_provision_azure_testcat.sh` | Dedicated ADLS account + connector + role, source (eastus) + target (eastus2). |
| `21_uc_storage_testcat.sh` | UC credential + external location on **both** sides (target = BYO prereq). |
| `31_testcat_catalog.sql` | Source catalog + 6 schemas (functions/core_tables/governed/external_store/advanced/restricted). |
| `32_testcat_target_byo.sql` | Empty target catalog + 6 schemas on `target_ws` (BYO shell). |
| `60_testcat_functions.sql` | 4 mask UDFs, 2 row-filter UDFs, a Python UDF, a TVF. |
| `61_testcat_core.sql` | Table zoo (identity/generated/partition/PK+FK/liquid-cluster/DV+row-tracking/CHECK/DEFAULT/VARIANT) + 3 views + restricted tables. |
| `62_testcat_governed.sql` | Classic masks + row filter; ABAC at catalog/schema(EXCEPT)/table scope; tags at catalog/schema/table/column. |
| `63_testcat_external.sql` | External Delta + Parquet tables, inline-mask external table, managed + external volumes (+volume tag). |
| `63_testcat_files.sh` | Seeds nested-subdir files into both volumes. |
| `67_testcat_metric_view.sql` | `advanced.emp_metrics` metric (semantic) view — a MIGRATED object. |
| `65_testcat_acl.sql` | Full grant matrix (users/groups/SPNs) + ownership variety. |
| `66_testcat_negative.sql` | Fail-closed fixtures (`ext_masked`, `abac_ext`) → un-migrated `ai_27.sec.mask_ext`. |
| `64_testcat_reportonly.py` | Report-only infra in `advanced`: MV, real Lakeflow pipeline (streaming table + event log), quality monitor, ML model (2 versions, ML-runtime job), Vector Search endpoint+index, Lakebase instance + synced table. |

Report-only infra needs live features (Lakeflow, Lakehouse Monitoring, MLflow-on-ML-runtime,
Vector Search, Lakebase) — each stage step is independent and prints CREATED/EXISTS/BLOCKED.
The migration only **inventories** these (not migrated), except the metric view (migrated).

## Notes / gotchas

- **`ai_27`** (out-of-scope catalog for the negative tests) is created without a
  MANAGED LOCATION, so it uses the metastore default storage. If the metastore has
  no default root, give it a `MANAGED LOCATION` on any available external location.
- Storage-account names are globally unique. After a true delete they're normally
  reusable; if `az` reports a name clash, bump a suffix in `config.env`.
- Masks/row filters need Standard or serverless compute — the serverless SQL
  warehouse used here is fine.
