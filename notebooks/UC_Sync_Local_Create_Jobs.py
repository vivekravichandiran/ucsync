# Databricks notebook source
# MAGIC %md
# MAGIC # UC Sync — LOCAL Job Creator (minimal)
# MAGIC Creates Databricks Jobs for **LOCAL** mode only.
# MAGIC
# MAGIC Pick a stage:
# MAGIC - `INVENTORY` / `EXPORT` / `IMPORT` — one job
# MAGIC - `ALL` — three jobs (Inventory, Export, Import)
# MAGIC - `SYNC` — one end-to-end job
# MAGIC
# MAGIC Required: `catalog_mapping_json`, `ops_catalog`, `ops_schema`, and
# MAGIC `output_volume_path`. The audit/state tables are derived as
# MAGIC `{ops_catalog}.{ops_schema}.uc_sync_audit` / `.uc_sync_state`; exports and
# MAGIC reports go to `output_volume_path`. The `UC_Sync_Main` notebook path is
# MAGIC auto-resolved from this notebook's own folder (no widget needed).

# COMMAND ----------

dbutils.widgets.dropdown(
    "stages",
    "ALL",
    ["INVENTORY", "EXPORT", "IMPORT", "ALL", "SYNC"],
)
dbutils.widgets.text(
    "catalog_mapping_json",
    '{"ril_sandbox":"ril_sandbox_ucsync_local"}',
)
dbutils.widgets.text(
    "location_mapping_csv_path",
    "/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports/config/location-mapping.csv",
)
dbutils.widgets.text("catalogs", "ril_sandbox")
dbutils.widgets.text("schemas", "ril_sandbox.ucsync_local_01")
dbutils.widgets.dropdown("dry_run", "false", ["true", "false"])
dbutils.widgets.dropdown("run_now", "false", ["true", "false"])
dbutils.widgets.text("existing_cluster_id", "")
dbutils.widgets.text("job_name_prefix", "UC-Sync-Local")
# Where UCSync writes its own artifacts:
#   ops_catalog + ops_schema  -> audit/state tables (standard names):
#       {ops_catalog}.{ops_schema}.uc_sync_audit / .uc_sync_state
#   output_volume_path        -> volume for exports + reports
dbutils.widgets.text("ops_catalog", "")
dbutils.widgets.text("ops_schema", "")
dbutils.widgets.text("output_volume_path", "")
dbutils.widgets.text("import_package_path", "")

# COMMAND ----------

import json
import sys
from pathlib import Path

for candidate in [
    Path.cwd(),
    Path.cwd().parent,
    Path("/Workspace/Users/vivek.ravichandiran@databricks.com/UCSync"),
    Path("/Workspace/Repos/UCSync"),
]:
    src = candidate / "src"
    if src.exists():
        sys.path.insert(0, str(src))
        break

for module_name in [name for name in sys.modules if name.split(".")[0] == "uc_sync"]:
    del sys.modules[module_name]

from uc_sync.job_wrapper import create_local_stage_jobs, resolve_local_stages


def resolve_main_notebook_path() -> str:
    """Absolute workspace path to the sibling ``UC_Sync_Main`` notebook.

    Databricks Jobs require an absolute workspace path for the notebook task —
    a relative ``./`` reference is not accepted. Since ``UC_Sync_Main`` lives in
    the same folder as this creator notebook, derive its path from this
    notebook's own context path instead of asking the user to type it.
    """
    from posixpath import dirname, join

    ctx = (
        dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    )
    current = ctx.notebookPath().get()
    return join(dirname(current), "UC_Sync_Main")


# COMMAND ----------

stages = dbutils.widgets.get("stages").strip()
catalog_mapping_json = dbutils.widgets.get("catalog_mapping_json").strip()
location_mapping_csv_path = dbutils.widgets.get("location_mapping_csv_path").strip()
catalogs = dbutils.widgets.get("catalogs").strip()
schemas = dbutils.widgets.get("schemas").strip()
dry_run = dbutils.widgets.get("dry_run").strip().lower()
run_now = dbutils.widgets.get("run_now").strip().lower() == "true"
existing_cluster_id = dbutils.widgets.get("existing_cluster_id").strip() or None
job_name_prefix = dbutils.widgets.get("job_name_prefix").strip() or "UC-Sync-Local"
ops_catalog = dbutils.widgets.get("ops_catalog").strip()
ops_schema = dbutils.widgets.get("ops_schema").strip()
output_volume_path = dbutils.widgets.get("output_volume_path").strip()
import_package_path = dbutils.widgets.get("import_package_path").strip()

notebook_path = resolve_main_notebook_path()

if not catalog_mapping_json:
    raise ValueError("catalog_mapping_json is required for LOCAL mode")
if not (ops_catalog and ops_schema and output_volume_path):
    raise ValueError(
        "ops_catalog, ops_schema and output_volume_path are all required."
    )

resolved = resolve_local_stages(stages)
print(f"Creating LOCAL jobs for stages: {', '.join(resolved)}")
print(f"UC_Sync_Main notebook (auto-resolved): {notebook_path}")
if existing_cluster_id:
    print(f"Using existing cluster: {existing_cluster_id}")
else:
    print("Using a new single-node job cluster per job")

results = create_local_stage_jobs(
    stages=stages,
    catalog_mapping_json=catalog_mapping_json,
    location_mapping_csv_path=location_mapping_csv_path,
    catalogs=catalogs,
    schemas=schemas,
    job_name_prefix=job_name_prefix,
    dry_run=dry_run,
    ops_catalog=ops_catalog,
    ops_schema=ops_schema,
    output_volume_path=output_volume_path,
    import_package_path=import_package_path,
    notebook_path=notebook_path,
    run_now=run_now,
    update_if_exists=True,
    existing_cluster_id=existing_cluster_id,
)

summary = {
    "execution_mode": "LOCAL",
    "stages_requested": stages,
    "stages_created": resolved,
    "job_count": len(results),
    "jobs": [item.to_dict() for item in results],
}

print("=" * 58)
print("UC SYNC LOCAL JOBS")
print("=" * 58)
print(json.dumps(summary, indent=2))
print("=" * 58)

dbutils.notebook.exit(json.dumps(summary))
