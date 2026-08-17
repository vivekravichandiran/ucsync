# Databricks notebook source
# MAGIC %md
# MAGIC # Unity Catalog Synchronization Utility
# MAGIC Thin orchestrator — implementation lives in `src/uc_sync`.

# COMMAND ----------

dbutils.widgets.dropdown(
    "mode",
    "INVENTORY",
    ["INVENTORY", "EXPORT", "IMPORT", "SYNC", "COMPARE", "VALIDATE"],
)
dbutils.widgets.dropdown(
    "execution_mode", "LOCAL", ["LOCAL", "CROSS_WORKSPACE"]
)
dbutils.widgets.text("source_workspace_url", "")
dbutils.widgets.text("source_oauth_secret_scope", "")
dbutils.widgets.text("source_client_id_secret_key", "")
dbutils.widgets.text("source_client_secret_key", "")
dbutils.widgets.text("source_token_secret_key", "source-token")
dbutils.widgets.text("target_workspace_url", "")
dbutils.widgets.text("target_oauth_secret_scope", "")
dbutils.widgets.text("target_client_id_secret_key", "")
dbutils.widgets.text("target_client_secret_key", "")
dbutils.widgets.text("target_token_secret_key", "target-token")
dbutils.widgets.text(
    "export_volume_path",
    "/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports",
)
dbutils.widgets.text(
    "report_volume_path",
    "/Volumes/classic_stable_target_vk/uc_sync_ops/uc_exports",
)
dbutils.widgets.text(
    "audit_table", "classic_stable_target_vk.uc_sync_ops.uc_sync_audit"
)
dbutils.widgets.text(
    "state_table", "classic_stable_target_vk.uc_sync_ops.uc_sync_state"
)
dbutils.widgets.text("import_package_path", "")
dbutils.widgets.text("catalogs", "")
dbutils.widgets.text(
    "catalog_mapping_json", '{"ril_sandbox":"ril_sandbox_copy"}'
)
dbutils.widgets.text("catalog_mapping_path", "")
dbutils.widgets.text("location_mapping_csv_path", "")
dbutils.widgets.text("schemas", "")
dbutils.widgets.text(
    "components",
    "ALL",
)
dbutils.widgets.text("include_object_types", "")
dbutils.widgets.dropdown("include_parents", "true", ["true", "false"])
dbutils.widgets.text("exclude_object_types", "MODEL")
dbutils.widgets.text("include_regex", "")
dbutils.widgets.text("exclude_regex", ".*_TEMP$")
dbutils.widgets.text("config_path", "")
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"])

# COMMAND ----------

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Repo root on Databricks Repos / workspace files
for candidate in [
    Path.cwd(),
    Path.cwd().parent,
    Path("/Workspace/Users/vivek.ravichandiran@databricks.com/UCSync"),
    Path("/Workspace/Repos/UCSync"),
]:
    src = candidate / "src"
    if src.exists():
        sys.path.insert(0, str(src))
        break

# Interactive notebook sessions keep the Python interpreter alive, so a
# re-run would otherwise reuse modules imported before the latest deploy.
for module_name in [name for name in sys.modules if name.split(".")[0] == "uc_sync"]:
    del sys.modules[module_name]

from uc_sync import __version__
from uc_sync.audit import AuditService, stage_audit_row
from uc_sync.auth import (
    WorkspaceAuth,
    dbutils_secrets_provider,
    load_workspace_auth,
    local_workspace_auth,
)
from uc_sync.components import undiscoverable_types
from uc_sync.config import from_sources, load_yaml
from uc_sync.export import ExportService
from uc_sync.import_engine import ImportEngine, SparkSqlExecutor
from uc_sync.inventory import InventoryService
from uc_sync.migrate_export import MigrateExportService
from uc_sync.models import RunStatus
from uc_sync.package_import import PackageImportEngine
from uc_sync.reporting import ReportService, is_error
from uc_sync.sync_state import SyncStateService, state_row_from_import
from uc_sync.validation import ValidationService
from uc_sync.workspace_client import WorkspaceClient

# COMMAND ----------

