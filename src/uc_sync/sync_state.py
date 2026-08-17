"""Sync state table — last successful/failed sync per UC object for incremental runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

STATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {full_name} (
  batch_id STRING,
  run_id STRING,
  object_type STRING,
  source_full_name STRING,
  target_full_name STRING,
  source_object_id STRING,
  source_definition_hash STRING,
  source_last_modified_at TIMESTAMP,
  last_sync_status STRING,
  last_sync_at TIMESTAMP,
  last_synced_by STRING,
  ddl_path STRING,
  grants_path STRING,
  error_code STRING,
  error_message STRING,
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
    "source_last_modified_at",
    "last_sync_status",
    "last_sync_at",
    "last_synced_by",
    "ddl_path",
    "grants_path",
    "error_code",
    "error_message",
    "utility_version",
    "updated_at",
]


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
    status = str(result.get("status") or "").upper()
    if status in {"SUCCESS", "MATCH", "SKIP_EXISTING"}:
        sync_status = "SUCCESS"
    elif status in {"PENDING", "DRY_RUN", "SKIPPED", "MANUAL_ACTION_REQUIRED"}:
        sync_status = "PENDING" if status != "MANUAL_ACTION_REQUIRED" else "MANUAL_ACTION_REQUIRED"
    else:
        sync_status = "FAILURE"
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
        "source_last_modified_at": _as_datetime(
            result.get("source_last_modified_at") or result.get("last_modified_at")
        ),
        "last_sync_status": sync_status,
        "last_sync_at": now,
        "last_synced_by": ran_by,
        "ddl_path": str(result.get("ddl_path") or ""),
        "grants_path": str(result.get("grants_path") or ""),
        "error_code": str(result.get("error_code") or ""),
        "error_message": str(
            result.get("error_message") or result.get("message") or ""
        )[:4000],
        "utility_version": utility_version,
        "updated_at": now,
    }
