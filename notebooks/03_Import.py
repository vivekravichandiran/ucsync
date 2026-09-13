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
from uc_sync.config import (
    from_sources, CREATE_TOGGLES, APPLY_TOGGLES, BYO_PREREQUISITE_TOGGLES, _split_csv,
)
from uc_sync.package_import import PackageImportEngine, governance_failures
from uc_sync.location_mapping import (
    load_object_locations_csv,
    load_external_locations_csv,
)
from uc_sync.import_engine import RestSqlExecutor
from uc_sync.auth import local_workspace_auth, direct_workspace_auth
from uc_sync.workspace_client import WorkspaceClient
from uc_sync.volume_copy import (
    VolumeDataCopier, SparkVolumeCopyControl, InMemoryVolumeCopyControl, copy_summary,
)
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
# The single external-storage mapping file (CSV). Column shape auto-selects
# behavior: 2 cols (source_base_path,target_base_path) → BYO (storage credential +
# external location are prerequisites; the utility only prefix-swaps external
# LOCATIONs); 3 cols (+access_connector_id) → the utility creates the storage
# credential + external location. Blank = no base-path swap.
dbutils.widgets.text("external_locations_path", "")
# Optional per-object target locations (CSV: schema,volume,table,location) — an
# exact override that BEATS the external_locations base-path swap for the rare
# object that doesn't follow the base pattern. A schema row sets that MANAGED
# LOCATION; an external volume/table row supplies that object's LOCATION. Blank =
# rely on external_locations (base-path swap) / the catalog root.
dbutils.widgets.text("object_locations_path", "")
# SQL warehouse (this/target workspace) for the ABAC phase AND the view-creation
# phase. CREATE POLICY is rejected at parse on a classic Spark cluster and only
# runs on a SQL warehouse; likewise a CREATE VIEW over a masked/row-filtered base
# table errors on classic Spark but succeeds on a warehouse. REQUIRED when the
# bundle has ABAC policies (otherwise the import fails those closed and drops the
# tables they protect) and strongly recommended whenever the bundle has views over
# masked tables. When unset, views fall back to the Spark executor.
dbutils.widgets.text("import_warehouse_id", "")
# Incremental (delta) sync (task 1): run mode is AUTO-DETECTED — a baseline in
# uc_sync_state (from a prior successful run) → incremental (only deltas applied,
# unchanged objects skipped with zero writes); no baseline → full run + seed the
# baseline. A plain re-run is idempotent; for a genuine reset use DROP SCHEMA …
# CASCADE then recreate (only safe before any data is loaded into the target).
# Streaming tables & materialized views are DLT/SDP-pipeline-managed → report-only
# by default (task 4). Set true to opt into materialized-view migration; streaming
# tables are always report-only.
dbutils.widgets.dropdown("migrate_materialized_views", "false", ["true", "false"])
# Volume data copy (FEAT-4): copy managed + external volume FILES source→target via
# the Files API (securables are always created; the bytes are optional). Default off;
# incremental via a control table (only new/modified files re-copied). >5 GB files are
# reported, not silently dropped.
dbutils.widgets.dropdown("copy_volume_data", "false", ["true", "false"])
# Graded environment preflight (task 9): when enforced (default), a NO-GO (e.g. a
# missing report library on a proxy-restricted cluster) is a red run, never a silent
# degrade. Every run must also produce its report — a report-write failure fails the
# run (bug #4, no opt-out).
dbutils.widgets.dropdown("preflight_enforce", "true", ["true", "false"])
# BYO-by-default: catalog / schema / storage-credential / external-location creation
# defaults OFF (they are customer prerequisites); all other create + apply toggles
# default ON. A 3-column external_locations.csv turns SC/EL creation back on.
for _t in (*CREATE_TOGGLES, *APPLY_TOGGLES):
    dbutils.widgets.dropdown(
        _t, "false" if _t in BYO_PREREQUISITE_TOGGLES else "true", ["true", "false"]
    )