widget_values = {
    "execution_mode": dbutils.widgets.get("execution_mode"),
    "mode": dbutils.widgets.get("mode"),
    "source_workspace_url": dbutils.widgets.get("source_workspace_url"),
    "source_oauth_secret_scope": dbutils.widgets.get("source_oauth_secret_scope"),
    "source_client_id_secret_key": dbutils.widgets.get("source_client_id_secret_key"),
    "source_client_secret_key": dbutils.widgets.get("source_client_secret_key"),
    "source_token_secret_key": dbutils.widgets.get("source_token_secret_key"),
    "target_workspace_url": dbutils.widgets.get("target_workspace_url"),
    "target_oauth_secret_scope": dbutils.widgets.get("target_oauth_secret_scope"),
    "target_client_id_secret_key": dbutils.widgets.get("target_client_id_secret_key"),
    "target_client_secret_key": dbutils.widgets.get("target_client_secret_key"),
    "target_token_secret_key": dbutils.widgets.get("target_token_secret_key"),
    "export_volume_path": dbutils.widgets.get("export_volume_path"),
    "report_volume_path": dbutils.widgets.get("report_volume_path"),
    "audit_table": dbutils.widgets.get("audit_table"),
    "state_table": dbutils.widgets.get("state_table"),
    "import_package_path": dbutils.widgets.get("import_package_path"),
    "catalog_mapping_json": dbutils.widgets.get("catalog_mapping_json"),
    "catalog_mapping_path": dbutils.widgets.get("catalog_mapping_path"),
    "location_mapping_csv_path": dbutils.widgets.get(
        "location_mapping_csv_path"
    ),
    "catalogs": dbutils.widgets.get("catalogs"),
    "schemas": dbutils.widgets.get("schemas"),
    "components": dbutils.widgets.get("components"),
    "include_object_types": dbutils.widgets.get("include_object_types"),
    "include_parents": dbutils.widgets.get("include_parents"),
    "exclude_object_types": dbutils.widgets.get("exclude_object_types"),
    "include_regex": dbutils.widgets.get("include_regex"),
    "exclude_regex": dbutils.widgets.get("exclude_regex"),
    "dry_run": dbutils.widgets.get("dry_run"),
}

config_path = dbutils.widgets.get("config_path")
file_cfg = load_yaml(config_path) if config_path else {}

cfg = from_sources(widget_values, file_cfg)
run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

print("=" * 58)
print("Unity Catalog Synchronization Utility")
print("=" * 58)
print(f"Run ID            : {run_id}")
print(f"Version           : {__version__}")
print(f"Mode              : {cfg.mode}")
print(f"Execution Mode    : {cfg.execution_mode}")
print(
    f"Workspace         : "
    f"{'(current workspace)' if cfg.execution_mode == 'LOCAL' else cfg.target_workspace_url}"
)
print(f"Catalog Mapping   : {json.dumps(cfg.catalog_mapping)}")
print(
    f"Location Mapping  : "
    f"{cfg.location_mapping_csv_path or '(YAML / none)'}"
)
print(f"Export Volume     : {cfg.export_volume_path}")
print(f"Report Volume     : {cfg.report_volume_path}")
print(f"Catalogs          : {', '.join(cfg.catalogs) or '(all non-system)'}")
print(f"Schemas           : {', '.join(cfg.schemas) or '(all)'}")
print(f"Components        : {cfg.components}")
print(
    f"Include Types     : "
    f"{', '.join(cfg.include_object_types) or 'ALL'}"
)
print(f"Exclude Types     : {', '.join(cfg.exclude_object_types) or '(none)'}")
print(f"Dry Run           : {cfg.dry_run}")
print("=" * 58)

# COMMAND ----------

print("[1/7] Loading configuration — done")
print("[2/7] Authenticating")

secrets = dbutils_secrets_provider(dbutils)


def _auth_or_fail(
    label: str,
    host: str,
    scope: str,
    id_key: str,
    secret_key: str,
    token_key: str = "token",
) -> WorkspaceAuth:
    try:
        return load_workspace_auth(
            host, scope, id_key, secret_key, secrets, token_key=token_key
        )
    except Exception as exc:
        raise RuntimeError(f"{label} authentication failed: {exc}") from exc


