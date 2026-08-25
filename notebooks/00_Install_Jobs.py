# Databricks notebook source
# MAGIC %md
# MAGIC # UC Governance Migration — 00 Install Jobs
# MAGIC The one wrapper notebook. Enter every widget once, pick which jobs to
# MAGIC create, and run. It stamps your values into the declarative specs under
# MAGIC `jobs/` and creates (or updates) the selected Databricks Jobs:
# MAGIC
# MAGIC | Job | Tasks | Runs on |
# MAGIC |---|---|---|
# MAGIC | **Airgap Inventory+Export (source)** | 01 → 02 | source workspace |
# MAGIC | **Airgap Import (target)** | 03 | target workspace |
# MAGIC | **End-to-end Dry Run** | 01 → 02 → 03 (`dry_run=true`) | one workspace |
# MAGIC | **End-to-end Live** | 01 → 02 → 03 (`dry_run=false`) | one workspace |
# MAGIC
# MAGIC Re-running this notebook **updates** existing jobs of the same name in place.
# MAGIC For **Airgap Import**, `run_id` is a job parameter — set it at run time to the
# MAGIC bundle folder id produced by the source Inventory+Export run.

# COMMAND ----------

import json, os, sys
from posixpath import dirname, join

for _p in ("../src", "./src", os.path.abspath(os.path.join(os.getcwd(), "..", "src"))):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from uc_sync.config import CREATE_TOGGLES, APPLY_TOGGLES
from uc_sync.install_jobs import JOB_LABELS, install_jobs, resolve_job_keys

# COMMAND ----------

# --- which jobs to create ---
dbutils.widgets.multiselect("jobs_to_create", "End-to-end Dry Run", list(JOB_LABELS))
dbutils.widgets.text("job_name_prefix", "UC-Gov-Migration")

# --- scope + ops locations (shared by every job) ---
dbutils.widgets.dropdown("connectivity_mode", "direct", ["direct", "airgap"])  # end-to-end jobs
dbutils.widgets.text("catalogs", "")            # csv; blank = whole metastore
dbutils.widgets.text("schemas", "")             # csv catalog.schema; blank = all in scope
dbutils.widgets.text("output_volume_path", "")  # /Volumes/<c>/<s>/<vol>
dbutils.widgets.text("ops_catalog", "")
dbutils.widgets.text("ops_schema", "")
dbutils.widgets.text("mapping_file_path", "")   # storage-cred + location mapping CSV
dbutils.widgets.text("run_id", "")              # Airgap Import: source bundle id (job param default)

# --- remote source (direct remote / airgap read); blank = current workspace ---
# client id is always plaintext; for the SECRET pick ONE: paste source_client_secret,
# OR name source_secret_scope + source_secret_key. Plaintext wins if both given.
dbutils.widgets.text("source_workspace_url", "")
dbutils.widgets.text("source_client_id", "")       # plaintext (never a secret)
dbutils.widgets.text("source_client_secret", "")   # plaintext secret (option 1)
dbutils.widgets.text("source_secret_scope", "")    # secret scope (option 2)
dbutils.widgets.text("source_secret_key", "")      # secret key   (option 2)
# SQL warehouse for governance reads (tags + ABAC). REQUIRED for a remote source,
# and STRONGLY RECOMMENDED for airgap-on-source too: without it ABAC policies come
# back EMPTY (classic job-cluster Spark cannot serve abac_policy_definitions). Point
# it at any SQL warehouse on the workspace that owns the objects.
dbutils.widgets.text("source_warehouse_id", "")

# --- target run-as service principal (import/e2e jobs) ---
# Application id of a service principal to run the TARGET (import) jobs as, so every
# migrated securable is owned by it and its privileges (CREATE CATALOG, etc.) are
# used. Blank = run as the installing user. The SP must be a workspace member with
# the needed UC privileges. (The source-only Inventory+Export job is unaffected.)
dbutils.widgets.text("run_as_spn", "")

# --- import table filter (import/e2e jobs; blank = import every table).
#     Catalog/schema scoping is set above via `catalogs`/`schemas`. ---
dbutils.widgets.text("filter_tables", "")     # csv catalog.schema.table (or bare table)
# --- catalog rename (import/e2e jobs): replicate a source catalog under a
#     different target name. JSON {"source_catalog":"target_catalog"}; blank = keep. ---
dbutils.widgets.text("catalog_mapping_json", "")
# --- per-object locations (import/e2e jobs): CSV schema,volume,table,location.
#     Schema rows set MANAGED LOCATION; external volume/table rows set LOCATION.
#     Used mainly when replicating into an existing catalog. Blank = catalog root. ---
dbutils.widgets.text("object_locations_path", "")

# --- cluster ---
dbutils.widgets.text("existing_cluster_id", "")  # blank = new USER_ISOLATION job cluster
dbutils.widgets.text("spark_version", "15.4.x-scala2.12")
dbutils.widgets.text("node_type_id", "Standard_DS3_v2")

# --- object-family create + governance apply toggles ---
for _t in (*CREATE_TOGGLES, *APPLY_TOGGLES):
    dbutils.widgets.dropdown(_t, "true", ["true", "false"])

dbutils.widgets.dropdown("run_now", "false", ["true", "false"])

# COMMAND ----------

# This notebook's own folder -> absolute workspace paths for the sibling 01/02/03.
ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
notebook_dir = dirname(ctx.notebookPath().get())

_simple = (
    "connectivity_mode", "catalogs", "schemas", "output_volume_path",
    "ops_catalog", "ops_schema", "mapping_file_path", "run_id",
    "source_workspace_url", "source_client_id", "source_client_secret",
    "source_secret_scope", "source_secret_key", "source_warehouse_id",
    "run_as_spn", "filter_tables", "catalog_mapping_json", "object_locations_path",
    "existing_cluster_id", "spark_version", "node_type_id", "job_name_prefix",
)
values = {k: dbutils.widgets.get(k).strip() for k in _simple}
values["notebook_dir"] = notebook_dir
for _t in (*CREATE_TOGGLES, *APPLY_TOGGLES):
    values[_t] = dbutils.widgets.get(_t)

job_keys = resolve_job_keys(dbutils.widgets.get("jobs_to_create"))
run_now = dbutils.widgets.get("run_now").strip().lower() == "true"

if not (values["output_volume_path"] and values["ops_catalog"] and values["ops_schema"]):
    raise ValueError("output_volume_path, ops_catalog and ops_schema are required.")

# COMMAND ----------

results = install_jobs(job_keys=job_keys, values=values, run_now=run_now)

summary = {
    "notebook_dir": notebook_dir,
    "jobs": [
        {
            "job_name": r.job_name,
            "job_id": r.job_id,
            "status": "updated" if r.updated else "created" if r.created else "unchanged",
            "run_page_url": r.run_page_url,
        }
        for r in results
    ],
}
print(json.dumps(summary, indent=2))
dbutils.notebook.exit(json.dumps(summary))
