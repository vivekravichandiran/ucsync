# Databricks notebook source
# MAGIC %md
# MAGIC # UC Sync — Create / Run Job Wrapper
# MAGIC Creates (or updates) a Databricks Job that runs `UC_Sync_Main` with the
# MAGIC parameters below. Use `execution_mode=LOCAL` for same-workspace catalog copy
# MAGIC — no workspace credential widgets are required.

# COMMAND ----------

dbutils.widgets.dropdown("execution_mode", "LOCAL", ["LOCAL", "CROSS_WORKSPACE"])
dbutils.widgets.dropdown(
    "mode",
    "SYNC",
    ["INVENTORY", "EXPORT", "IMPORT", "SYNC", "COMPARE", "VALIDATE"],
)
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"])
dbutils.widgets.dropdown("run_now", "true", ["true", "false"])
dbutils.widgets.dropdown("update_if_exists", "true", ["true", "false"])

dbutils.widgets.text("job_name", "UC-Sync")
dbutils.widgets.text(
    "notebook_path", "/Repos/UCSync/notebooks/UC_Sync_Main"
)
dbutils.widgets.text(
    "catalog_mapping_json", '{"ril_sandbox":"ril_sandbox_copy"}'
)
dbutils.widgets.text("catalog_mapping_path", "")
dbutils.widgets.text("location_mapping_csv_path", "")
dbutils.widgets.text("catalogs", "")
dbutils.widgets.text("schemas", "")
dbutils.widgets.text("components", "ALL")
dbutils.widgets.text("include_object_types", "")
dbutils.widgets.dropdown("include_parents", "true", ["true", "false"])
dbutils.widgets.text("exclude_object_types", "MODEL")
dbutils.widgets.text("include_regex", "")
dbutils.widgets.text("exclude_regex", ".*_TEMP$")
dbutils.widgets.text(
    "export_volume_path", "/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports"
)
dbutils.widgets.text(
    "report_volume_path", "/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports"
)
dbutils.widgets.text("audit_table", "classic_stable_target_vk.uc_sync_ops.uc_sync_audit")
dbutils.widgets.text("config_path", "")
dbutils.widgets.text("existing_cluster_id", "")
dbutils.widgets.text("spark_version", "15.4.x-scala2.12")
dbutils.widgets.text("node_type_id", "Standard_DS3_v2")
dbutils.widgets.text("num_workers", "0")
# Standard (USER_ISOLATION) or serverless is required to apply column masks /
# row filters. Single-user (SINGLE_USER / assigned) clusters cannot apply them.
dbutils.widgets.text("data_security_mode", "USER_ISOLATION")

# Cross-workspace only
dbutils.widgets.text("source_workspace_url", "")
dbutils.widgets.text("source_oauth_secret_scope", "")
dbutils.widgets.text("source_client_id_secret_key", "")
dbutils.widgets.text("source_client_secret_key", "")
dbutils.widgets.text("target_workspace_url", "")
dbutils.widgets.text("target_oauth_secret_scope", "")
dbutils.widgets.text("target_client_id_secret_key", "")
dbutils.widgets.text("target_client_secret_key", "")

# COMMAND ----------

import json
import sys
from pathlib import Path

for candidate in [
    Path.cwd(),
    Path.cwd().parent,
    Path("/Workspace/Repos/UCSync"),
]:
    src = candidate / "src"
    if src.exists():
        sys.path.insert(0, str(src))
        break

from uc_sync.job_wrapper import UCSyncJobParams, create_uc_sync_job

# COMMAND ----------

params = UCSyncJobParams(
    execution_mode=dbutils.widgets.get("execution_mode"),
    mode=dbutils.widgets.get("mode"),
    dry_run=dbutils.widgets.get("dry_run"),
    catalog_mapping_json=dbutils.widgets.get("catalog_mapping_json"),
    catalog_mapping_path=dbutils.widgets.get("catalog_mapping_path"),
    location_mapping_csv_path=dbutils.widgets.get(
        "location_mapping_csv_path"
    ),
    catalogs=dbutils.widgets.get("catalogs"),
    schemas=dbutils.widgets.get("schemas"),
    components=dbutils.widgets.get("components"),
    include_object_types=dbutils.widgets.get("include_object_types"),
    include_parents=dbutils.widgets.get("include_parents"),
    exclude_object_types=dbutils.widgets.get("exclude_object_types"),
    include_regex=dbutils.widgets.get("include_regex"),
    exclude_regex=dbutils.widgets.get("exclude_regex"),
    export_volume_path=dbutils.widgets.get("export_volume_path"),
    report_volume_path=dbutils.widgets.get("report_volume_path"),
    audit_table=dbutils.widgets.get("audit_table"),
    config_path=dbutils.widgets.get("config_path"),
    source_workspace_url=dbutils.widgets.get("source_workspace_url"),
    source_oauth_secret_scope=dbutils.widgets.get("source_oauth_secret_scope"),
    source_client_id_secret_key=dbutils.widgets.get(
        "source_client_id_secret_key"
    ),
    source_client_secret_key=dbutils.widgets.get("source_client_secret_key"),
    target_workspace_url=dbutils.widgets.get("target_workspace_url"),
    target_oauth_secret_scope=dbutils.widgets.get("target_oauth_secret_scope"),
    target_client_id_secret_key=dbutils.widgets.get(
        "target_client_id_secret_key"
    ),
    target_client_secret_key=dbutils.widgets.get("target_client_secret_key"),
)

existing_cluster_id = dbutils.widgets.get("existing_cluster_id").strip() or None

result = create_uc_sync_job(
    job_name=dbutils.widgets.get("job_name"),
    notebook_path=dbutils.widgets.get("notebook_path"),
    params=params,
    run_now=dbutils.widgets.get("run_now").lower() == "true",
    update_if_exists=dbutils.widgets.get("update_if_exists").lower() == "true",
    existing_cluster_id=existing_cluster_id,
    spark_version=dbutils.widgets.get("spark_version"),
    node_type_id=dbutils.widgets.get("node_type_id"),
    num_workers=int(dbutils.widgets.get("num_workers") or "0"),
    data_security_mode=dbutils.widgets.get("data_security_mode").strip()
    or "USER_ISOLATION",
)

print("=" * 58)
print("UC SYNC JOB WRAPPER")
print("=" * 58)
print(json.dumps(result.to_dict(), indent=2))
print("=" * 58)

dbutils.notebook.exit(json.dumps(result.to_dict()))