# LOCAL needs no workspace or credential parameters. It uses the notebook's
# short-lived current-workspace context for both source and target catalogs.
if cfg.execution_mode == "LOCAL":
    local_auth = local_workspace_auth(dbutils)
    source_auth = local_auth
    target_auth = local_auth
    cfg.source_workspace_url = local_auth.host
    cfg.target_workspace_url = local_auth.host
else:
    source_auth = _auth_or_fail(
        "Source",
        cfg.source_workspace_url,
        cfg.source_oauth_secret_scope,
        cfg.source_client_id_secret_key,
        cfg.source_client_secret_key,
        token_key=dbutils.widgets.get("source_token_secret_key") or "source-token",
    )
    target_auth = _auth_or_fail(
        "Target",
        cfg.target_workspace_url,
        cfg.target_oauth_secret_scope,
        cfg.target_client_id_secret_key,
        cfg.target_client_secret_key,
        token_key=dbutils.widgets.get("target_token_secret_key") or "target-token",
    )

source_client = WorkspaceClient(source_auth)
target_client = WorkspaceClient(target_auth)

print("[3/7] Running preflight")
preflight = []
source_metastore_id = ""
target_metastore_id = ""
clients_to_check = (
    [("Current Workspace", source_client)]
    if cfg.execution_mode == "LOCAL"
    else [("Source", source_client), ("Target", target_client)]
)
for label, client in clients_to_check:
    try:
        assignment = client.current_metastore_assignment()
        if label in {"Source", "Current Workspace"}:
            source_metastore_id = assignment.get("metastore_id") or ""
        if label in {"Target", "Current Workspace"}:
            target_metastore_id = assignment.get("metastore_id") or ""
        preflight.append((f"{label} UC Access", "PASS", assignment.get("metastore_id")))
    except Exception as exc:
        preflight.append((f"{label} UC Access", "FAIL", str(exc)))

for name, status, detail in preflight:
    print(f"  {name:28} {status:6} {detail}")
if any(s == "FAIL" for _, s, _ in preflight):
    raise RuntimeError("Preflight failed — aborting before mutations")

# COMMAND ----------

summary = {
    "run_id": run_id,
    "mode": cfg.mode,
    "dry_run": cfg.dry_run,
    "inventory": 0,
    "exported": 0,
    "migrated": 0,
    "imported": 0,
    "validated": 0,
    "failures": 0,
    "status": RunStatus.SUCCESS.value,
    "audit_table": cfg.audit_table,
    "state_table": cfg.state_table,
    "location_mapping_csv_path": cfg.location_mapping_csv_path,
    "location_mappings": len(cfg.mappings.get("location_mappings") or []),
    "export_path": "",
    "export_workspace_path": "",
    "migrated_workspace_path": "",
    "import_package_path": cfg.import_package_path,
    "reports": {},
}

objects = []
stage_rows = {}
audit_rows = []
state_rows = []
reporter = ReportService(cfg.report_volume_path, run_id, fs=dbutils.fs)
export_workspace_path = ""
migrated_workspace_path = ""

try:
    ran_by = spark.sql("SELECT current_user()").collect()[0][0]
except Exception:
    ran_by = "unknown"


def _inventory_rows(items):
    rows = []
    for obj in items:
        row = obj.to_dict()
        row["target_catalog"] = cfg.catalog_mapping.get(obj.catalog or "", "")
        row["target_full_name"] = (
            row["target_catalog"] + obj.full_name[len(obj.catalog or "") :]
            if obj.catalog and row["target_catalog"]
            else ""
        )
        rows.append(row)
    return rows


def _enrich_object_rows(rows, *, name_key):
    """Attach complete source metadata to export/import/validation rows."""
    by_name = {obj.full_name: obj.to_dict() for obj in objects}
    enriched = []
    for result in rows:
        row = dict(result)
        source_name = str(row.get(name_key) or "")
        metadata = by_name.get(source_name, {})
        enriched.append({**metadata, **row})
    return enriched


