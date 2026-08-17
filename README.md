# UCSync

Databricks **notebook + job** Unity Catalog metadata synchronization utility.
It supports cross-workspace migration and same-workspace catalog-to-catalog
local mode. Not a CLI/VM app.

## Status

Live feasibility against `uc-source` / `uc-target` profiles: **GO** for inventory/export/compare. Import mutations require mapping + secret scopes. See [docs/feasibility.md](docs/feasibility.md).

| Workspace | Profile | Metastore |
|-----------|---------|-----------|
| Source | `uc-source` | `008f5578-1fca-4f87-b4fb-ce0545efc00e` (westus3) |
| Target | `uc-target` | `5a7903e7-8a04-4b13-987d-bfa7f6b5e906` (eastus) |

## Layout

```
notebooks/UC_Sync_Main.py        # process orchestrator
notebooks/UC_Sync_Create_Job.py  # creates/runs the Databricks Job
src/uc_sync/                     # implementation package
configs/example.yaml             # mappings + selection
docs/                            # architecture + feasibility
resources/jobs/                  # job stub
tests/
```

## Docs

- [Technical feasibility](docs/feasibility.md)
- [Architecture](docs/architecture.md)
- [Object support matrix](docs/uc-object-support-matrix.md)
- [Permissions](docs/permissions.md)
- [API mapping](docs/api-mapping.md)
- [Local mode and reports](docs/local-mode-and-reports.md)
- [Job deployment / wrapper](docs/job-deployment.md)

## Create the Job

From a Databricks notebook (current workspace auth):

```python
from uc_sync.job_wrapper import create_local_sync_job

result = create_local_sync_job(
    job_name="UC-Sync-Local-Sandbox",
    catalog_mapping_json='{"ril_sandbox":"ril_sandbox_copy"}',
    dry_run="true",
    run_now=True,
)
```

Or open `notebooks/UC_Sync_Create_Job`, set widgets, and run.

## Local mode

No workspace credentials are required:

```text
execution_mode = LOCAL
catalog_mapping_json = {"ril_sandbox":"ril_sandbox_copy"}
components = tables_views
dry_run = true
```

Scope a run with `components`, for example:

- `tables`
- `tables_views` or `tables+views`
- `dynamic_views`
- `tables+functions+volumes`
- `ALL`

Reports land under `<report_volume_path>/run_<run_id>/reports/`.

## Before first Job run

1. Create secret scope `uc-migration` with source/target SP (or PAT `token`) keys  
2. Create export volume + audit schema  
3. Fill `managed_storage` / `external_locations` / `principals` in YAML  
4. Keep `dry_run=true` until COMPARE looks clean  

## Local tests

```bash
pip install -e ".[dev]"  # or: pip install -e . pytest
pytest -q
```
