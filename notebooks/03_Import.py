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
    VolumeDataCopier, WarehouseVolumeCopyControl, InMemoryVolumeCopyControl, copy_summary,
)
from uc_sync.audit import AuditService, stage_audit_row
from uc_sync.sync_state import SyncStateService, state_row_from_import
from uc_sync.logging_util import (
    configure_logging, get_log, set_context, register_secret, get_captured_log,
)

# COMMAND ----------

# Widgets carry a numbered `label` (backlog item 10) so Databricks renders them
# grouped + ordered (it sorts by label). The widget NAME/key is never changed.
# --- 1. Source auth (direct mode; needed only to read source volume FILES when
#        copy_volume_data is on — volume bytes are not in the bundle). ---
dbutils.widgets.text("source_workspace_url", "", "1b. Source · Workspace URL (blank = current)")
dbutils.widgets.text("source_client_id", "", "1c. Source · SP client id (plaintext)")
dbutils.widgets.text("source_client_secret", "", "1d. Source · SP secret (plaintext, option 1)")
dbutils.widgets.text("source_secret_scope", "", "1e. Source · secret scope (option 2)")
dbutils.widgets.text("source_secret_key", "", "1f. Source · secret key (option 2)")
# --- 2. Scope + bundle location ---
dbutils.widgets.text("output_volume_path", "", "2d. Scope · Output volume path")
dbutils.widgets.text("ops_catalog", "", "2e. Scope · Ops catalog")
dbutils.widgets.text("ops_schema", "", "2f. Scope · Ops schema")
# The single external-storage mapping file (CSV). 2 cols = BYO prefix-swap; 3 cols
# (+access connector) = create SC/EL. Blank = no base-path swap.
dbutils.widgets.text("external_locations_path", "", "2g. Scope · External locations file")
# Optional import TABLE filter: import only a subset of tables from the bundle. Blank
# = import every table. Accepts catalog.schema.table or the bare table name.
dbutils.widgets.text("filter_tables", "", "2h. Scope · Import table filter (allowlist)")
# Optional catalog rename: JSON {"source_catalog":"target_catalog"} (blank = keep).
dbutils.widgets.text("catalog_mapping_json", "", "2i. Scope · Catalog rename JSON")
# Optional per-object target locations (CSV: schema,volume,table,location) — an exact
# override that BEATS the external_locations base-path swap for the rare object.
dbutils.widgets.text("object_locations_path", "", "2j. Scope · Object locations file")
# --- 4. Apply / behavior toggles (materialized views + volume data copy) ---
# Streaming tables & materialized views are DLT/SDP-managed → report-only by default;
# set true to migrate materialized views (streaming tables stay report-only).
dbutils.widgets.dropdown("migrate_materialized_views", "false", ["true", "false"], "4d. Apply · Migrate materialized views")
# Volume data copy (FEAT-4): copy managed + external volume FILES source→target via the
# Files API. Default off; incremental via a control table; >5 GB reported.
dbutils.widgets.dropdown("copy_volume_data", "false", ["true", "false"], "4e. Apply · Copy volume data")
# Retry-failed-only (backlog item 4): replay ONLY the prior run's failed objects (read
# from uc_sync_state) + their parents, skipping everything else. Same run_id/bundle as a
# normal import; requires a prior run that seeded state. Default off.
dbutils.widgets.dropdown("retry_failed_only", "false", ["true", "false"], "4f. Apply · Retry failed only")
# --- 5. Warehouse ---
# SQL warehouse (target) for the ABAC phase AND the view-creation phase. REQUIRED when
# the bundle has ABAC policies; strongly recommended for views over masked tables.
dbutils.widgets.text("import_warehouse_id", "", "5b. Warehouse · Import (ABAC + views)")
# --- 8. Run controls ---
dbutils.widgets.text("run_id", "", "8c. Run · Run id (from Export)")
dbutils.widgets.dropdown("dry_run", "false", ["true", "false"], "8d. Run · Dry run")
# Graded environment preflight (task 9): enforced by default. Every run also always
# produces its report — a report-write failure fails the run (bug #4, no opt-out).
dbutils.widgets.dropdown("preflight_enforce", "true", ["true", "false"], "8a. Run · Preflight enforce")
# Structured logging verbosity (backlog item 9). INFO by default; DEBUG opt-in.
dbutils.widgets.dropdown("log_level", "INFO", ["INFO", "DEBUG", "WARNING", "ERROR"], "8b. Run · Log level")
# Within-level import parallelism (backlog item 3). 1 = sequential (safe fallback).
# Keep ≤ the import warehouse's max concurrent queries.
dbutils.widgets.text("parallel_threads", "4", "8g. Run · Parallel threads")
# --- 3. Create toggles + 4a-c apply toggles (BYO-by-default: catalog / schema / SC /
#        EL creation defaults OFF; contents + governance default ON). A 3-column
#        external_locations.csv turns SC/EL creation back on. ---
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
    "retry_failed_only": dbutils.widgets.get("retry_failed_only"),
    "parallel_threads": dbutils.widgets.get("parallel_threads"),
    "preflight_enforce": dbutils.widgets.get("preflight_enforce"),
    "source_workspace_url": dbutils.widgets.get("source_workspace_url"),
    "source_client_id": dbutils.widgets.get("source_client_id"),
    "source_client_secret": dbutils.widgets.get("source_client_secret"),
    "source_secret_scope": dbutils.widgets.get("source_secret_scope"),
    "source_secret_key": dbutils.widgets.get("source_secret_key"),
    **{t: dbutils.widgets.get(t) for t in (*CREATE_TOGGLES, *APPLY_TOGGLES)},
})

