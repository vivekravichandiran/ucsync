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

from uc_sync.config import CREATE_TOGGLES, APPLY_TOGGLES, BYO_PREREQUISITE_TOGGLES
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
dbutils.widgets.text("mapping_file_path", "")   # legacy storage-cred + location mapping CSV (back-compat)
# The single external-storage mapping file (task 2). Column shape auto-selects:
# 2 cols (source_base_path,target_base_path) → BYO (storage credential + external
# location are prerequisites; only prefix-swap external LOCATIONs); 3 cols
# (+access_connector_id) → the utility creates the storage credential + external
# location. Supersedes mapping_file_path; drives export path-rewrite AND the
# import-time base-path swap. Blank = none.
dbutils.widgets.text("external_locations_path", "")
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
# SQL warehouse on the TARGET (import) workspace for the ABAC phase. CREATE POLICY
# is rejected at parse on a classic Spark cluster and only runs on a SQL warehouse.
# REQUIRED for import/e2e jobs whose bundle carries ABAC policies (otherwise those
# policies fail closed and their tables are dropped). Blank = no ABAC.
dbutils.widgets.text("import_warehouse_id", "")

# --- target run-as service principal (import/e2e jobs) ---
# Application id of a service principal to run the TARGET (import) jobs as, so every
# migrated securable is owned by it and its privileges (CREATE CATALOG, etc.) are
# used. Blank = run as the installing user. The SP must be a workspace member with
# the needed UC privileges. (The source-only Inventory+Export job is unaffected.)
dbutils.widgets.text("run_as_spn", "")
# --- source run-as service principal (Airgap Inventory+Export job) ---
# Application id of a service principal to run the SOURCE (inventory+export) job as,
# so source discovery + DDL capture run as that read-only SP. Blank = run as the
# installing user. (The end-to-end jobs run all stages as a single job, so they use
# run_as_spn above, not this.)
dbutils.widgets.text("source_run_as_spn", "")

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

# --- HTTP proxy for the job cluster (behind-proxy customer networks) ---
# The job cluster the utility creates does NOT inherit a corporate forward proxy by
# default. When the network requires one, the cluster needs it in its environment so
# (a) PyPI library installs (databricks-sdk / PyYAML / openpyxl) can reach the internet
# and (b) the utility's REST + cross-workspace calls route correctly. These are injected
# as the job cluster's spark_env_vars (both UPPER and lower case) at job-creation time.
# Leave the proxy URLs BLANK when there is no forward proxy (e.g. network-level / VNet
# egress) — blank = nothing injected, so it is safe to leave the widgets in place.
# NO_PROXY is pre-filled with the Databricks control-plane + Azure storage domains
# (the latter so external-volume/table data-plane access is not sent through the proxy);
# it is harmless when no proxy URL is set.
dbutils.widgets.text("http_proxy", "")   # e.g. http://proxy.corp:8080 ; blank = none
dbutils.widgets.text("https_proxy", "")  # e.g. http://proxy.corp:8080 ; blank = none
dbutils.widgets.text(
    "no_proxy",
    "*.azuredatabricks.net,*.databricks.azure.com,*.dfs.core.windows.net,"
    "*.blob.core.windows.net,169.254.169.254,127.0.0.1,localhost",
)

# --- object-family create + governance apply toggles ---
# BYO-by-default: catalog / schema / storage-credential / external-location creation
# defaults OFF (customer prerequisites); contents + governance default ON.
for _t in (*CREATE_TOGGLES, *APPLY_TOGGLES):
    dbutils.widgets.dropdown(
        _t, "false" if _t in BYO_PREREQUISITE_TOGGLES else "true", ["true", "false"]
    )

# --- incremental (delta) sync (import/e2e jobs): run mode is auto-detected from
#     uc_sync_state; force_full re-seeds a full reconcile on demand (default off). ---
dbutils.widgets.dropdown("force_full", "false", ["true", "false"])
# --- report-only Tier-A handling (import/e2e jobs): streaming tables & materialized
#     views are DLT/SDP-managed and report-only; set true to migrate materialized
#     views (streaming tables stay report-only). ---
dbutils.widgets.dropdown("migrate_materialized_views", "false", ["true", "false"])
# --- graded preflight (all jobs): NO-GO on a bad environment (missing report lib,
#     unreachable warehouse) is enforced by default; allow_missing_report makes the
#     import report non-best-effort. ---
dbutils.widgets.dropdown("preflight_enforce", "true", ["true", "false"])
dbutils.widgets.dropdown("allow_missing_report", "false", ["true", "false"])

dbutils.widgets.dropdown("run_now", "false", ["true", "false"])

# COMMAND ----------

# This notebook's own folder -> absolute workspace paths for the sibling 01/02/03.
ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
notebook_dir = dirname(ctx.notebookPath().get())

_simple = (
    "connectivity_mode", "catalogs", "schemas", "output_volume_path",
    "ops_catalog", "ops_schema", "mapping_file_path", "external_locations_path",
    "run_id",
    "source_workspace_url", "source_client_id", "source_client_secret",
    "source_secret_scope", "source_secret_key", "source_warehouse_id",
    "import_warehouse_id",
    "run_as_spn", "source_run_as_spn", "filter_tables", "catalog_mapping_json",
    "object_locations_path",
    "existing_cluster_id", "spark_version", "node_type_id", "job_name_prefix",
    "http_proxy", "https_proxy", "no_proxy",
)
values = {k: dbutils.widgets.get(k).strip() for k in _simple}
values["notebook_dir"] = notebook_dir
values["force_full"] = dbutils.widgets.get("force_full")
values["migrate_materialized_views"] = dbutils.widgets.get("migrate_materialized_views")
values["preflight_enforce"] = dbutils.widgets.get("preflight_enforce")
values["allow_missing_report"] = dbutils.widgets.get("allow_missing_report")
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
