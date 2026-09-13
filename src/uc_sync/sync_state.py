"""Sync state table — last successful/failed sync per UC object for incremental runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

# Incremental-sync fingerprint columns (task 1): the three per-object fingerprints
# the next run diffs against. Appended to older state tables in place by
# SyncStateService.ensure_table (same pattern as the audit table).
STATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {full_name} (
  batch_id STRING,
  run_id STRING,
  object_type STRING,
  source_full_name STRING,
  target_full_name STRING,
  source_object_id STRING,
  source_definition_hash STRING,
  ddl_hash STRING,
  governance_hash STRING,
  grants_json STRING,
  source_last_modified_at TIMESTAMP,
  last_sync_status STRING,
  last_sync_at TIMESTAMP,
  last_synced_by STRING,
  ddl_path STRING,
  grants_path STRING,
  error_code STRING,
  error_message STRING,
  detail STRING,
  utility_version STRING,
  updated_at TIMESTAMP
) USING DELTA
"""

STATE_COLUMNS = [
    "batch_id",
    "run_id",
    "object_type",
    "source_full_name",
    "target_full_name",
    "source_object_id",
    "source_definition_hash",
    "ddl_hash",
    "governance_hash",
    "grants_json",
    "source_last_modified_at",
    "last_sync_status",
    "last_sync_at",
    "last_synced_by",
    "ddl_path",
    "grants_path",
    "error_code",
    "error_message",
    "detail",
    "utility_version",
    "updated_at",
]

# (column, SQL type) for columns that may be missing on an older state table.
_STATE_UPGRADE_COLUMNS = [
    ("ddl_hash", "STRING"),
    ("governance_hash", "STRING"),
    ("grants_json", "STRING"),
    ("detail", "STRING"),
]

# --- last_sync_status vocabulary (bug #19) ----------------------------------
# The durable per-object outcome. Fuller than the old SUCCESS/FAILURE/PENDING so the
# roll-up is honest and the table is readable:
#   SUCCESS   - created / applied on target THIS run
#   UNCHANGED - incremental skip: unchanged since a prior present run (zero writes)
#   ADOPTED   - pre-existing on target and left as-is (BYO / create disabled)
#   REPORT_ONLY - a report-only type (never migrated: streaming tables, Tier-A, etc.)
#   SKIPPED   - deliberately not applied (filtered out / out of scope)
#   MANUAL_ACTION_REQUIRED - needs a manual step (e.g. external object w/o mapping)
#   PENDING   - dry-run / not yet applied
#   FAILURE   - attempted and failed
SYNC_STATUS_SUCCESS = "SUCCESS"
SYNC_STATUS_UNCHANGED = "UNCHANGED"
SYNC_STATUS_ADOPTED = "ADOPTED"
SYNC_STATUS_REPORT_ONLY = "REPORT_ONLY"
SYNC_STATUS_SKIPPED = "SKIPPED"
SYNC_STATUS_MANUAL = "MANUAL_ACTION_REQUIRED"
SYNC_STATUS_PENDING = "PENDING"
SYNC_STATUS_FAILURE = "FAILURE"


def _sync_status_for(result: Mapping[str, Any]) -> str:
    """Map an import/export result's ``status`` + ``action`` to a durable state
    status (see the vocabulary above). Kept in one place so the state table, the audit
    table and the report all agree."""
    status = str(result.get("status") or "").upper()
    action = str(result.get("action") or "").upper()
    delta_action = str(result.get("delta_action") or "").upper()
    if action in {"REPORT_ONLY", "SKIP_REPORT_ONLY"} or delta_action == "REPORT_ONLY":
        return SYNC_STATUS_REPORT_ONLY
    if status in {"ERROR", "FAILED", "FAILURE"}:
        return SYNC_STATUS_FAILURE
    if status == "MANUAL_ACTION_REQUIRED" or action == "MANUAL_ACTION_REQUIRED":
        return SYNC_STATUS_MANUAL
    if action == "UNCHANGED" or delta_action == "UNCHANGED" or status == "UNCHANGED":
        return SYNC_STATUS_UNCHANGED
    if action in {"SKIP_EXISTING", "SKIP_CREATE_DISABLED"} or status == "SKIP_EXISTING":
        return SYNC_STATUS_ADOPTED
    if action == "SKIP_FILTERED" or status == "SKIP_FILTERED":
        return SYNC_STATUS_SKIPPED
    if status in {"SUCCESS", "MATCH", "CREATE_OR_SKIP"} or status.startswith("SUCCESS"):
        return SYNC_STATUS_SUCCESS
    if status in {"PENDING", "DRY_RUN", "SKIPPED"}:
        return SYNC_STATUS_PENDING if status != "SKIPPED" else SYNC_STATUS_SKIPPED
    return SYNC_STATUS_FAILURE


def ensure_state_schema_sql(full_name: str) -> Optional[str]:
    parts = full_name.split(".")
    if len(parts) != 3:
        return None
    return f"CREATE SCHEMA IF NOT EXISTS {parts[0]}.{parts[1]}"


def ensure_state_table_sql(full_name: str) -> str:
    return STATE_TABLE_DDL.format(full_name=full_name)


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        epoch = float(value)
        if epoch > 1e12:
            epoch = epoch / 1000.0
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


