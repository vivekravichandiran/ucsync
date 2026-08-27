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

## Notes / gotchas

- **`ai_27`** (out-of-scope catalog for the negative tests) is created without a
  MANAGED LOCATION, so it uses the metastore default storage. If the metastore has
  no default root, give it a `MANAGED LOCATION` on any available external location.
- Storage-account names are globally unique. After a true delete they're normally
  reusable; if `az` reports a name clash, bump a suffix in `config.env`.
- Masks/row filters need Standard or serverless compute — the serverless SQL
  warehouse used here is fine.
