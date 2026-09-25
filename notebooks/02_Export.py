# Databricks notebook source
# MAGIC %md
# MAGIC # UC Governance Migration — 02 Export
# MAGIC Turn the inventory into a replayable bundle: full-fidelity `SHOW CREATE`
# MAGIC DDL, grants, governed tags, ABAC policies, classic masks/row filters, plus
# MAGIC a manifest + checksums. Run on the **source** workspace (airgap) or current
# MAGIC workspace (direct). In airgap the whole `run_<id>/` directory is what the
# MAGIC operator moves to the target.
# MAGIC
# MAGIC **A SQL warehouse is required in BOTH modes** (`source_warehouse_id`): all
# MAGIC full-fidelity DDL capture runs `SHOW CREATE` on the warehouse — a classic
# MAGIC Spark cluster is flaky on masked/row-filtered tables, and a failed capture is
# MAGIC a **hard failure** (no silent synthesized fallback that would drop
# MAGIC masks/row filters/constraints). Functions are captured from
# MAGIC `information_schema` over the same warehouse.

# COMMAND ----------

import json, os, sys
for _p in ("../src", "./src", os.path.abspath(os.path.join(os.getcwd(), "..", "src"))):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from uc_sync.config import from_sources
from uc_sync.audit import AuditService, stage_audit_row
from uc_sync.export import ExportService, export_read_failures
from uc_sync.migrate_export import MigrateExportService
from uc_sync.import_engine import SparkSqlExecutor, RestSqlExecutor
from uc_sync.auth import local_workspace_auth, direct_workspace_auth
from uc_sync.workspace_client import WorkspaceClient
from uc_sync.models import UCObject, ObjectType, LastModifiedSource
from uc_sync.logging_util import (
    configure_logging, get_log, set_context, register_secret, get_captured_log,
)

# COMMAND ----------

# Widgets carry a numbered `label` (backlog item 10) so Databricks renders them
# grouped + ordered. The widget NAME/key is never changed.
dbutils.widgets.dropdown("connectivity_mode", "direct", ["direct", "airgap"], "1a. Source · Connectivity mode")
dbutils.widgets.text("source_workspace_url", "", "1b. Source · Workspace URL (blank = current)")
dbutils.widgets.text("source_client_id", "", "1c. Source · SP client id (plaintext)")
dbutils.widgets.text("source_client_secret", "", "1d. Source · SP secret (plaintext, option 1)")
dbutils.widgets.text("source_secret_scope", "", "1e. Source · secret scope (option 2)")
dbutils.widgets.text("source_secret_key", "", "1f. Source · secret key (option 2)")
dbutils.widgets.text("output_volume_path", "", "2d. Scope · Output volume path")
dbutils.widgets.text("ops_catalog", "", "2e. Scope · Ops catalog")
dbutils.widgets.text("ops_schema", "", "2f. Scope · Ops schema")
# The single external-storage mapping file (task 2). Drives the export-time path
# rewrite (source→target external LOCATIONs + external-location URLs). 2-col = BYO,
# 3-col = create SC/EL on import. Blank = none.
dbutils.widgets.text("external_locations_path", "", "2g. Scope · External locations file")
dbutils.widgets.text("source_warehouse_id", "", "5a. Warehouse · Source (SHOW CREATE DDL)")
# Graded environment preflight (task 9): enforced by default.
dbutils.widgets.dropdown("preflight_enforce", "true", ["true", "false"], "8a. Run · Preflight enforce")
# Structured logging verbosity (backlog item 9). INFO by default; DEBUG opt-in.
dbutils.widgets.dropdown("log_level", "INFO", ["INFO", "DEBUG", "WARNING", "ERROR"], "8b. Run · Log level")
# Bounded parallelism for the SHOW CREATE capture burst (backlog item 3). 1 =
# sequential. Keep ≤ the source warehouse's max concurrent queries.
dbutils.widgets.text("parallel_threads", "4", "8g. Run · Parallel threads")
dbutils.widgets.text("run_id", "", "8c. Run · Run id (from Inventory)")

# COMMAND ----------

cfg = from_sources({
    "stage": "EXPORT",
    "connectivity_mode": dbutils.widgets.get("connectivity_mode"),
    "output_volume_path": dbutils.widgets.get("output_volume_path"),
    "ops_catalog": dbutils.widgets.get("ops_catalog"),
    "ops_schema": dbutils.widgets.get("ops_schema"),
    "external_locations_path": dbutils.widgets.get("external_locations_path"),
    "source_workspace_url": dbutils.widgets.get("source_workspace_url"),
    "source_client_id": dbutils.widgets.get("source_client_id"),
    "source_client_secret": dbutils.widgets.get("source_client_secret"),
    "source_secret_scope": dbutils.widgets.get("source_secret_scope"),
    "source_secret_key": dbutils.widgets.get("source_secret_key"),
    "source_warehouse_id": dbutils.widgets.get("source_warehouse_id"),
    "preflight_enforce": dbutils.widgets.get("preflight_enforce"),
    "parallel_threads": dbutils.widgets.get("parallel_threads"),
})

