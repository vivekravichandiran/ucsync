"""Audit table helpers (schema DDL + row shape)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from uc_sync import __version__
from uc_sync.security import redact

# Ordered (column, SQL type). Appended entries are added to existing tables by
# AuditService.ensure_table, so new and upgraded tables share the same layout.
AUDIT_SCHEMA: list[tuple[str, str]] = [
    ("run_id", "STRING"),
    ("operation_id", "STRING"),
    ("source_workspace_url", "STRING"),
    ("target_workspace_url", "STRING"),
    ("source_metastore_id", "STRING"),
    ("target_metastore_id", "STRING"),
    ("catalog_name", "STRING"),
    ("schema_name", "STRING"),
    ("object_name", "STRING"),
    ("full_name", "STRING"),
    ("object_type", "STRING"),
    ("source_object_id", "STRING"),
    ("source_created_at", "TIMESTAMP"),
    ("source_last_modified_at", "TIMESTAMP"),
    ("source_last_modified_source", "STRING"),
    ("inventory_created_at", "TIMESTAMP"),
    ("export_created_at", "TIMESTAMP"),
    ("import_created_at", "TIMESTAMP"),
    ("validation_created_at", "TIMESTAMP"),
    ("export_status", "STRING"),
    ("import_status", "STRING"),
    ("validation_status", "STRING"),
    ("source_definition_hash", "STRING"),
    ("exported_definition_hash", "STRING"),
    ("target_definition_hash", "STRING"),
    ("export_path", "STRING"),
    ("ddl_path", "STRING"),
    ("dependency_level", "INT"),
    ("import_order", "INT"),
    ("operation_mode", "STRING"),
    ("error_code", "STRING"),
    ("error_message", "STRING"),
    ("metadata_json", "STRING"),
    ("utility_version", "STRING"),
    ("created_at", "TIMESTAMP"),
    ("updated_at", "TIMESTAMP"),
    # Unified outcome for the row's own stage, so every row has one readable
    # status without needing to know which stage column applies.
    ("status", "STRING"),
    ("target_full_name", "STRING"),
]

AUDIT_COLUMNS = [name for name, _ in AUDIT_SCHEMA]

AUDIT_COLUMN_TYPES = dict(AUDIT_SCHEMA)

AUDIT_TABLE_DDL = (
    "\nCREATE TABLE IF NOT EXISTS {full_name} (\n"
    + ",\n".join(f"  {name} {sql_type}" for name, sql_type in AUDIT_SCHEMA)
    + "\n) USING DELTA\n"
)


def ensure_audit_table_sql(full_name: str) -> str:
    return AUDIT_TABLE_DDL.format(full_name=full_name)


def ensure_audit_schema_sql(full_name: str) -> Optional[str]:
    """DDL for the audit table's parent schema, when it can be derived."""
    parts = full_name.split(".")
    if len(parts) != 3:
        return None
    return f"CREATE SCHEMA IF NOT EXISTS {parts[0]}.{parts[1]}"


def add_missing_columns_sql(full_name: str, existing: Iterable[str]) -> Optional[str]:
    """ALTER statement that brings an older audit table up to the current schema."""
    present = {str(name) for name in existing}
    missing = [
        f"{name} {sql_type}"
        for name, sql_type in AUDIT_SCHEMA
        if name not in present
    ]
    if not missing:
        return None
    return f"ALTER TABLE {full_name} ADD COLUMNS ({', '.join(missing)})"


def backfill_status_sql(full_name: str) -> str:
    """Populate the unified status column for rows written before it existed."""
    return f"""
        UPDATE {full_name}
        SET status = CASE upper(operation_mode)
            WHEN 'IMPORT' THEN import_status
            WHEN 'EXPORT' THEN export_status
            WHEN 'MIGRATE' THEN export_status
            WHEN 'INVENTORY' THEN 'PENDING'
            ELSE validation_status
        END
        WHERE status IS NULL
    """


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        # UC REST timestamps are typically epoch milliseconds.
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


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def error_audit_row(
    *,
    run_id: str,
    stage: str,
    result: Mapping[str, Any],
    source_workspace_url: str = "",
    target_workspace_url: str = "",
    source_metastore_id: str = "",
    target_metastore_id: str = "",
) -> dict[str, Any]:
    """Backward-compatible alias for stage audit rows."""
    return stage_audit_row(
        run_id=run_id,
        stage=stage,
        result=result,
        source_workspace_url=source_workspace_url,
        target_workspace_url=target_workspace_url,
        source_metastore_id=source_metastore_id,
        target_metastore_id=target_metastore_id,
    )


