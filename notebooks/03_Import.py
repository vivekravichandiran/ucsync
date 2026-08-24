# Databricks notebook source
# MAGIC %md
# MAGIC # UC Governance Migration — 03 Import
# MAGIC Replay the migrated bundle on the **target** workspace in dependency order:
# MAGIC structure + full table definitions (`create_*` gated) → governed tags →
# MAGIC ABAC policies → classic masks/row filters → grants. Idempotent and additive
# MAGIC — re-runs skip existing objects and never revoke.
# MAGIC
# MAGIC Requires Standard (USER_ISOLATION) or serverless compute for masks/filters.

# COMMAND ----------

import json, os, sys
for _p in ("../src", "./src", os.path.abspath(os.path.join(os.getcwd(), "..", "src"))):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from uc_sync.config import from_sources, CREATE_TOGGLES, APPLY_TOGGLES, _split_csv
from uc_sync.package_import import PackageImportEngine
from uc_sync.import_engine import SparkSqlExecutor
from uc_sync.auth import local_workspace_auth
from uc_sync.workspace_client import WorkspaceClient

# COMMAND ----------

dbutils.widgets.text("output_volume_path", "")
dbutils.widgets.text("ops_catalog", "")
dbutils.widgets.text("ops_schema", "")
dbutils.widgets.text("run_id", "")
# Optional import scope filter: import only a subset of the bundle. Blank = import
# everything. Composed with AND. Catalogs/schemas/functions/volumes needed by a
# selected table still come along; the tables filter narrows only table-like
# securables. Names accept fully-qualified (catalog.schema[.table]) or bare.
dbutils.widgets.text("filter_catalogs", "")   # csv catalog names
dbutils.widgets.text("filter_schemas", "")    # csv catalog.schema (or bare schema)
dbutils.widgets.text("filter_tables", "")     # csv catalog.schema.table (or bare table)
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

# COMMAND ----------

results = PackageImportEngine(
    migrated, SparkSqlExecutor(spark), dry_run=cfg.dry_run, toggles=toggles,
    workspace_client=WorkspaceClient(local_workspace_auth(dbutils)),
    select_catalogs=_split_csv(dbutils.widgets.get("filter_catalogs")),
    select_schemas=_split_csv(dbutils.widgets.get("filter_schemas")),
    select_tables=_split_csv(dbutils.widgets.get("filter_tables")),
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

summary = {}
for r in results:
    summary[r.status] = summary.get(r.status, 0) + 1
print(json.dumps({"run_id": run_id, "by_status": summary}, indent=2))
for r in results:
    if r.status not in ("SUCCESS", "SKIP_EXISTING", "PENDING"):
        print(f"  [{r.status}] {r.object_type} {r.target_full_name}: {str(r.message)[:200]}")
dbutils.notebook.exit(json.dumps({"run_id": run_id, "by_status": summary}))
