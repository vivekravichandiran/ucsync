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

# Widgets carry a numbered `label` (backlog item 10) so Databricks renders them
# grouped + ordered (it sorts by label). The widget NAME/key is never changed — every
# dbutils.widgets.get(...), the `_simple` passthrough, and job-spec ${...} placeholders
# are unchanged.
# --- 0. Install: which jobs + run-as identities ---
dbutils.widgets.multiselect("jobs_to_create", "End-to-end Dry Run", list(JOB_LABELS), "0a. Install · Jobs to create")
dbutils.widgets.text("job_name_prefix", "UC-Gov-Migration", "0b. Install · Job name prefix")
# Target run-as SPN (import/e2e): every migrated securable is owned by it. Blank = the
# installing user. Source-only Inventory+Export job is unaffected.
dbutils.widgets.text("run_as_spn", "", "0c. Install · Target run-as SPN")
# Source run-as SPN (Airgap Inventory+Export job): source discovery + DDL capture run
# as that read-only SP. Blank = the installing user.
dbutils.widgets.text("source_run_as_spn", "", "0d. Install · Source run-as SPN")

# --- 1. Source connection (blank = current workspace). client id is plaintext; for the
#        SECRET pick ONE: paste source_client_secret, OR name scope + key. ---
dbutils.widgets.dropdown("connectivity_mode", "direct", ["direct", "airgap"], "1a. Source · Connectivity mode")
dbutils.widgets.text("source_workspace_url", "", "1b. Source · Workspace URL (blank = current)")
dbutils.widgets.text("source_client_id", "", "1c. Source · SP client id (plaintext)")
dbutils.widgets.text("source_client_secret", "", "1d. Source · SP secret (plaintext, option 1)")
dbutils.widgets.text("source_secret_scope", "", "1e. Source · secret scope (option 2)")
dbutils.widgets.text("source_secret_key", "", "1f. Source · secret key (option 2)")

# --- 2. Scope + ops locations (shared by every job) ---
dbutils.widgets.text("catalogs", "", "2a. Scope · Catalogs (csv; blank = all)")
dbutils.widgets.text("schemas", "", "2b. Scope · Schemas (csv catalog.schema)")
# Table EXCLUDE filter (backlog item 2): comma-separated Python regexes (.search on
# catalog.schema.table). Blank = exclude nothing. Applied at inventory only.
dbutils.widgets.text("exclude_regex", "", "2c. Scope · Exclude regex (csv)")
dbutils.widgets.text("output_volume_path", "", "2d. Scope · Output volume path")
dbutils.widgets.text("ops_catalog", "", "2e. Scope · Ops catalog")
dbutils.widgets.text("ops_schema", "", "2f. Scope · Ops schema")
# The single external-storage mapping file (task 2). 2 cols = BYO prefix-swap; 3 cols
# (+access connector) = create SC/EL. Drives export path-rewrite AND import swap.
dbutils.widgets.text("external_locations_path", "", "2g. Scope · External locations file")
# Import table filter (import/e2e; blank = every table). Accepts catalog.schema.table.
dbutils.widgets.text("filter_tables", "", "2h. Scope · Import table filter (allowlist)")
# Catalog rename (import/e2e): JSON {"source_catalog":"target_catalog"}; blank = keep.
dbutils.widgets.text("catalog_mapping_json", "", "2i. Scope · Catalog rename JSON")
# Per-object locations (import/e2e): CSV schema,volume,table,location; blank = root.
dbutils.widgets.text("object_locations_path", "", "2j. Scope · Object locations file")

# --- 4d/4e behavior toggles (materialized views + volume data copy) ---
# Materialized views/streaming tables are DLT/SDP-managed report-only; set true to
# migrate materialized views (streaming tables stay report-only).
dbutils.widgets.dropdown("migrate_materialized_views", "false", ["true", "false"], "4d. Apply · Migrate materialized views")
# Volume data copy (FEAT-4): copy volume files via the Files API; default off.
dbutils.widgets.dropdown("copy_volume_data", "false", ["true", "false"], "4e. Apply · Copy volume data")
# Retry-failed-only (backlog item 4; import/e2e jobs): replay only the prior run's failed
# objects + parents from uc_sync_state; same run_id/bundle. Default off.
dbutils.widgets.dropdown("retry_failed_only", "false", ["true", "false"], "4f. Apply · Retry failed only")

# --- 5. Warehouses ---
# Source SQL warehouse for governance reads (tags + ABAC). REQUIRED for a remote
# source; STRONGLY RECOMMENDED for airgap-on-source (classic Spark returns EMPTY ABAC).
dbutils.widgets.text("source_warehouse_id", "", "5a. Warehouse · Source (governance reads)")
# Target SQL warehouse for the ABAC phase. REQUIRED for import/e2e jobs whose bundle
# carries ABAC policies (otherwise those fail closed and their tables are dropped).
dbutils.widgets.text("import_warehouse_id", "", "5b. Warehouse · Import (ABAC + views)")

