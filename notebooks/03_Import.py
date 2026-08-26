# Databricks notebook source
# MAGIC %md
# MAGIC # UC Governance Migration — 03 Import
# MAGIC Replay the migrated bundle on the **target** workspace in dependency order:
# MAGIC structure + full table definitions (`create_*` gated, masks/row filters kept
# MAGIC INLINE → atomic) → governed tags → ABAC policies → drop sweep (fail-closed) →
# MAGIC views → grants → ownership. Idempotent and additive — re-runs skip existing
# MAGIC objects and never revoke.
# MAGIC
# MAGIC Requires Standard (USER_ISOLATION) or serverless compute for masks/filters.
# MAGIC `import_warehouse_id` (a SQL warehouse) is used for BOTH the ABAC phase and
# MAGIC the **view-creation phase** — a classic Spark cluster errors on a `CREATE
# MAGIC VIEW` over a masked/row-filtered base table, so views build on the warehouse.

# COMMAND ----------

import json, os, sys
for _p in ("../src", "./src", os.path.abspath(os.path.join(os.getcwd(), "..", "src"))):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import uuid
from uc_sync import __version__
from uc_sync.config import from_sources, CREATE_TOGGLES, APPLY_TOGGLES, _split_csv
from uc_sync.package_import import PackageImportEngine
from uc_sync.location_mapping import load_object_locations_csv
from uc_sync.import_engine import SparkSqlExecutor, RestSqlExecutor
from uc_sync.auth import local_workspace_auth
from uc_sync.workspace_client import WorkspaceClient
from uc_sync.audit import AuditService, stage_audit_row
from uc_sync.sync_state import SyncStateService, state_row_from_import

# COMMAND ----------

dbutils.widgets.text("output_volume_path", "")
dbutils.widgets.text("ops_catalog", "")
dbutils.widgets.text("ops_schema", "")
dbutils.widgets.text("run_id", "")
# Optional import TABLE filter: import only a subset of tables from the bundle
# (catalog/schema scoping is done upstream at inventory via catalogs/schemas).
# Blank = import every table. The catalogs/schemas/functions/volumes a selected
# table needs still come along. Names accept fully-qualified (catalog.schema.table)
# or the bare table name.
dbutils.widgets.text("filter_tables", "")
# Optional catalog rename: replicate a source catalog under a different target
# name. JSON object {"source_catalog":"target_catalog"} (blank = keep source
# names). Every replayed statement is rewritten source->target catalog.
dbutils.widgets.text("catalog_mapping_json", "")
# Optional per-object target locations (CSV: schema,volume,table,location).
# A schema listed here is created with that MANAGED LOCATION (else it inherits the
# catalog root); an external volume/table row supplies that object's LOCATION. Used
# mainly when replicating into an existing catalog (its storage credential +
# external location are prerequisites). Blank = every schema uses the catalog root.
dbutils.widgets.text("object_locations_path", "")
# SQL warehouse (this/target workspace) for the ABAC phase AND the view-creation
# phase. CREATE POLICY is rejected at parse on a classic Spark cluster and only
# runs on a SQL warehouse; likewise a CREATE VIEW over a masked/row-filtered base
# table errors on classic Spark but succeeds on a warehouse. REQUIRED when the
# bundle has ABAC policies (otherwise the import fails those closed and drops the
# tables they protect) and strongly recommended whenever the bundle has views over
# masked tables. When unset, views fall back to the Spark executor.
dbutils.widgets.text("import_warehouse_id", "")
for _t in (*CREATE_TOGGLES, *APPLY_TOGGLES):
    dbutils.widgets.dropdown(_t, "true", ["true", "false"])
dbutils.widgets.dropdown("dry_run", "false", ["true", "false"])

# COMMAND ----------

cfg = from_sources({
    "stage": "IMPORT",
    "output_volume_path": dbutils.widgets.get("output_volume_path"),
    "ops_catalog": dbutils.widgets.get("ops_catalog"),
    "ops_schema": dbutils.widgets.get("ops_schema"),
    "dry_run": dbutils.widgets.get("dry_run"),
    "catalog_mapping_json": dbutils.widgets.get("catalog_mapping_json"),
    "import_warehouse_id": dbutils.widgets.get("import_warehouse_id"),
    **{t: dbutils.widgets.get(t) for t in (*CREATE_TOGGLES, *APPLY_TOGGLES)},
})
run_id = dbutils.widgets.get("run_id").strip()
if not run_id:
    raise ValueError("run_id from the Export stage is required")
