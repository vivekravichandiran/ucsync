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
from uc_sync.import_engine import SparkSqlExecutor
from uc_sync.auth import load_workspace_auth, local_workspace_auth, dbutils_secrets_provider
from uc_sync.workspace_client import WorkspaceClient

# COMMAND ----------

dbutils.widgets.dropdown("connectivity_mode", "direct", ["direct", "airgap"])
dbutils.widgets.text("catalogs", "")           # csv; blank = whole metastore
dbutils.widgets.text("schemas", "")            # csv catalog.schema; blank = all in scope
dbutils.widgets.text("output_volume_path", "")  # /Volumes/<c>/<s>/<vol>
dbutils.widgets.text("ops_catalog", "")
dbutils.widgets.text("ops_schema", "")
# Direct-mode remote source (leave blank to read the current workspace):
dbutils.widgets.text("source_workspace_url", "")
dbutils.widgets.text("source_oauth_secret_scope", "")
dbutils.widgets.text("source_client_id_secret_key", "")
dbutils.widgets.text("source_client_secret_key", "")
dbutils.widgets.text("run_id", "")

# COMMAND ----------

widgets = {k: dbutils.widgets.get(k) for k in (
    "connectivity_mode", "catalogs", "schemas", "output_volume_path",
    "ops_catalog", "ops_schema", "source_workspace_url",
    "source_oauth_secret_scope", "source_client_id_secret_key",
    "source_client_secret_key",
)}
widgets["stage"] = "INVENTORY"
cfg = from_sources(widgets)

run_id = dbutils.widgets.get("run_id").strip() or spark.sql("SELECT uuid()").collect()[0][0][:8]
run_dir = f"{cfg.export_volume_path.rstrip('/')}/run_{run_id}/bundle"
dbutils.fs.mkdirs(run_dir)

# Source client: current workspace unless a remote source SP is provided.
if cfg.source_workspace_url:
    auth = load_workspace_auth(
        cfg.source_workspace_url, cfg.source_oauth_secret_scope,
        cfg.source_client_id_secret_key, cfg.source_client_secret_key,
        dbutils_secrets_provider(dbutils),
    )
else:
    auth = local_workspace_auth(dbutils)
source = WorkspaceClient(auth)

# COMMAND ----------

objects = InventoryService(source, cfg, SparkSqlExecutor(spark)).run()
payload = json.dumps([o.to_dict() for o in objects], indent=2, default=str)
dst = f"/dbfs{run_dir}/inventory.json" if run_dir.startswith("/Volumes") else run_dir + "/inventory.json"
with open(dst, "w") as fh:
    fh.write(payload)

by_type = {}
for o in objects:
    by_type[o.object_type.value] = by_type.get(o.object_type.value, 0) + 1
summary = {"run_id": run_id, "run_dir": run_dir, "objects": len(objects), "by_type": by_type}
print(json.dumps(summary, indent=2))
dbutils.notebook.exit(json.dumps(summary))