# Structured logging: configure up front so preflight + every phase is captured.
configure_logging(stage="IMPORT", level=dbutils.widgets.get("log_level") or "INFO")
log = get_log(__name__)
log.info("stage IMPORT start (dry_run=%s)", cfg.dry_run)

# Graded environment preflight — libraries importable at the right versions (the
# openpyxl-missing silent-no-report failure mode), before any work is done.
from uc_sync.preflight import run_preflight, enforce_preflight
enforce_preflight(run_preflight(check_libs=True), enforce=cfg.preflight_enforce)
run_id = dbutils.widgets.get("run_id").strip()
if not run_id:
    raise ValueError("run_id from the Export stage is required")
set_context(run_id=run_id)
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
        log.info("delta: read baseline of %d objects from %s", len(prior_state), cfg.state_table)
except Exception as _exc:  # noqa: BLE001 - no baseline → full run
    log.warning("delta: baseline read skipped (%r) — running full", _exc)
    prior_state = {}

# The run-as SPN = the identity this import runs as. The utility never re-grants to
# it (it already holds its catalog-scoped grant; its source ACLs are irrelevant to
# the target), so it is excluded as a grantee when replicating ACLs (task 8).
try:
    _run_as_spn = spark.sql("SELECT current_user()").collect()[0][0]
except Exception:  # noqa: BLE001
    _run_as_spn = ""

# Retry-failed-only (backlog item 4): replay ONLY the prior run's failed set (read from
# uc_sync_state's per-facet *_status) + required parents. Requires a state baseline (a
# run that never seeded state has nothing to retry). Same run_id / bundle as a normal
# import — no re-export.
retry_failed_set = set()
if cfg.retry_failed_only:
    try:
        retry_failed_set = SyncStateService(spark, cfg.state_table).failed_object_names()
    except Exception as _exc:  # noqa: BLE001
        log.warning("retry-failed-only: could not read failed set (%r)", _exc)
    log.info("retry-failed-only: %d prior-failed object(s) to replay", len(retry_failed_set))
    if not retry_failed_set:
        log.warning("retry-failed-only: no failed objects in %s — nothing to replay "
                    "(a prior run must have seeded state)", cfg.state_table)

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
    retry_failed_only=cfg.retry_failed_only,
    retry_failed_set=retry_failed_set,
    parallel_threads=cfg.parallel_threads,
)
results = engine.run()
_mode = "incremental" if (engine.delta_plan and engine.delta_plan.incremental) else "full"
log.info("delta: run mode = %s%s", _mode,
         (f"; {engine.delta_plan.unchanged_count()} unchanged (skipped)"
          if engine.delta_plan else ""))