dbutils.widgets.dropdown("dry_run", "false", ["true", "false"])
# Source-workspace auth (direct mode) — needed only to read source volume FILES when
# copy_volume_data is on (volume bytes are not in the bundle). Blank in airgap / when
# the toggle is off.
dbutils.widgets.text("source_workspace_url", "")
dbutils.widgets.text("source_client_id", "")       # plaintext (never a secret)
dbutils.widgets.text("source_client_secret", "")   # plaintext secret (option 1)
dbutils.widgets.text("source_secret_scope", "")    # secret scope (option 2)
dbutils.widgets.text("source_secret_key", "")      # secret key   (option 2)

# COMMAND ----------

cfg = from_sources({
    "stage": "IMPORT",
    "output_volume_path": dbutils.widgets.get("output_volume_path"),
    "ops_catalog": dbutils.widgets.get("ops_catalog"),
    "ops_schema": dbutils.widgets.get("ops_schema"),
    "dry_run": dbutils.widgets.get("dry_run"),
    "catalog_mapping_json": dbutils.widgets.get("catalog_mapping_json"),
    "import_warehouse_id": dbutils.widgets.get("import_warehouse_id"),
    "external_locations_path": dbutils.widgets.get("external_locations_path"),
    "migrate_materialized_views": dbutils.widgets.get("migrate_materialized_views"),
    "copy_volume_data": dbutils.widgets.get("copy_volume_data"),
    "preflight_enforce": dbutils.widgets.get("preflight_enforce"),
    "source_workspace_url": dbutils.widgets.get("source_workspace_url"),
    "source_client_id": dbutils.widgets.get("source_client_id"),
    "source_client_secret": dbutils.widgets.get("source_client_secret"),
    "source_secret_scope": dbutils.widgets.get("source_secret_scope"),
    "source_secret_key": dbutils.widgets.get("source_secret_key"),
    **{t: dbutils.widgets.get(t) for t in (*CREATE_TOGGLES, *APPLY_TOGGLES)},
})

# Graded environment preflight — libraries importable at the right versions (the
# openpyxl-missing silent-no-report failure mode), before any work is done.
from uc_sync.preflight import run_preflight, enforce_preflight
enforce_preflight(run_preflight(check_libs=True), enforce=cfg.preflight_enforce)
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
# The single external-storage mapping (base-path swap; task 2). Its column shape
# already set the create_storage_credentials/create_external_locations toggles in
# from_sources (2-col BYO forces them off); here it drives the import-time swap.
_external_locations_path = _local(dbutils.widgets.get("external_locations_path").strip())
external_locations = (
    load_external_locations_csv(_external_locations_path)
    if _external_locations_path else None
)

# COMMAND ----------

wc = WorkspaceClient(local_workspace_auth(dbutils))
# FEAT-1: run ALL replay (tables, functions, masks, row filters, views, materialized
# views, ABAC policies, tags, grants) through ONE serverless SQL warehouse — nothing
# on the job cluster's Spark session. One always-current runtime (fixes GEOMETRY /
# bug #9 with no version fiddling) and one auth path (removes the class behind the
# one-off view-403 / bug #10), with consistent, explainable behavior. The same
# executor is the main DDL executor AND the ABAC/view executor. Inventory reads
# stay as-is. import_warehouse_id is therefore required for the import stage.
if not cfg.import_warehouse_id:
    raise ValueError(
        "import_warehouse_id is required — FEAT-1 runs all import replay on a single "
        "serverless SQL warehouse (no Spark-cluster DDL). Set import_warehouse_id."
    )
warehouse_executor = RestSqlExecutor(wc, cfg.import_warehouse_id)
abac_executor = warehouse_executor

# Incremental (delta) sync: read the prior-run baseline from uc_sync_state. When a
# baseline exists the run is incremental — only deltas are applied and unchanged
# objects are skipped entirely; otherwise it is a full run that seeds the baseline.
# Best-effort: no readable state → full run.
prior_state = {}
try:
    if cfg.state_table:
        prior_state = SyncStateService(spark, cfg.state_table).load_baseline()
        print(f"delta: read baseline of {len(prior_state)} objects from {cfg.state_table}")
