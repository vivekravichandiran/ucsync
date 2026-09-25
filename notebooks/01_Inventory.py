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
from uc_sync.audit import AuditService, stage_audit_row
from uc_sync.inventory import InventoryService
from uc_sync.import_engine import SparkSqlExecutor, RestSqlExecutor
from uc_sync.auth import local_workspace_auth, direct_workspace_auth
from uc_sync.workspace_client import WorkspaceClient
from uc_sync.logging_util import (
    configure_logging, get_log, set_context, register_secret, get_captured_log,
)

# COMMAND ----------

# Widgets carry a numbered `label` (backlog item 10) so Databricks renders them
# grouped + ordered (it sorts by label). The widget NAME/key is never changed — every
# dbutils.widgets.get(...) and job-spec ${...} placeholder is unchanged.
dbutils.widgets.dropdown("connectivity_mode", "direct", ["direct", "airgap"], "1a. Source · Connectivity mode")
dbutils.widgets.text("source_workspace_url", "", "1b. Source · Workspace URL (blank = current)")
dbutils.widgets.text("source_client_id", "", "1c. Source · SP client id (plaintext)")
dbutils.widgets.text("source_client_secret", "", "1d. Source · SP secret (plaintext, option 1)")
dbutils.widgets.text("source_secret_scope", "", "1e. Source · secret scope (option 2)")
dbutils.widgets.text("source_secret_key", "", "1f. Source · secret key (option 2)")
dbutils.widgets.text("catalogs", "", "2a. Scope · Catalogs (csv; blank = all)")
dbutils.widgets.text("schemas", "", "2b. Scope · Schemas (csv catalog.schema)")
# Table EXCLUDE filter (backlog item 2): comma-separated Python regexes matched with
# .search() on catalog.schema.table. Blank = exclude nothing. Escape dots (\.) and
# anchor with $ — a bare `orders` substring-matches `orders_archive`. Parent
# catalog/schema are never excluded. e.g. `.*_TEMP$, sales\.public\.orders_raw$`.
dbutils.widgets.text("exclude_regex", "", "2c. Scope · Exclude regex (csv)")
dbutils.widgets.text("output_volume_path", "", "2d. Scope · Output volume path")
dbutils.widgets.text("ops_catalog", "", "2e. Scope · Ops catalog")
dbutils.widgets.text("ops_schema", "", "2f. Scope · Ops schema")
dbutils.widgets.text("external_locations_path", "", "2g. Scope · External locations file")
# SQL warehouse used for governance reads (tags + ABAC policies, which live in
# information_schema). REQUIRED for a remote source; STRONGLY RECOMMENDED even for
# airgap-on-source (classic Spark returns EMPTY ABAC).
dbutils.widgets.text("source_warehouse_id", "", "5a. Warehouse · Source (governance reads)")
# Graded environment preflight (task 9): enforced by default — a missing report
# library is a red run, never a silent degrade.
dbutils.widgets.dropdown("preflight_enforce", "true", ["true", "false"], "8a. Run · Preflight enforce")
# Structured logging verbosity (backlog item 9). INFO by default; DEBUG opt-in.
dbutils.widgets.dropdown("log_level", "INFO", ["INFO", "DEBUG", "WARNING", "ERROR"], "8b. Run · Log level")
# Bounded parallelism for the per-object grant fan-out (backlog item 3). 1 = sequential.
dbutils.widgets.text("parallel_threads", "4", "8g. Run · Parallel threads")
dbutils.widgets.text("run_id", "", "8c. Run · Run id")

# COMMAND ----------

widgets = {k: dbutils.widgets.get(k) for k in (
    "connectivity_mode", "catalogs", "schemas", "exclude_regex", "output_volume_path",
    "ops_catalog", "ops_schema", "source_workspace_url",
    "source_client_id", "source_client_secret", "source_secret_scope",
    "source_secret_key", "source_warehouse_id", "external_locations_path",
    "preflight_enforce", "parallel_threads",
)}
widgets["stage"] = "INVENTORY"
cfg = from_sources(widgets)

# Structured logging: configure once, up front, so preflight + every step below is
# captured (INVENTORY stage; run_id is stamped in once resolved). The buffer captures
# the whole run's log into a string written alongside the report on the volume.
configure_logging(stage="INVENTORY", level=dbutils.widgets.get("log_level") or "INFO")
log = get_log(__name__)
log.info("stage INVENTORY start (connectivity=%s)", cfg.connectivity_mode)

# Graded environment preflight — required libraries importable, before any work.
from uc_sync.preflight import run_preflight, enforce_preflight
enforce_preflight(run_preflight(check_libs=True), enforce=cfg.preflight_enforce)

def _local(path):
    # UC Volumes are read/written directly at /Volumes/...; only dbfs:/ paths use
    # the /dbfs FUSE mount. (Prefixing /dbfs onto a /Volumes path is wrong.)
    return "/dbfs/" + path[len("dbfs:/"):] if path.startswith("dbfs:/") else path