# Operations tables under {ops_catalog}.{ops_schema} on THIS (target) workspace:
#   uc_sync_audit — one IMPORT row per object (append-only history).
#   uc_sync_state — one row per source object (MERGE upsert), the per-object
#     last-sync record that a future incremental run would diff against.
# Best-effort: audit/state logging must never fail the migration itself.
outstanding_rows = None  # cumulative failures from state (Part E) → report Outstanding sheet
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
            log.info("audit: wrote %d IMPORT rows to %s", len(result_dicts), cfg.audit_table)
        if cfg.state_table:
            batch_id = str(uuid.uuid4())
            # uc_sync_state = ONE row per source OBJECT (the incremental baseline).
            # Governed-tag ALTER ops are attributes of an object already counted (its
            # table/catalog/schema), not objects — including them would collide on the
            # per-object MERGE key and clobber the object's fingerprints. ABAC policies
            # ARE objects (own row). So exclude only the tag-op results (policies_path
            # set AND object_type != ABAC_POLICY).
            # COLUMN results (a schema-evolution ADD COLUMN) are attributes of a table
            # already counted (its own row + ddl_hash), not standalone objects — writing
            # them would leave orphan rows that read as "deleted in source" on the next
            # run (columns are never inventoried as objects). Exclude them, same as tag
            # ops.
            state_dicts = [
                rd for rd in result_dicts
                if rd.get("object_type") != "COLUMN"
                and not (rd.get("policies_path") and rd.get("object_type") != "ABAC_POLICY")
            ]
            _state_svc = SyncStateService(spark, cfg.state_table)
            _state_svc.upsert(
                state_row_from_import(
                    batch_id=batch_id, run_id=run_id, result=rd,
                    ran_by=ran_by, utility_version=__version__,
                    connectivity_mode=cfg.connectivity_mode,
                )
                for rd in state_dicts
            )
            log.info("state: upserted %d object rows into %s", len(state_dicts), cfg.state_table)
            # Outstanding (Part E): cumulative still-broken objects from state across ALL
            # runs, read AFTER this run's upsert so the report's Outstanding sheet is the
            # authoritative "everything still broken" view. Best-effort.
            try:
                outstanding_rows = _state_svc.outstanding_rows()
                log.info("state: %d outstanding (cumulative failures)", len(outstanding_rows))
            except Exception as _ox:  # noqa: BLE001
                log.warning("outstanding read skipped: %r", _ox)
except Exception as _exc:  # noqa: BLE001 - ops tables are best-effort
    log.warning("ops audit/state write skipped: %r", _exc, exc_info=True)

# Volume data copy (FEAT-4): copy managed + external volume FILES source→target via
# the Files API — the bundle carries the volume securables, not their bytes. Toggle
# default off; incremental via a control table (only new/modified files re-copied);
# a >5 GB file is reported, not silently dropped. Best-effort: a copy failure is
# recorded, never fails the governance migration itself.
vol_copy_status = "disabled"
volume_copy_report_rows = None  # FEAT-4 copy rows for the report (bug #17); None → sheet omitted
if cfg.copy_volume_data:
    vol_copy_status = "started"
    try:
        with open(f"{migrated}/inventory/objects.json") as _fh:
            _inv_rows = json.load(_fh)
        _volumes = [
            r for r in _inv_rows
            if str(r.get("object_type")) in ("VOLUME", "EXTERNAL_VOLUME")
        ]
        if not cfg.source_workspace_url:
            vol_copy_status = "skipped: no source_workspace_url (set source auth)"
            log.warning("volume-copy %s", vol_copy_status)
        elif not _volumes:
            vol_copy_status = "no volumes in scope"
            log.info("volume-copy: %s", vol_copy_status)
        else:
            _secret = cfg.source_client_secret
            if not _secret and cfg.source_secret_scope and cfg.source_secret_key:
                _secret = dbutils.secrets.get(
                    scope=cfg.source_secret_scope, key=cfg.source_secret_key)
            register_secret(_secret)  # scrub the source SP secret from all log lines
            _src_client = WorkspaceClient(direct_workspace_auth(
                cfg.source_workspace_url, cfg.source_client_id, _secret))
            # FEAT-1: persist the control table through the import WAREHOUSE executor
            # (reliable), not the job-cluster Spark session. A control failure must not
            # silently fall back — surface it (a fallback would re-copy every file).
            # The volume-copy control table lives in the same ops schema as the
            # audit/state tables; derive its name from cfg.state_table
            # ({ops_catalog}.{ops_schema}.uc_sync_state) — SyncConfig exposes the full
            # table names, not ops_catalog/ops_schema separately.
            _ctrl_table = (
                cfg.state_table.rsplit(".", 1)[0] + ".uc_sync_volume_files"
                if cfg.state_table else ""
            )
            _control_kind = "warehouse"
            try:
                if not _ctrl_table:
                    raise ValueError("no ops state_table configured")
                _control = WarehouseVolumeCopyControl(warehouse_executor, _ctrl_table)
            except Exception as _cx:  # noqa: BLE001
                _control_kind = f"in-memory (control table unavailable: {_cx!r})"
                log.warning("volume-copy %s", _control_kind)
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
            volume_copy_report_rows = [_r.to_dict() for _r in _all_copy]
            _flush_status = "n/a"
            if isinstance(_control, WarehouseVolumeCopyControl):
                try:
                    _control.flush()
                    _flush_status = "flushed"
                except Exception as _fx:  # noqa: BLE001
                    _flush_status = f"flush FAILED: {_fx!r}"
                    log.error("volume-copy %s", _flush_status)
            vol_copy_status = (
                f"control={_control_kind}; {copy_summary(_all_copy)}; flush={_flush_status}"
            )
            log.info("volume-copy: %s", vol_copy_status)
            for _r in _all_copy:
                if _r.status in ("FAILED", "SKIPPED_TOO_LARGE"):
                    log.warning("volume-copy [%s] %s: %s", _r.status, _r.source_path, _r.message[:160])
    except Exception as _exc:  # noqa: BLE001 - volume data copy is best-effort
        vol_copy_status = f"skipped: {_exc!r}"
        log.error("volume-copy %s", vol_copy_status, exc_info=True)