# Structured logging: configure up front so preflight + every step is captured.
configure_logging(stage="EXPORT", level=dbutils.widgets.get("log_level") or "INFO")
log = get_log(__name__)
log.info("stage EXPORT start (connectivity=%s)", cfg.connectivity_mode)

# Graded environment preflight — required libraries importable, before any work.
from uc_sync.preflight import run_preflight, enforce_preflight
enforce_preflight(run_preflight(check_libs=True), enforce=cfg.preflight_enforce)

run_id = dbutils.widgets.get("run_id").strip()
if not run_id:
    raise ValueError("run_id from the Inventory stage is required")
set_context(run_id=run_id)
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

# DDL capture is warehouse-only in BOTH connectivity modes (plan P2-A): SHOW CREATE
# runs reliably on governed tables on a SQL warehouse but is flaky on a classic
# Spark cluster, and there is no synthesized fallback for tables/views (a failed
# capture is a hard failure). So source_warehouse_id is REQUIRED here.
#   - direct: the job runs on the target; reach the source over its warehouse.
#   - airgap: the job runs on the source; use a local client + the source warehouse.
if not cfg.source_warehouse_id:
    raise ValueError(
        "source_warehouse_id is required for export: full-fidelity SHOW CREATE DDL "
        "is captured over a SQL warehouse in both direct and airgap modes (a classic "
        "Spark cluster is unreliable on masked/row-filtered tables, and a failed "
        "capture is a hard failure — there is no synthesized fallback)."
    )
if cfg.source_workspace_url:
    secret = cfg.source_client_secret
    if not secret and cfg.source_secret_scope and cfg.source_secret_key:
        secret = dbutils.secrets.get(scope=cfg.source_secret_scope, key=cfg.source_secret_key)
    register_secret(secret)  # scrub the source SP secret from all log lines
    source = WorkspaceClient(direct_workspace_auth(cfg.source_workspace_url, cfg.source_client_id, secret))
else:
    source = WorkspaceClient(local_workspace_auth(dbutils))
log.info("DDL capture over source warehouse %s for %d objects",
         cfg.source_warehouse_id, len(objects))
ddl_sql = RestSqlExecutor.for_ddl_capture(source, cfg.source_warehouse_id)

# Capture full-fidelity DDL + governance artifacts, then path-rewrite to target.
export_root = f"{base}/export"
result = ExportService(export_root, run_id, workspace_root=_local(export_root),
                       sql_executor=ddl_sql,
                       parallel_threads=cfg.parallel_threads).run(objects, dry_run=False)
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

# Append one EXPORT row per object to {ops_catalog}.{ops_schema}.uc_sync_audit on
# this workspace (best-effort: audit logging must never fail the export).
try:
    if cfg.audit_table and export_results:
        AuditService(spark, cfg.audit_table).append(
            stage_audit_row(run_id=run_id, stage="EXPORT", result=r)
            for r in export_results
        )
        log.info("audit: wrote %d EXPORT rows to %s", len(export_results), cfg.audit_table)
except Exception as _exc:  # noqa: BLE001 - audit is best-effort
    log.warning("audit write skipped: %r", _exc, exc_info=True)

# Operator-facing export report from the path-rewritten (migrated) bundle.
try:
    from uc_sync.report import build_report_from_file
    inv = f"{_local(base)}/migrated/inventory/objects.json"
    report_path = f"{_local(base)}/reports/export.xlsx"
    build_report_from_file(inv, report_path, run_id=run_id, stage="EXPORT",
                           export_results=export_results)
    log.info("report: %s", report_path)
except Exception as _exc:  # noqa: BLE001 - report is best-effort
    log.error("report generation skipped: %r", _exc, exc_info=True)

log.info("export summary: %s",
         json.dumps({k: v for k, v in result.items() if k != "results"}, default=str))

# Bug #2: fail the export LOUDLY if any inventoried object could not be read
# (permission-denied SHOW CREATE, failed DDL capture, …) rather than reporting
# overall success and letting the import run on a partial bundle. The report + audit
# rows above are already written, so the operator sees the real per-object cause;
# the stage then exits non-zero so the pipeline stops before importing an incomplete
# set. Fix the source permission / prerequisite and re-run.
read_failures = export_read_failures(result)
# Persist the full run log alongside the report so a handed-over artifact includes it.
try:
    with open(f"{_local(base)}/reports/export.log", "w") as fh:
        fh.write(get_captured_log())
except Exception as _exc:  # noqa: BLE001 - log persistence is best-effort
    log.warning("run-log persistence skipped: %r", _exc)
if read_failures:
    log.error("%d object(s) could not be read — job will exit non-zero:", len(read_failures))
    for f in read_failures:
        log.error("  [%s] %s %s: %s", f.get('error_code'), f.get('object_type'),
                  f.get('full_name'), str(f.get('error_message'))[:200])
    raise RuntimeError(
        f"UC Sync export could not read {len(read_failures)} inventoried object(s) "
        "(e.g. permission-denied SHOW CREATE / DDL capture failure). The report and "
        "audit rows were written; the bundle is INCOMPLETE, so the export fails "
        "rather than letting the import run on a partial set. Fix the source "
        f"permissions/prerequisites and re-run. run_id={run_id}"
    )
dbutils.notebook.exit(json.dumps({"run_id": run_id, "exported": result.get("exported")}))