run_id = dbutils.widgets.get("run_id").strip() or spark.sql("SELECT uuid()").collect()[0][0][:8]
set_context(run_id=run_id)
run_dir = f"{cfg.export_volume_path.rstrip('/')}/run_{run_id}/bundle"
dbutils.fs.mkdirs(run_dir)
log.info("run_id=%s run_dir=%s", run_id, run_dir)

# Source client: current workspace unless a remote source SP is provided.
if cfg.source_workspace_url:
    # Secret = plaintext value if given, else fetched from the named scope/key.
    secret = cfg.source_client_secret
    if not secret and cfg.source_secret_scope and cfg.source_secret_key:
        secret = dbutils.secrets.get(scope=cfg.source_secret_scope, key=cfg.source_secret_key)
    register_secret(secret)  # scrub the source SP secret from all log lines
    log.info("source: remote workspace %s (SP %s)", cfg.source_workspace_url, cfg.source_client_id)
    auth = direct_workspace_auth(cfg.source_workspace_url, cfg.source_client_id, secret)
else:
    log.info("source: current workspace (local auth)")
    auth = local_workspace_auth(dbutils)
source = WorkspaceClient(auth)

# Governance reads (tags/ABAC) query information_schema on the workspace that owns
# the objects. Prefer a SQL warehouse whenever one is given: it is REQUIRED for a
# remote source (local Spark points at the wrong workspace), and it is the only
# path that can read `information_schema.abac_policy_definitions` — classic
# job-cluster Spark serves the tag views but returns EMPTY for ABAC, so airgap runs
# without a warehouse silently drop all ABAC policies.
if cfg.source_warehouse_id:
    log.info("governance reads via SQL warehouse %s", cfg.source_warehouse_id)
    gov_sql = RestSqlExecutor(source, cfg.source_warehouse_id)
elif cfg.source_workspace_url:
    raise ValueError(
        "source_warehouse_id is required to inventory a remote source: "
        "tags and ABAC policies are read via the source workspace's SQL "
        "warehouse (Spark on this job runs against the target)."
    )
else:
    log.warning(
        "no source_warehouse_id set — ABAC policies are NOT readable on classic job "
        "compute and will be EMPTY (tags still work). Provide a SQL warehouse (or run "
        "on serverless) to capture ABAC policies."
    )
    gov_sql = SparkSqlExecutor(spark)

# COMMAND ----------

objects = InventoryService(source, cfg, gov_sql).run()
log.info("inventory discovered %d objects", len(objects))

# Append one INVENTORY row per object to {ops_catalog}.{ops_schema}.uc_sync_audit on
# this workspace (best-effort: audit logging must never fail the inventory).
try:
    if cfg.audit_table and objects:
        AuditService(spark, cfg.audit_table).append(
            stage_audit_row(run_id=run_id, stage="INVENTORY", result=o.to_dict())
            for o in objects
        )
        log.info("audit: wrote %d INVENTORY rows to %s", len(objects), cfg.audit_table)
except Exception as _exc:  # noqa: BLE001 - audit is best-effort
    log.warning("audit write skipped: %r", _exc, exc_info=True)

payload = json.dumps([o.to_dict() for o in objects], indent=2, default=str)
dst = f"{_local(run_dir)}/inventory.json"
with open(dst, "w") as fh:
    fh.write(payload)
log.info("wrote bundle inventory.json (%d bytes)", len(payload))

# Operator-facing inventory report (spine + governance sheets) under reports/.
try:
    from uc_sync.report import build_report
    report_path = f"{cfg.export_volume_path.rstrip('/')}/run_{run_id}/reports/inventory.xlsx"
    build_report([o.to_dict() for o in objects], report_path, run_id=run_id, stage="INVENTORY")
    log.info("report: %s", report_path)
except Exception as _exc:  # noqa: BLE001 - report is best-effort
    log.error("report generation skipped: %r", _exc, exc_info=True)

by_type = {}
for o in objects:
    by_type[o.object_type.value] = by_type.get(o.object_type.value, 0) + 1
summary = {"run_id": run_id, "run_dir": run_dir, "objects": len(objects), "by_type": by_type}
log.info("stage INVENTORY end: %s", json.dumps(by_type))

# Persist the full run log alongside the report so a handed-over artifact includes it.
try:
    log_path = f"{_local(cfg.export_volume_path.rstrip('/'))}/run_{run_id}/reports/inventory.log"
    with open(log_path, "w") as fh:
        fh.write(get_captured_log())
except Exception as _exc:  # noqa: BLE001 - log persistence is best-effort
    log.warning("run-log persistence skipped: %r", _exc)

dbutils.notebook.exit(json.dumps(summary))