# Clean migration report (spine + governance sheets) under reports/. Written AFTER the
# volume-data copy so the report includes the FEAT-4 copy results (bug #17). The import
# report carries the export_status forward from stage 02 (export_results.json) alongside
# this stage's import_status, so it is the cumulative base report.
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
        volume_copy_results=volume_copy_report_rows,
        outstanding=outstanding_rows,
        workspace_url=getattr(wc.auth, "host", ""),
    )
    log.info("report: %s", report_path)
except Exception as _exc:  # noqa: BLE001
    log.error("report generation failed: %r", _exc, exc_info=True)
    # Bug #4 — every run must produce its report; there is no opt-out. A failure to
    # write the report always fails the run.
    raise RuntimeError(
        "report generation failed — a run must not complete without its report. "
        f"Root cause: {_exc!r}"
    )

summary = {}
for r in results:
    summary[r.status] = summary.get(r.status, 0) + 1
log.info("import by_status: %s", json.dumps(summary))
for r in results:
    if r.status not in ("SUCCESS", "SKIP_EXISTING", "PENDING", "UNCHANGED"):
        log.warning("[%s] %s %s: %s", r.status, r.object_type,
                    r.target_full_name, str(r.message)[:200])

# Persist the full run log alongside the report so a handed-over artifact includes it.
try:
    with open(f"{_local(base)}/reports/import.log", "w") as fh:
        fh.write(get_captured_log())
except Exception as _exc:  # noqa: BLE001 - log persistence is best-effort
    log.warning("run-log persistence skipped: %r", _exc)

# Task 10 — a governance failure is NEVER a silently green run. The report and the
# audit/state tables were already written above (so the run is fully accounted for),
# and now the job HARD-FAILS if any object could not be fully protected: a fresh
# table shell was dropped fail-closed, or a pre-existing (possibly deep-cloned) table
# was flagged PROTECTION_FAILED (never dropped — that would destroy data). Ops sees a
# red job and fixes the governance prerequisite, then re-runs.
gov_failed = governance_failures(results)
exit_payload = {"run_id": run_id, "by_status": summary,
                "governance_failed": len(gov_failed),
                "volume_copy": vol_copy_status}
if gov_failed:
    log.error("%d GOVERNANCE FAILURE(S) — job will exit non-zero:", len(gov_failed))
    for r in gov_failed:
        log.error("  [%s] %s %s: %s", r.error_code, r.object_type,
                  r.target_full_name, str(r.message)[:200])
    raise RuntimeError(
        f"UC Sync import completed with {len(gov_failed)} governance failure(s) "
        "(PROTECTION_FAILED / GOVERNANCE_FAILED / ABAC_WAREHOUSE_REQUIRED). The "
        "report and audit/state tables were written; a fresh table shell that could "
        "not be protected was dropped, and any pre-existing table was flagged (never "
        f"dropped). Fix the governance prerequisites and re-run. run_id={run_id}, "
        f"by_status={summary}"
    )
dbutils.notebook.exit(json.dumps(exit_payload))
