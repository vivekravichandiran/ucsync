"""Sync state table — last successful/failed sync per UC object for incremental runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from uc_sync import vocab

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
  source_definition_hash STRING COMMENT 'Whole-object canonical hash (export-level integrity): the entire captured object. Any change to the object changes it.',
  ddl_hash STRING COMMENT 'Structural DDL hash only — columns/types/view text/function body/storage AND inline classic masks & row filters (they ride in CREATE); tags excluded. Drives CHANGED/REPLACED.',
  governance_hash STRING COMMENT 'TAGS ONLY — object-level + per-column governed tags. Drives GOVERNANCE_UPDATED even when the DDL is unchanged.',
  grants_json STRING,
  source_last_modified_at TIMESTAMP,
  last_action STRING COMMENT 'Unified status vocabulary (uc_sync.vocab), shared verbatim with the report: created/created_with_warning/updated/adopted/skipped/skipped_create_disabled/not_selected/skipped_no_object/deleted_in_source/manual/failed.',
  last_sync_at TIMESTAMP,
  last_synced_by STRING,
  ddl_path STRING,
  grants_path STRING,
  error_code STRING,
  error_message STRING,
  detail STRING,
  utility_version STRING,
  first_seen TIMESTAMP COMMENT 'When this object was FIRST migrated (set once on insert, preserved across updates).',
  connectivity_mode STRING COMMENT 'direct vs airgap for the run that last touched this object.',
  failure_category STRING COMMENT 'Coarse failure taxonomy (GOVERNANCE / STORAGE / SCHEMA_EVOLUTION / DEPENDENCY_UNRESOLVED / API_ERROR / OTHER) — set only when last_action=failed.',
  last_error_raw STRING COMMENT 'Full untruncated error text for the last failure (error_message is truncated for display).',
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
    "last_action",
    "last_sync_at",
    "last_synced_by",
    "ddl_path",
    "grants_path",
    "error_code",
    "error_message",
    "detail",
    "utility_version",
    "first_seen",
    "connectivity_mode",
    "failure_category",
    "last_error_raw",
    "updated_at",
]

# Columns whose value must be PRESERVED on a MERGE update (set once, never overwritten).
_PRESERVE_ON_UPDATE = {"first_seen"}

# (column, SQL type) for columns that may be missing on an older state table. The
# last_action rename lands here too: an older table has last_sync_status but not
# last_action, so ADD it (ensure_table then backfills it from the legacy column). The
# Part-D parity columns (first_seen / connectivity_mode / failure_category /
# last_error_raw) are appended in place the same way.
_STATE_UPGRADE_COLUMNS = [
    ("ddl_hash", "STRING"),
    ("governance_hash", "STRING"),
    ("grants_json", "STRING"),
    ("detail", "STRING"),
    ("last_action", "STRING"),
    ("first_seen", "TIMESTAMP"),
    ("connectivity_mode", "STRING"),
    ("failure_category", "STRING"),
    ("last_error_raw", "STRING"),
]

# error_code → coarse failure_category (Part D). A fallback keeps unknown codes as OTHER.
_FAILURE_CATEGORY = {
    "PROTECTION_FAILED": "GOVERNANCE",
    "GOVERNANCE_FAILED": "GOVERNANCE",
    "GOVERNANCE_PREREQ_MISSING": "DEPENDENCY_UNRESOLVED",
    "ABAC_WAREHOUSE_REQUIRED": "GOVERNANCE",
    "GOVERNED_TAG_FAILED": "GOVERNANCE",
    "SCHEMA_EVOLUTION_FAILED": "SCHEMA_EVOLUTION",
    "EXTERNAL_CREATE_FAILED": "STORAGE",
    "LOCATION_OVERLAP": "STORAGE",
    "EXTERNAL_LOCATION_MISSING": "STORAGE",
}


def _failure_category_for(error_code: str, last_action: str) -> str:
    """Coarse failure bucket for a state row — only meaningful for a failure."""
    if last_action != vocab.FAILED:
        return ""
    code = str(error_code or "").upper()
    return _FAILURE_CATEGORY.get(code, "API_ERROR" if code else "OTHER")


def _last_action_for(result: Mapping[str, Any]) -> str:
    """The durable per-object ``last_action`` for a result — the ONE shared vocabulary
    (``uc_sync.vocab``) the report renders too, so the state table and the workbook
    never disagree for the same outcome (the "constant naming + vocab" unification;
    replaces the old, separate SUCCESS/UNCHANGED/ADOPTED/… state vocabulary)."""
    return vocab.status_key(dict(result))


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
        # last_action rename backfill: an older table carried last_sync_status; map its
        # legacy values into the new last_action column (once, where still NULL) so a
        # pre-existing baseline reads in the unified vocabulary. The legacy column is
        # left in place (harmless) — never read once last_action is populated.
        if "last_action" in missing and "last_sync_status" in existing:
            cases = " ".join(
                f"WHEN '{legacy}' THEN '{action}'"
                for legacy, action in vocab._LEGACY_STATUS_TO_ACTION.items()
            )
            try:
                self.spark.sql(
                    f"UPDATE {self.full_name} SET last_action = "
                    f"CASE last_sync_status {cases} ELSE lower(last_sync_status) END "
                    "WHERE last_action IS NULL"
                )
            except Exception:  # noqa: BLE001 - backfill is best-effort; NULL reads clean
                pass

    def load_baseline(self) -> dict[str, dict[str, Any]]:
        """Read the current per-object baseline for an incremental run.

        Returns ``{source_full_name: {object_type, ddl_hash, governance_hash,
        grants, last_action}}`` — one row per object (the MERGE keeps the latest).
        ``grants`` is the parsed explicit-grant set (``{principal: [privileges]}``);
        ``last_action`` is the prior run's outcome (the unified vocabulary) so the delta
        planner can re-attempt anything not cleanly applied (bug #18). A missing table /
        unreadable state yields an empty baseline → the run is **full**.
        """
        try:
            self.ensure_table()
            rows = self.spark.sql(
                "SELECT source_full_name, object_type, ddl_hash, governance_hash, "
                f"grants_json, last_action FROM {self.full_name}"
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
                "last_action": str(data.get("last_action") or ""),
            }
        return baseline

    def outstanding_rows(self) -> list[dict[str, Any]]:
        """Cumulative still-broken objects across ALL runs — every state row whose
        ``last_action`` is in ``vocab.OUTSTANDING_ACTIONS`` (failures). Feeds the report's
        Outstanding sheet (Part E); read AFTER this run's state upsert so it reflects the
        authoritative current picture. Empty on any read error (best-effort)."""
        actions = sorted(vocab.OUTSTANDING_ACTIONS)
        in_list = ", ".join(f"'{a}'" for a in actions)
        try:
            self.ensure_table()
            rows = self.spark.sql(
                "SELECT source_full_name, object_type, last_action, error_code, "
                "error_message, run_id, last_sync_at "
                f"FROM {self.full_name} WHERE last_action IN ({in_list})"
            ).collect()
        except Exception:  # noqa: BLE001 - no state / unreadable → nothing outstanding
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            data = row.asDict() if hasattr(row, "asDict") else dict(row)
            out.append({
                "source_full_name": str(data.get("source_full_name") or ""),
                "object_type": str(data.get("object_type") or ""),
                "last_action": str(data.get("last_action") or ""),
                "error_code": str(data.get("error_code") or ""),
                "error_message": str(data.get("error_message") or ""),
                "run_id": str(data.get("run_id") or ""),
                "last_sync_at": str(data.get("last_sync_at") or ""),
            })
        return out

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
        # MERGE keeps one row per source object for incremental planning. first_seen is
        # PRESERVED on update (set once, on the first insert) — so the UPDATE lists every
        # column EXCEPT the preserved ones; INSERT still writes them all.
        update_cols = [
            f.name for f in schema.fields if f.name not in _PRESERVE_ON_UPDATE
        ]
        set_clause = ", ".join(
            f"target.{c} = source.{c}" for c in update_cols
        )
        self.spark.sql(
            f"""
            MERGE INTO {self.full_name} AS target
            USING {temp_view} AS source
            ON target.source_full_name = source.source_full_name
               AND target.object_type = source.object_type
            WHEN MATCHED THEN UPDATE SET {set_clause}
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
    connectivity_mode: str = "",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    last_action = _last_action_for(result)
    # Split the two message channels (bug #19): error_message carries ONLY a real
    # failure; detail carries informational context (skip reason, applied note, DDL).
    err_text = str(result.get("error_message") or "")
    info_text = str(result.get("message") or "")
    if last_action == vocab.FAILED:
        error_message = (err_text or info_text)[:4000]
        detail = ""
        last_error_raw = (err_text or info_text)  # full, untruncated (Part D)
    else:
        error_message = ""
        detail = (info_text or err_text)[:4000]
        last_error_raw = ""
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
        "last_action": last_action,
        "last_sync_at": now,
        "last_synced_by": ran_by,
        "ddl_path": str(result.get("ddl_path") or ""),
        "grants_path": str(result.get("grants_path") or ""),
        "error_code": str(result.get("error_code") or ""),
        "error_message": error_message,
        "detail": detail,
        "utility_version": utility_version,
        # Part D — state-table parity columns.
        "first_seen": now,  # preserved on update by the MERGE (set once, on insert)
        "connectivity_mode": str(connectivity_mode or ""),
        "failure_category": _failure_category_for(
            str(result.get("error_code") or ""), last_action
        ),
        "last_error_raw": last_error_raw,
        "updated_at": now,
    }