def _audit_stage(stage, rows):
    for row in rows:
        audit_rows.append(
            stage_audit_row(
                run_id=run_id,
                stage=stage,
                result=row,
                source_workspace_url=cfg.source_workspace_url,
                target_workspace_url=cfg.target_workspace_url,
                source_metastore_id=source_metastore_id,
                target_metastore_id=target_metastore_id,
            )
        )


print("[4/7] Inventorying Unity Catalog")
if cfg.mode in {"INVENTORY", "EXPORT", "IMPORT", "SYNC", "COMPARE", "VALIDATE"}:
    objects = InventoryService(source_client, cfg).run()
    summary["inventory"] = len(objects)
    print(f"  Inventoried: {len(objects)}")
    discovered = Counter(obj.object_type.value for obj in objects)
    summary["inventory_by_object_type"] = dict(sorted(discovered.items()))
    for object_type, count in summary["inventory_by_object_type"].items():
        print(f"    {object_type}: {count}")
    unsupported = undiscoverable_types(cfg.include_object_types)
    empty = [
        object_type
        for object_type in cfg.include_object_types
        if str(object_type).upper() not in unsupported
        and not discovered.get(str(object_type).upper())
    ]
    summary["object_types_not_discoverable"] = unsupported
    summary["object_types_with_no_matches"] = empty
    if unsupported:
        print(
            "  WARNING: selected types are not discovered by inventory yet, so "
            f"reports will not contain them: {', '.join(unsupported)}"
        )
    if empty:
        print(
            "  WARNING: no objects matched these selected types; check "
            f"catalogs/schemas/regex scope: {', '.join(empty)}"
        )
    stage_rows["INVENTORY"] = _inventory_rows(objects)
    summary["reports"]["inventory"] = reporter.write_stage(
        "INVENTORY", stage_rows["INVENTORY"], summary
    )
    _audit_stage("INVENTORY", stage_rows["INVENTORY"])

print("[5/7] Exporting metadata + migrating package")
if cfg.mode in {"EXPORT", "SYNC"}:
    export_result = ExportService(
        cfg.export_volume_path,
        run_id,
        sql_executor=SparkSqlExecutor(spark),
        fs=dbutils.fs,
    ).run(objects, dry_run=cfg.dry_run)
    summary["exported"] = export_result["exported"]
    summary["export_path"] = export_result["path"]
    export_workspace_path = export_result.get("workspace_path", "")
    summary["export_workspace_path"] = export_workspace_path
    summary["ddl_files"] = export_result.get("ddl_files", 0)
    summary["grant_files"] = export_result.get("grant_files", 0)
    summary["ddl_by_source"] = export_result.get("ddl_by_source", {})
    stage_rows["EXPORT"] = _enrich_object_rows(
        export_result["results"], name_key="full_name"
    )
    summary["reports"]["export"] = reporter.write_stage(
        "EXPORT", stage_rows["EXPORT"], summary
    )
    _audit_stage("EXPORT", stage_rows["EXPORT"])

    if not cfg.dry_run and export_workspace_path:
        migrated_workspace_path = (
            "/Workspace/Users/vivek.ravichandiran@databricks.com/"
            f"UCSync/export_migrated_staging/{run_id}"
        )
        migrate_result = MigrateExportService(
            source_root=export_workspace_path,
            target_root=migrated_workspace_path,
            catalog_mapping=cfg.catalog_mapping,
            mappings=cfg.mappings,
            volume_root=cfg.export_volume_path,
            run_id=run_id,
            fs=dbutils.fs,
        ).run(dry_run=False)
        summary["migrated"] = migrate_result["migrated"]
        summary["migrated_workspace_path"] = migrate_result["target_root"]
        summary["migrated_volume_path"] = migrate_result.get("volume_root", "")
        stage_rows["MIGRATE"] = migrate_result["results"]
        summary["reports"]["migrate"] = reporter.write_stage(
            "MIGRATE", stage_rows["MIGRATE"], summary
        )
        _audit_stage("MIGRATE", stage_rows["MIGRATE"])
        summary["import_package_path"] = migrated_workspace_path

