# Databricks notebook source
# MAGIC %md
# MAGIC # UC Governance Migration — 01 Inventory
# MAGIC Read-only discovery of UC structure + governance (grants, tags, ABAC,
# MAGIC classic masks/row filters) for the catalogs/schemas in scope. Writes the
# MAGIC self-describing bundle `run_<id>/bundle/inventory.json` under the output
# MAGIC volume. All logic lives in `src/uc_sync`; this notebook is widgets only.
# MAGIC
# MAGIC Run this on the **source** workspace (airgap) or the current workspace
# MAGIC (direct/local). Requires Standard (USER_ISOLATION) or serverless compute
# MAGIC so masks/row filters are readable.

# COMMAND ----------

import json, os, sys

# Make the packaged src/ importable when run from a Repo/Workspace checkout.
for _p in ("../src", "./src", os.path.abspath(os.path.join(os.getcwd(), "..", "src"))):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from uc_sync.config import from_sources
from uc_sync.inventory import InventoryService
from uc_sync.import_engine import SparkSqlExecutor, RestSqlExecutor
from uc_sync.auth import local_workspace_auth, direct_workspace_auth
from uc_sync.workspace_client import WorkspaceClient

# COMMAND ----------

dbutils.widgets.dropdown("connectivity_mode", "direct", ["direct", "airgap"])
dbutils.widgets.text("catalogs", "")           # csv; blank = whole metastore
dbutils.widgets.text("schemas", "")            # csv catalog.schema; blank = all in scope
dbutils.widgets.text("output_volume_path", "")  # /Volumes/<c>/<s>/<vol>
dbutils.widgets.text("ops_catalog", "")
dbutils.widgets.text("ops_schema", "")
# Remote source (leave source_workspace_url blank to read the CURRENT workspace).
# The service-principal client id is always plaintext (it is not a secret). For the
# SECRET, pick ONE: paste source_client_secret (plaintext), OR name a secret
# source_secret_scope + source_secret_key. If both are given, the plaintext wins.
dbutils.widgets.text("source_workspace_url", "")
dbutils.widgets.text("source_client_id", "")       # plaintext (never a secret)
dbutils.widgets.text("source_client_secret", "")   # plaintext secret (option 1)
dbutils.widgets.text("source_secret_scope", "")    # secret scope (option 2)
dbutils.widgets.text("source_secret_key", "")      # secret key   (option 2)
# SQL warehouse used for governance reads (tags + ABAC policies, which live in
# information_schema). REQUIRED for a remote source (source_workspace_url set).
# STRONGLY RECOMMENDED even for airgap-on-source: classic job-cluster Spark cannot
# serve `information_schema.abac_policy_definitions`, so ABAC policies come back
# EMPTY without a warehouse (tags still work, but ABAC/Policy-Matched-Columns are
# blank). Point it at any SQL warehouse on the workspace that owns the objects.
dbutils.widgets.text("source_warehouse_id", "")
dbutils.widgets.text("run_id", "")

# COMMAND ----------

widgets = {k: dbutils.widgets.get(k) for k in (
    "connectivity_mode", "catalogs", "schemas", "output_volume_path",
    "ops_catalog", "ops_schema", "source_workspace_url",
    "source_client_id", "source_client_secret", "source_secret_scope",
    "source_secret_key", "source_warehouse_id",
)}
widgets["stage"] = "INVENTORY"
cfg = from_sources(widgets)

def _local(path):
    # UC Volumes are read/written directly at /Volumes/...; only dbfs:/ paths use
    # the /dbfs FUSE mount. (Prefixing /dbfs onto a /Volumes path is wrong.)
    return "/dbfs/" + path[len("dbfs:/"):] if path.startswith("dbfs:/") else path

run_id = dbutils.widgets.get("run_id").strip() or spark.sql("SELECT uuid()").collect()[0][0][:8]
run_dir = f"{cfg.export_volume_path.rstrip('/')}/run_{run_id}/bundle"
dbutils.fs.mkdirs(run_dir)

# Source client: current workspace unless a remote source SP is provided.
if cfg.source_workspace_url:
    # Secret = plaintext value if given, else fetched from the named scope/key.
    secret = cfg.source_client_secret
    if not secret and cfg.source_secret_scope and cfg.source_secret_key:
        secret = dbutils.secrets.get(scope=cfg.source_secret_scope, key=cfg.source_secret_key)
    auth = direct_workspace_auth(cfg.source_workspace_url, cfg.source_client_id, secret)
else:
    auth = local_workspace_auth(dbutils)
source = WorkspaceClient(auth)

# Governance reads (tags/ABAC) query information_schema on the workspace that owns
# the objects. Prefer a SQL warehouse whenever one is given: it is REQUIRED for a
# remote source (local Spark points at the wrong workspace), and it is the only
# path that can read `information_schema.abac_policy_definitions` — classic
# job-cluster Spark serves the tag views but returns EMPTY for ABAC, so airgap runs
# without a warehouse silently drop all ABAC policies.
if cfg.source_warehouse_id:
    gov_sql = RestSqlExecutor(source, cfg.source_warehouse_id)
elif cfg.source_workspace_url:
    raise ValueError(
        "source_warehouse_id is required to inventory a remote source: "
        "tags and ABAC policies are read via the source workspace's SQL "
        "warehouse (Spark on this job runs against the target)."
    )
else:
    print(
        "[inventory] WARNING: no source_warehouse_id set — ABAC policies are NOT "
        "readable on classic job compute and will be EMPTY (tags still work). "
        "Provide a SQL warehouse (or run on serverless) to capture ABAC policies."
    )
    gov_sql = SparkSqlExecutor(spark)

# COMMAND ----------

objects = InventoryService(source, cfg, gov_sql).run()
payload = json.dumps([o.to_dict() for o in objects], indent=2, default=str)
dst = f"{_local(run_dir)}/inventory.json"
with open(dst, "w") as fh:
    fh.write(payload)

# Operator-facing inventory report (spine + governance sheets) under reports/.
try:
    from uc_sync.report import build_report
    report_path = f"{cfg.export_volume_path.rstrip('/')}/run_{run_id}/reports/inventory.xlsx"
    build_report([o.to_dict() for o in objects], report_path, run_id=run_id, stage="INVENTORY")
    print(f"report: {report_path}")
except Exception as _exc:  # noqa: BLE001 - report is best-effort
    import traceback
    print(f"report generation skipped: {_exc!r}")
    traceback.print_exc()

by_type = {}
for o in objects:
    by_type[o.object_type.value] = by_type.get(o.object_type.value, 0) + 1
summary = {"run_id": run_id, "run_dir": run_dir, "objects": len(objects), "by_type": by_type}
print(json.dumps(summary, indent=2))
dbutils.notebook.exit(json.dumps(summary))