def _local(path):
    # UC Volumes are read/written directly at /Volumes/...; only dbfs:/ paths use
    # the /dbfs FUSE mount. (Prefixing /dbfs onto a /Volumes path is wrong.)
    return "/dbfs/" + path[len("dbfs:/"):] if path.startswith("dbfs:/") else path

base = f"{cfg.export_volume_path.rstrip('/')}/run_{run_id}"
migrated = f"{_local(base)}/migrated"

toggles = {t: getattr(cfg, t) for t in (*CREATE_TOGGLES, *APPLY_TOGGLES)}

# Optional explicit per-object locations (schema / external volume / external table).
_object_locations_path = _local(dbutils.widgets.get("object_locations_path").strip())
object_locations = (
    load_object_locations_csv(_object_locations_path)
    if _object_locations_path else None
)

# COMMAND ----------

wc = WorkspaceClient(local_workspace_auth(dbutils))
# The SQL warehouse executor runs BOTH the ABAC CREATE POLICY phase and the view-
# creation phase (both are rejected / unreliable on a classic Spark cluster). None
# when unset, in which case an ABAC-carrying bundle fails those policies closed
# (fail-fast) and views fall back to the Spark executor.
abac_executor = (
    RestSqlExecutor(wc, cfg.import_warehouse_id) if cfg.import_warehouse_id else None
)

results = PackageImportEngine(
    migrated, SparkSqlExecutor(spark), dry_run=cfg.dry_run, toggles=toggles,
    workspace_client=wc,
    catalog_mapping=cfg.catalog_mapping,
    select_tables=_split_csv(dbutils.widgets.get("filter_tables")),
    object_locations=object_locations,
    abac_sql_executor=abac_executor,
).run()

# Clean migration report (spine + governance sheets) under reports/. The import
# report carries the export_status forward from stage 02 (export_results.json)
# alongside this stage's import_status, so it is the cumulative base report.
try:
    from uc_sync.report import build_report_from_file
    inv = f"{migrated}/inventory/objects.json"
    report_path = f"{_local(base)}/reports/import.xlsx"
    export_results = []
    _er = f"{migrated}/export_results.json"
    if os.path.exists(_er):
        with open(_er) as fh:
            export_results = json.load(fh)
    build_report_from_file(
        inv, report_path, run_id=run_id, stage="IMPORT",
        export_results=export_results,
        import_results=[r.to_dict() for r in results],
    )
    print(f"report: {report_path}")
except Exception as _exc:  # noqa: BLE001 - report is best-effort
    import traceback
    print(f"report generation skipped: {_exc!r}")
    traceback.print_exc()

# Operations tables under {ops_catalog}.{ops_schema} on THIS (target) workspace:
#   uc_sync_audit — one IMPORT row per object (append-only history).
#   uc_sync_state — one row per source object (MERGE upsert), the per-object
#     last-sync record that a future incremental run would diff against.
# Best-effort: audit/state logging must never fail the migration itself.
try:
    if cfg.audit_table or cfg.state_table:
        result_dicts = [r.to_dict() for r in results]
        try:
            ran_by = spark.sql("SELECT current_user()").collect()[0][0]
        except Exception:  # noqa: BLE001
            ran_by = ""
        if cfg.audit_table:
            AuditService(spark, cfg.audit_table).append(
                stage_audit_row(run_id=run_id, stage="IMPORT", result=rd)
                for rd in result_dicts
            )
            print(f"audit: wrote {len(result_dicts)} IMPORT rows to {cfg.audit_table}")
        if cfg.state_table:
            batch_id = str(uuid.uuid4())
            SyncStateService(spark, cfg.state_table).upsert(
                state_row_from_import(
                    batch_id=batch_id, run_id=run_id, result=rd,
                    ran_by=ran_by, utility_version=__version__,
                )
                for rd in result_dicts
            )
            print(f"state: upserted {len(result_dicts)} rows into {cfg.state_table}")
except Exception as _exc:  # noqa: BLE001 - ops tables are best-effort
    import traceback
    print(f"ops audit/state write skipped: {_exc!r}")
    traceback.print_exc()

summary = {}
for r in results:
    summary[r.status] = summary.get(r.status, 0) + 1
print(json.dumps({"run_id": run_id, "by_status": summary}, indent=2))
for r in results:
    if r.status not in ("SUCCESS", "SKIP_EXISTING", "PENDING"):
        print(f"  [{r.status}] {r.object_type} {r.target_full_name}: {str(r.message)[:200]}")
dbutils.notebook.exit(json.dumps({"run_id": run_id, "by_status": summary}))