except Exception as _exc:  # noqa: BLE001 - no baseline → full run
    print(f"delta: baseline read skipped ({_exc!r}) — running full")
    prior_state = {}

# The run-as SPN = the identity this import runs as. The utility never re-grants to
# it (it already holds its catalog-scoped grant; its source ACLs are irrelevant to
# the target), so it is excluded as a grantee when replicating ACLs (task 8).
try:
    _run_as_spn = spark.sql("SELECT current_user()").collect()[0][0]
except Exception:  # noqa: BLE001
    _run_as_spn = ""

engine = PackageImportEngine(
    migrated, warehouse_executor, dry_run=cfg.dry_run, toggles=toggles,
    workspace_client=wc,
    catalog_mapping=cfg.catalog_mapping,
    select_tables=_split_csv(dbutils.widgets.get("filter_tables")),
    object_locations=object_locations,
    external_locations=external_locations,
    prior_state=prior_state,
    migrate_materialized_views=cfg.migrate_materialized_views,
    run_as_spn=_run_as_spn,
    abac_sql_executor=abac_executor,
)
results = engine.run()
_mode = "incremental" if (engine.delta_plan and engine.delta_plan.incremental) else "full"
print(f"delta: run mode = {_mode}"
      + (f"; {engine.delta_plan.unchanged_count()} unchanged (skipped)"
         if engine.delta_plan else ""))

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
    delta_rows = engine.delta_plan.delta_rows() if engine.delta_plan else []
    if engine.delta_plan:
        delta_rows = delta_rows + [
            {"action": "UNCHANGED_COUNT",
             "detail": engine.delta_plan.unchanged_count()}
        ]
    build_report_from_file(
        inv, report_path, run_id=run_id, stage="IMPORT",
        export_results=export_results,
        import_results=[r.to_dict() for r in results],
        delta_rows=delta_rows,
        workspace_url=getattr(wc.auth, "host", ""),
    )
    print(f"report: {report_path}")
except Exception as _exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    # Bug #4 — every run must produce its report; there is no opt-out. A failure to
    # write the report always fails the run.
    raise RuntimeError(
        "report generation failed — a run must not complete without its report. "
        f"Root cause: {_exc!r}"
    )

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
            # uc_sync_state = ONE row per source OBJECT (the incremental baseline).
            # Governed-tag ALTER ops are attributes of an object already counted (its
            # table/catalog/schema), not objects — including them would collide on the
            # per-object MERGE key and clobber the object's fingerprints. ABAC policies
            # ARE objects (own row). So exclude only the tag-op results (policies_path
            # set AND object_type != ABAC_POLICY).
            state_dicts = [
                rd for rd in result_dicts
                if not (rd.get("policies_path") and rd.get("object_type") != "ABAC_POLICY")
            ]
            SyncStateService(spark, cfg.state_table).upsert(
                state_row_from_import(
                    batch_id=batch_id, run_id=run_id, result=rd,
                    ran_by=ran_by, utility_version=__version__,
                )
                for rd in state_dicts
            )
            print(f"state: upserted {len(state_dicts)} object rows into {cfg.state_table}")
except Exception as _exc:  # noqa: BLE001 - ops tables are best-effort
    import traceback
    print(f"ops audit/state write skipped: {_exc!r}")
    traceback.print_exc()

