# Databricks notebook source
# MAGIC %md
# MAGIC # UC Governance Migration — 02 Export
# MAGIC Turn the inventory into a replayable bundle: full-fidelity `SHOW CREATE`
# MAGIC DDL, grants, governed tags, ABAC policies, classic masks/row filters, plus
# MAGIC a manifest + checksums. Run on the **source** workspace (airgap) or current
# MAGIC workspace (direct). In airgap the whole `run_<id>/` directory is what the
# MAGIC operator moves to the target.

# COMMAND ----------

import json, os, sys
for _p in ("../src", "./src", os.path.abspath(os.path.join(os.getcwd(), "..", "src"))):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from uc_sync.config import from_sources
from uc_sync.export import ExportService
from uc_sync.migrate_export import MigrateExportService
from uc_sync.import_engine import SparkSqlExecutor, RestSqlExecutor
from uc_sync.auth import local_workspace_auth, direct_workspace_auth
from uc_sync.workspace_client import WorkspaceClient
from uc_sync.models import UCObject, ObjectType, LastModifiedSource

# COMMAND ----------

dbutils.widgets.dropdown("connectivity_mode", "direct", ["direct", "airgap"])
dbutils.widgets.text("output_volume_path", "")
dbutils.widgets.text("ops_catalog", "")
dbutils.widgets.text("ops_schema", "")
dbutils.widgets.text("run_id", "")
dbutils.widgets.text("mapping_file_path", "")   # storage-cred + location mapping CSV
# Remote source (direct mode): the export stage captures full-fidelity SHOW CREATE
# DDL from the SOURCE. In direct mode this job runs on the TARGET, where the source
# objects do not exist yet, so — exactly like 01_Inventory — SHOW CREATE must run
# over the source workspace's SQL warehouse. Leave source_workspace_url blank for
# airgap (this notebook then runs on the source and uses local Spark).
dbutils.widgets.text("source_workspace_url", "")
dbutils.widgets.text("source_client_id", "")       # plaintext (never a secret)
dbutils.widgets.text("source_client_secret", "")   # plaintext secret (option 1)
dbutils.widgets.text("source_secret_scope", "")    # secret scope (option 2)
dbutils.widgets.text("source_secret_key", "")      # secret key   (option 2)
dbutils.widgets.text("source_warehouse_id", "")    # source SQL warehouse (direct)

# COMMAND ----------

cfg = from_sources({
    "stage": "EXPORT",
    "connectivity_mode": dbutils.widgets.get("connectivity_mode"),
    "output_volume_path": dbutils.widgets.get("output_volume_path"),
    "ops_catalog": dbutils.widgets.get("ops_catalog"),
    "ops_schema": dbutils.widgets.get("ops_schema"),
    "mapping_file_path": dbutils.widgets.get("mapping_file_path"),
    "source_workspace_url": dbutils.widgets.get("source_workspace_url"),
    "source_client_id": dbutils.widgets.get("source_client_id"),
    "source_client_secret": dbutils.widgets.get("source_client_secret"),
    "source_secret_scope": dbutils.widgets.get("source_secret_scope"),
    "source_secret_key": dbutils.widgets.get("source_secret_key"),
    "source_warehouse_id": dbutils.widgets.get("source_warehouse_id"),
})
run_id = dbutils.widgets.get("run_id").strip()
if not run_id:
    raise ValueError("run_id from the Inventory stage is required")
base = f"{cfg.export_volume_path.rstrip('/')}/run_{run_id}"

def _local(path):
    # UC Volumes are read/written directly at /Volumes/...; only dbfs:/ paths use
    # the /dbfs FUSE mount. (Prefixing /dbfs onto a /Volumes path is wrong.)
    return "/dbfs/" + path[len("dbfs:/"):] if path.startswith("dbfs:/") else path

objects = []
for d in json.load(open(_local(f"{base}/bundle/inventory.json"))):
    d = dict(d)
    d["object_type"] = ObjectType(d["object_type"])
    d["last_modified_source"] = LastModifiedSource(d.get("last_modified_source", "NOT_AVAILABLE"))
    objects.append(UCObject(**d))

# COMMAND ----------

# SHOW CREATE reads run against the workspace that owns the objects. In direct mode
# this job runs on the target, so — like 01_Inventory — capture DDL over the source
# workspace's SQL warehouse. In airgap mode this notebook runs on the source, so the
# local Spark session already sees the objects.
if cfg.source_workspace_url:
    secret = cfg.source_client_secret
    if not secret and cfg.source_secret_scope and cfg.source_secret_key:
        secret = dbutils.secrets.get(scope=cfg.source_secret_scope, key=cfg.source_secret_key)
    source = WorkspaceClient(direct_workspace_auth(cfg.source_workspace_url, cfg.source_client_id, secret))
    if not cfg.source_warehouse_id:
        raise ValueError(
            "source_warehouse_id is required to export a remote source: full-fidelity "
            "SHOW CREATE DDL is captured via the source workspace's SQL warehouse "
            "(Spark on this job runs against the target). Falls back to synthesized "
            "DDL from inventory if omitted."
        )
    ddl_sql = RestSqlExecutor(source, cfg.source_warehouse_id)
else:
    ddl_sql = SparkSqlExecutor(spark)

# Capture full-fidelity DDL + governance artifacts, then path-rewrite to target.
export_root = f"{base}/export"
result = ExportService(export_root, run_id, workspace_root=_local(export_root),
                       sql_executor=ddl_sql).run(objects, dry_run=False)
MigrateExportService(
    source_root=_local(f"{export_root}/run_{run_id}"),
    target_root=_local(f"{base}/migrated"),
    mappings=cfg.mappings, run_id=run_id,
).run(dry_run=False)

# Persist per-object export results into the migrated bundle so the IMPORT stage
# can carry the export_status forward (each report becomes the base for the next).
export_results = result.get("results") or []
with open(f"{_local(base)}/migrated/export_results.json", "w") as fh:
    json.dump(export_results, fh, indent=2, default=str)

# Operator-facing export report from the path-rewritten (migrated) bundle.
try:
    from uc_sync.report import build_report_from_file
    inv = f"{_local(base)}/migrated/inventory/objects.json"
    report_path = f"{_local(base)}/reports/export.xlsx"
    build_report_from_file(inv, report_path, run_id=run_id, stage="EXPORT",
                           export_results=export_results)
    print(f"report: {report_path}")
except Exception as _exc:  # noqa: BLE001 - report is best-effort
    import traceback
    print(f"report generation skipped: {_exc!r}")
    traceback.print_exc()

print(json.dumps({k: v for k, v in result.items() if k != "results"}, indent=2, default=str))
dbutils.notebook.exit(json.dumps({"run_id": run_id, "exported": result.get("exported")}))