class SyncStateService:
    def __init__(self, spark: Any, full_name: str):
        self.spark = spark
        self.full_name = full_name

    def ensure_table(self) -> None:
        schema_sql = ensure_state_schema_sql(self.full_name)
        if schema_sql:
            self.spark.sql(schema_sql)
        self.spark.sql(ensure_state_table_sql(self.full_name))
        # Upgrade older state tables in place with the incremental-sync fingerprint
        # columns, so a pre-existing baseline keeps working (missing columns read as
        # NULL → treated as a change → re-applied, which is safe/idempotent).
        try:
            existing = {f.name for f in self.spark.table(self.full_name).schema}
        except Exception:  # noqa: BLE001 - if we cannot read schema, skip upgrade
            return
        missing = [
            f"{name} {sql_type}"
            for name, sql_type in _STATE_UPGRADE_COLUMNS
            if name not in existing
        ]
        if missing:
            self.spark.sql(
                f"ALTER TABLE {self.full_name} ADD COLUMNS ({', '.join(missing)})"
            )

    def load_baseline(self) -> dict[str, dict[str, Any]]:
        """Read the current per-object baseline for an incremental run.

        Returns ``{source_full_name: {object_type, ddl_hash, governance_hash,
        grants, last_sync_status}}`` — one row per object (the MERGE keeps the latest).
        ``grants`` is the parsed explicit-grant set (``{principal: [privileges]}``);
        ``last_sync_status`` is the prior run's outcome so the delta planner can
        re-attempt anything not cleanly applied (bug #18). A missing table / unreadable
        state yields an empty baseline → the run is **full**.
        """
        try:
            self.ensure_table()
            rows = self.spark.sql(
                "SELECT source_full_name, object_type, ddl_hash, governance_hash, "
                f"grants_json, last_sync_status FROM {self.full_name}"
            ).collect()
        except Exception:  # noqa: BLE001 - no baseline yet → full run
            return {}
        baseline: dict[str, dict[str, Any]] = {}
        for row in rows:
            data = row.asDict() if hasattr(row, "asDict") else dict(row)
            name = str(data.get("source_full_name") or "")
            if not name:
                continue
            grants_json = data.get("grants_json")
            try:
                grants = json.loads(grants_json) if grants_json else {}
            except (TypeError, ValueError):
                grants = {}
            baseline[name] = {
                "object_type": str(data.get("object_type") or ""),
                "ddl_hash": str(data.get("ddl_hash") or ""),
                "governance_hash": str(data.get("governance_hash") or ""),
                "grants": grants if isinstance(grants, dict) else {},
                "last_sync_status": str(data.get("last_sync_status") or ""),
            }
        return baseline

    def upsert(self, rows: Iterable[Mapping[str, Any]]) -> int:
        records = [dict(row) for row in rows]
        if not records:
            return 0
        self.ensure_table()
        schema = self.spark.table(self.full_name).schema
        aligned = []
        for record in records:
            values = []
            for field in schema.fields:
                value = record.get(field.name)
                type_name = field.dataType.simpleString().lower()
                if "timestamp" in type_name:
                    value = _as_datetime(value)
                elif type_name in {"string", "varchar", "char"} and value is not None:
                    value = str(value)
                values.append(value)
            aligned.append(tuple(values))
        temp_view = f"_uc_sync_state_batch_{abs(hash(self.full_name)) % 10_000_000}"
        (
            self.spark.createDataFrame(aligned, schema=schema)
            .createOrReplaceTempView(temp_view)
        )
        # MERGE keeps one row per source object for incremental planning.
        self.spark.sql(
            f"""
            MERGE INTO {self.full_name} AS target
            USING {temp_view} AS source
            ON target.source_full_name = source.source_full_name
               AND target.object_type = source.object_type
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
            """
        )
        return len(records)


def state_row_from_import(
    *,
    batch_id: str,
    run_id: str,
    result: Mapping[str, Any],
    ran_by: str,
    utility_version: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    sync_status = _sync_status_for(result)
    # Split the two message channels (bug #19): error_message carries ONLY a real
    # failure; detail carries informational context (skip reason, applied note, DDL).
    err_text = str(result.get("error_message") or "")
    info_text = str(result.get("message") or "")
    if sync_status == SYNC_STATUS_FAILURE:
        error_message = (err_text or info_text)[:4000]
        detail = ""
    else:
        error_message = ""
        detail = (info_text or err_text)[:4000]
    return {
        "batch_id": batch_id,
        "run_id": run_id,
        "object_type": str(result.get("object_type") or ""),
        "source_full_name": str(
            result.get("source_full_name") or result.get("full_name") or ""
        ),
        "target_full_name": str(result.get("target_full_name") or ""),
        "source_object_id": str(result.get("source_object_id") or ""),
        "source_definition_hash": str(
            result.get("source_definition_hash")
            or result.get("definition_hash")
            or ""
        ),
        "ddl_hash": str(result.get("ddl_hash") or ""),
        "governance_hash": str(result.get("governance_hash") or ""),
        "grants_json": (
            result.get("grants_json")
            if isinstance(result.get("grants_json"), str)
            else json.dumps(result.get("grants_json") or {}, sort_keys=True)
        ),
        "source_last_modified_at": _as_datetime(
            result.get("source_last_modified_at") or result.get("last_modified_at")
        ),
        "last_sync_status": sync_status,
        "last_sync_at": now,
        "last_synced_by": ran_by,
        "ddl_path": str(result.get("ddl_path") or ""),
        "grants_path": str(result.get("grants_path") or ""),
        "error_code": str(result.get("error_code") or ""),
        "error_message": error_message,
        "detail": detail,
        "utility_version": utility_version,
        "updated_at": now,
    }
