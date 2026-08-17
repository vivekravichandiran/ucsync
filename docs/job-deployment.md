# Job Deployment

## Recommended host

- Local mode: run in the workspace containing both source and target catalogs;
  no workspace/auth parameters are needed.
- Cross-workspace mode: run in the **target** workspace and pass source
  credentials via secret scope.

## Job creation wrapper

Use `src/uc_sync/job_wrapper.py` or notebook `notebooks/UC_Sync_Create_Job`.

### Python API

```python
from uc_sync.job_wrapper import UCSyncJobParams, create_uc_sync_job, create_local_sync_job

# Same-workspace catalog copy
result = create_local_sync_job(
    job_name="UC-Sync-Local-Sandbox",
    catalog_mapping_json='{"ril_sandbox":"ril_sandbox_copy"}',
    mode="SYNC",
    dry_run="true",
    export_volume_path="/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports",
    report_volume_path="/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports",
    audit_table="classic_stable_target_vk.uc_sync_ops.uc_sync_audit",
    notebook_path="/Repos/UCSync/notebooks/UC_Sync_Main",
    run_now=True,          # create + trigger
    update_if_exists=True, # reset job settings when name already exists
)

print(result.job_id, result.run_id, result.run_page_url)
```

### Notebook wrapper

1. Import the repo into Databricks Repos.
2. Open `notebooks/UC_Sync_Create_Job`.
3. Set widgets (`execution_mode`, `catalog_mapping_json`, volumes, `run_now`, …).
4. Run the notebook — it creates/updates the Job and optionally starts a run.

Returned JSON includes `job_id`, `run_id`, `run_page_url`, and the exact notebook parameters.

## Cluster

- Default wrapper creates a single-node Job cluster (`num_workers=0`).
- Or pass `existing_cluster_id` to attach an all-purpose cluster.
- Libraries installed on the task: `databricks-sdk`, `PyYAML`, `openpyxl`.

## Example DAB fragment

See `resources/jobs/uc_sync_job.yml`.

## Parameters for BU runs

Reuse one job definition; vary:

- `catalogs`, `schemas`
- `export_volume_path` = `/Volumes/.../BU001/run_001`
- `config_path` = BU-specific mapping YAML
- `catalog_mapping_json` or `catalog_mapping_path` for local mode
- `report_volume_path` for XLSX/HTML output

## Exit contract

`UC_Sync_Main` exits with JSON:

```json
{
  "run_id": "...",
  "status": "COMPLETED_WITH_WARNINGS",
  "inventory": 10245,
  "exported": 10201,
  "imported": 10176,
  "validated": 10176,
  "failures": 23,
  "audit_table": "...",
  "export_path": "..."
}
```

Fatal preflight → raise exception (Job FAILED).