# --- 6. Cluster ---
dbutils.widgets.text("existing_cluster_id", "", "6a. Cluster · Existing cluster id (blank = new)")
dbutils.widgets.text("spark_version", "15.4.x-scala2.12", "6b. Cluster · Spark version")
dbutils.widgets.text("node_type_id", "Standard_DS3_v2", "6c. Cluster · Node type id")

# --- 7. HTTP proxy for the job cluster (behind-proxy customer networks) ---
# The job cluster does NOT inherit a corporate forward proxy by default. When the
# network requires one, set the two URLs so (a) PyPI library installs reach the internet
# and (b) REST + cross-workspace calls route correctly (injected as spark_env_vars).
# Blank both => nothing injected, cluster routes directly. NO_PROXY is the bypass list
# (Databricks control plane + Azure storage data-plane + loopback/metadata).
dbutils.widgets.text("http_proxy", "", "7a. Proxy · HTTP proxy URL (blank = none)")
dbutils.widgets.text("https_proxy", "", "7b. Proxy · HTTPS proxy URL (blank = none)")
dbutils.widgets.text(
    "no_proxy",
    "localhost,127.0.0.1,169.254.169.254,*.databricks.com,*.azuredatabricks.net,"
    "*.databricks.azure.com,*.dfs.core.windows.net,*.blob.core.windows.net",
    "7c. Proxy · NO_PROXY bypass list",
)

# --- 3. Create toggles + 4a-c apply toggles ---
# BYO-by-default: catalog / schema / storage-credential / external-location creation
# defaults OFF (customer prerequisites); contents + governance default ON.
_TOGGLE_LABELS = {
    "create_storage_credentials": "3a. Create · Storage credentials",
    "create_external_locations": "3b. Create · External locations",
    "create_catalogs": "3c. Create · Catalogs",
    "create_schemas": "3d. Create · Schemas",
    "create_volumes": "3e. Create · Volumes",
    "create_functions": "3f. Create · Functions",
    "create_tables": "3g. Create · Tables",
    "create_views": "3h. Create · Views",
    "create_abac_policies": "3i. Create · ABAC policies",
    "apply_grants": "4a. Apply · Grants",
    "apply_tags": "4b. Apply · Tags",
    "apply_masks_row_filters": "4c. Apply · Masks & row filters",
}
for _t in (*CREATE_TOGGLES, *APPLY_TOGGLES):
    dbutils.widgets.dropdown(
        _t, "false" if _t in BYO_PREREQUISITE_TOGGLES else "true", ["true", "false"],
        _TOGGLE_LABELS.get(_t, _t),
    )

# --- 8. Run controls ---
# Airgap Import: source bundle id (a job-param default set at run time).
dbutils.widgets.text("run_id", "", "8e. Run · Run id (Airgap Import bundle id)")
# Graded preflight (all jobs): NO-GO on a bad environment is enforced by default. Every
# run also always produces its report (bug #4, no opt-out).
dbutils.widgets.dropdown("preflight_enforce", "true", ["true", "false"], "8a. Run · Preflight enforce")
# Structured logging verbosity (all jobs; backlog item 9). INFO default; DEBUG opt-in.
dbutils.widgets.dropdown("log_level", "INFO", ["INFO", "DEBUG", "WARNING", "ERROR"], "8b. Run · Log level")
# Bounded parallelism inside each stage (backlog item 3): Export SHOW CREATE capture +
# Inventory grant fan-out. 1 = sequential. Keep ≤ the warehouse's max concurrent queries.
dbutils.widgets.text("parallel_threads", "4", "8g. Run · Parallel threads")
dbutils.widgets.dropdown("run_now", "false", ["true", "false"], "8f. Run · Run now")

# COMMAND ----------

# This notebook's own folder -> absolute workspace paths for the sibling 01/02/03.
ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
notebook_dir = dirname(ctx.notebookPath().get())

_simple = (
    "connectivity_mode", "catalogs", "schemas", "exclude_regex", "output_volume_path",
    "ops_catalog", "ops_schema", "external_locations_path",
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
values["migrate_materialized_views"] = dbutils.widgets.get("migrate_materialized_views")
values["copy_volume_data"] = dbutils.widgets.get("copy_volume_data")
values["retry_failed_only"] = dbutils.widgets.get("retry_failed_only")
values["preflight_enforce"] = dbutils.widgets.get("preflight_enforce")
values["log_level"] = dbutils.widgets.get("log_level")
values["parallel_threads"] = dbutils.widgets.get("parallel_threads")
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