# Volume data copy (FEAT-4): copy managed + external volume FILES source→target via
# the Files API — the bundle carries the volume securables, not their bytes. Toggle
# default off; incremental via a control table (only new/modified files re-copied);
# a >5 GB file is reported, not silently dropped. Best-effort: a copy failure is
# recorded, never fails the governance migration itself.
if cfg.copy_volume_data:
    try:
        with open(f"{migrated}/inventory/objects.json") as _fh:
            _inv_rows = json.load(_fh)
        _volumes = [
            r for r in _inv_rows
            if str(r.get("object_type")) in ("VOLUME", "EXTERNAL_VOLUME")
        ]
        if not cfg.source_workspace_url:
            print("[volume-copy] copy_volume_data=true but no source_workspace_url — "
                  "cannot read source files; skipping (set source auth to enable).")
        elif not _volumes:
            print("[volume-copy] no volumes in scope to copy.")
        else:
            _secret = cfg.source_client_secret
            if not _secret and cfg.source_secret_scope and cfg.source_secret_key:
                _secret = dbutils.secrets.get(
                    scope=cfg.source_secret_scope, key=cfg.source_secret_key)
            _src_client = WorkspaceClient(direct_workspace_auth(
                cfg.source_workspace_url, cfg.source_client_id, _secret))
            try:
                _control = SparkVolumeCopyControl(
                    spark, f"{cfg.ops_catalog}.{cfg.ops_schema}.uc_sync_volume_files")
            except Exception as _cx:  # noqa: BLE001
                print(f"[volume-copy] control table unavailable ({_cx!r}) — in-memory")
                _control = InMemoryVolumeCopyControl()
            _copier = VolumeDataCopier(_src_client, wc, control=_control)
            _all_copy = []
            for _v in _volumes:
                _parts = str(_v.get("full_name") or "").split(".")
                if len(_parts) != 3:
                    continue
                _cat, _sch, _name = _parts
                _tgt_cat = cfg.catalog_mapping.get(_cat, _cat)
                _all_copy.extend(_copier.copy_volume(
                    _v["full_name"],
                    f"/Volumes/{_cat}/{_sch}/{_name}",
                    f"/Volumes/{_tgt_cat}/{_sch}/{_name}",
                ))
            if isinstance(_control, SparkVolumeCopyControl):
                _control.flush()
            print(f"[volume-copy] {copy_summary(_all_copy)}")
            for _r in _all_copy:
                if _r.status in ("FAILED", "SKIPPED_TOO_LARGE"):
                    print(f"  [{_r.status}] {_r.source_path}: {_r.message[:160]}")
    except Exception as _exc:  # noqa: BLE001 - volume data copy is best-effort
        import traceback
        print(f"[volume-copy] skipped: {_exc!r}")
        traceback.print_exc()

summary = {}
for r in results:
    summary[r.status] = summary.get(r.status, 0) + 1
print(json.dumps({"run_id": run_id, "by_status": summary}, indent=2))
for r in results:
    if r.status not in ("SUCCESS", "SKIP_EXISTING", "PENDING"):
        print(f"  [{r.status}] {r.object_type} {r.target_full_name}: {str(r.message)[:200]}")

# Task 10 — a governance failure is NEVER a silently green run. The report and the
# audit/state tables were already written above (so the run is fully accounted for),
# and now the job HARD-FAILS if any object could not be fully protected: a fresh
# table shell was dropped fail-closed, or a pre-existing (possibly deep-cloned) table
# was flagged PROTECTION_FAILED (never dropped — that would destroy data). Ops sees a
# red job and fixes the governance prerequisite, then re-runs.
gov_failed = governance_failures(results)
exit_payload = {"run_id": run_id, "by_status": summary,
                "governance_failed": len(gov_failed)}
if gov_failed:
    print(f"\n[import] {len(gov_failed)} GOVERNANCE FAILURE(S) — job will exit non-zero:")
    for r in gov_failed:
        print(f"  [{r.error_code}] {r.object_type} {r.target_full_name}: "
              f"{str(r.message)[:200]}")
    raise RuntimeError(
        f"UC Sync import completed with {len(gov_failed)} governance failure(s) "
        "(PROTECTION_FAILED / GOVERNANCE_FAILED / ABAC_WAREHOUSE_REQUIRED). The "
        "report and audit/state tables were written; a fresh table shell that could "
        "not be protected was dropped, and any pre-existing table was flagged (never "
        f"dropped). Fix the governance prerequisites and re-run. run_id={run_id}, "
        f"by_status={summary}"
    )
dbutils.notebook.exit(json.dumps(exit_payload))