def stage_audit_row(
    *,
    run_id: str,
    stage: str,
    result: Mapping[str, Any],
    source_workspace_url: str = "",
    target_workspace_url: str = "",
    source_metastore_id: str = "",
    target_metastore_id: str = "",
) -> dict[str, Any]:
    """Convert any stage result (SUCCESS / FAILURE / PENDING / …) to audit schema."""
    now = datetime.now(timezone.utc)
    full_name = str(
        result.get("source_full_name") or result.get("full_name") or ""
    )
    parts = full_name.split(".")
    status = str(result.get("status") or "").upper()
    if status in {"ERROR", "FAILED"}:
        status = "FAILURE"
    elif status in {"DRY_RUN", "SKIPPED", "SKIP_EXISTING"}:
        status = "PENDING" if status != "SKIP_EXISTING" else "SUCCESS"
    elif status.startswith("SUCCESS"):
        status = "SUCCESS"
    elif status == "MANUAL_ACTION_REQUIRED":
        status = "MANUAL_ACTION_REQUIRED"
    elif not status:
        status = "PENDING"

    row = {column: None for column in AUDIT_COLUMNS}
    row.update(
        {
            "run_id": run_id,
            "operation_id": str(uuid.uuid4()),
            "source_workspace_url": source_workspace_url,
            "target_workspace_url": target_workspace_url,
            "source_metastore_id": source_metastore_id,
            "target_metastore_id": target_metastore_id,
            "catalog_name": parts[0] if parts else None,
            "schema_name": parts[1] if len(parts) > 2 else None,
            "object_name": parts[-1] if parts else None,
            "full_name": full_name,
            "object_type": str(result.get("object_type") or ""),
            "source_object_id": str(
                result.get("source_object_id") or result.get("object_id") or ""
            )
            or None,
            "source_created_at": _as_datetime(
                result.get("created_at") or result.get("source_created_at")
            ),
            "source_last_modified_at": _as_datetime(
                result.get("last_modified_at")
                or result.get("source_last_modified_at")
            ),
            "source_last_modified_source": result.get("last_modified_source")
            or result.get("source_last_modified_source"),
            "source_definition_hash": str(
                result.get("source_definition_hash")
                or result.get("definition_hash")
                or ""
            )
            or None,
            "export_path": result.get("metadata_path") or result.get("export_path"),
            "ddl_path": result.get("ddl_path") or result.get("workspace_ddl_path"),
            "dependency_level": _as_int(result.get("dependency_level")),
            "import_order": _as_int(result.get("import_order")),
            "operation_mode": stage.upper(),
            "error_code": str(result.get("error_code") or "") or None,
            "error_message": redact(
                str(result.get("error_message") or result.get("message") or "")
            )
            or None,
            "metadata_json": redact(json.dumps(dict(result), default=str)),
            "utility_version": __version__,
            "created_at": now,
            "updated_at": now,
            "status": status,
            "target_full_name": str(result.get("target_full_name") or "") or None,
        }
    )
    stage_name = stage.upper()
    if stage_name == "EXPORT":
        row["export_status"] = status
        row["export_created_at"] = now
        # Objects that exported successfully are pending import until that stage runs.
        row["import_status"] = "PENDING" if status == "SUCCESS" else None
    elif stage_name == "MIGRATE":
        row["export_status"] = status
        row["export_created_at"] = now
        row["import_status"] = "PENDING" if status == "SUCCESS" else None
        row["ddl_path"] = result.get("target_path") or result.get("ddl_path")
    elif stage_name == "IMPORT":
        row["import_status"] = status
        row["import_created_at"] = now
    elif stage_name == "INVENTORY":
        row["inventory_created_at"] = now
        row["export_status"] = "PENDING"
        row["import_status"] = "PENDING"
    else:
        row["validation_status"] = status
        row["validation_created_at"] = now
    return row


class AuditService:
    def __init__(self, spark: Any, full_name: str):
        self.spark = spark
        self.full_name = full_name

    def ensure_table(self) -> None:
        schema_sql = ensure_audit_schema_sql(self.full_name)
        if schema_sql:
            self.spark.sql(schema_sql)
        self.spark.sql(ensure_audit_table_sql(self.full_name))
        existing = [field.name for field in self.spark.table(self.full_name).schema]
        alter_sql = add_missing_columns_sql(self.full_name, existing)
        if alter_sql:
            self.spark.sql(alter_sql)
            # One-time upgrade: give pre-existing rows a readable unified status.
            self.spark.sql(backfill_status_sql(self.full_name))

    def append(self, rows: Iterable[Mapping[str, Any]]) -> int:
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
                elif type_name.startswith("int"):
                    value = _as_int(value)
                elif type_name in {"string", "varchar", "char"} and value is not None:
                    value = str(value)
                values.append(value)
            aligned.append(tuple(values))
        (
            self.spark.createDataFrame(aligned, schema=schema)
            .write.mode("append")
            .saveAsTable(self.full_name)
        )
        return len(records)