print("[6/7] Importing to target from migrated package")
if cfg.mode in {"IMPORT", "SYNC"}:
    package_path = (
        cfg.import_package_path
        or summary.get("import_package_path")
        or migrated_workspace_path
    )
    summary["import_package_path"] = package_path
    if package_path:
        import_results = PackageImportEngine(
            package_path,
            SparkSqlExecutor(spark),
            dry_run=cfg.dry_run,
            apply_grants=True,
            catalog_mapping=cfg.catalog_mapping,
        ).run()
        stage_rows["IMPORT"] = [result.to_dict() for result in import_results]
    else:
        import_results = ImportEngine(
            target_client, cfg, SparkSqlExecutor(spark)
        ).run(objects)
        stage_rows["IMPORT"] = _enrich_object_rows(
            [result.to_dict() for result in import_results],
            name_key="source_full_name",
        )
    summary["imported"] = sum(
        str(row.get("status", "")).upper() == "SUCCESS"
        for row in stage_rows["IMPORT"]
    )
    summary["failures"] += sum(
        str(row.get("status", "")).upper()
        in {"ERROR", "FAILURE"}
        for row in stage_rows["IMPORT"]
    )
    summary["manual_action_required"] = sum(
        str(row.get("status", "")).upper() == "MANUAL_ACTION_REQUIRED"
        for row in stage_rows["IMPORT"]
    )
    summary["reports"]["import"] = reporter.write_stage(
        "IMPORT", stage_rows["IMPORT"], summary
    )
    summary["reports"]["import_comparison"] = reporter.write_import_comparison(
        stage_rows["IMPORT"], summary
    )
    print(
        "  Import comparison report: "
        f"success={summary['reports']['import_comparison'].get('success')} "
        f"failures={summary['reports']['import_comparison'].get('failures')} "
        f"manual={summary['reports']['import_comparison'].get('manual_action_required')}"
    )
    print(
        "  HTML: "
        + str(summary["reports"]["import_comparison"].get("html") or "")
    )
    print(
        "  XLSX: "
        + str(summary["reports"]["import_comparison"].get("xlsx") or "")
    )
    _audit_stage("IMPORT", stage_rows["IMPORT"])
    for row in stage_rows["IMPORT"]:
        state_rows.append(
            state_row_from_import(
                batch_id=run_id,
                run_id=run_id,
                result=row,
                ran_by=str(ran_by),
                utility_version=__version__,
            )
        )

print("[7/7] Validating")
if cfg.mode in {"COMPARE", "VALIDATE", "SYNC"}:
    comparisons = ValidationService(target_client, cfg).compare(objects)
    stage_rows["VALIDATION"] = _enrich_object_rows(
        [comparison.to_dict() for comparison in comparisons],
        name_key="source_full_name",
    )
    summary["validated"] = len(comparisons)
    summary["failures"] += sum(
        comparison.status not in {"MATCH"} for comparison in comparisons
    )
    try:
        display(__import__("pandas").DataFrame(stage_rows["VALIDATION"]))
    except Exception:
        for row in stage_rows["VALIDATION"][:50]:
            print(row)
    summary["reports"]["validation"] = reporter.write_stage(
        "VALIDATION", stage_rows["VALIDATION"], summary
    )
    _audit_stage("VALIDATION", stage_rows["VALIDATION"])

if summary["failures"]:
    summary["status"] = RunStatus.COMPLETED_WITH_WARNINGS.value

if audit_rows and cfg.audit_table:
    written = AuditService(spark, cfg.audit_table).append(audit_rows)
    summary["audit_rows"] = written
else:
    summary["audit_rows"] = 0

if state_rows and cfg.state_table:
    written_state = SyncStateService(spark, cfg.state_table).upsert(state_rows)
    summary["state_rows"] = written_state
else:
    summary["state_rows"] = 0

summary["reports"]["final"] = reporter.write_final(summary, stage_rows)

print("=" * 58)
print("UC SYNC COMPLETED")
print("=" * 58)
for k, v in summary.items():
    print(f"{k:18}: {v}")
print("=" * 58)

dbutils.notebook.exit(json.dumps(summary))
