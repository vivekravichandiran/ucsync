"""Dependency-aware metadata import, including same-workspace local mode."""

from __future__ import annotations

import random
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable, List, Optional, Protocol

from uc_sync.config import SyncConfig
from uc_sync.dependency import plan
from uc_sync.export import canonical_hash
from uc_sync.mapping import MappingResolver
from uc_sync.models import UCObject
from uc_sync.package_import import (
    _POLICY_COMPUTE_HINT as POLICY_COMPUTE_HINT,
    _is_policy_unsupported_error,
)
from uc_sync.sql_ddl import (
    POLICY_TABLE_TYPES,
    create_ddl_for_object,
    quote_full_name,
    quote_identifier,
)
from uc_sync.workspace_client import WorkspaceClient


@dataclass
class ImportResult:
    object_type: str
    source_full_name: str
    target_full_name: str
    full_name: str
    action: str
    status: str
    message: str = ""
    error_code: str = ""
    source_location: str = ""
    target_location: str = ""
    target_external_location: str = ""
    target_credential: str = ""
    dependency_level: int = 0
    import_order: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SqlExecutor(Protocol):
    def execute(self, sql: str) -> Any: ...

    def show_create(self, object_type: str, full_name: str) -> str: ...


class SparkSqlExecutor:
    def __init__(self, spark: Any):
        self.spark = spark

    def execute(self, sql: str) -> Any:
        return self.spark.sql(sql).collect()

    def show_create(self, object_type: str, full_name: str) -> str:
        command = (
            f"SHOW CREATE FUNCTION {quote_full_name(full_name)}"
            if object_type == "FUNCTION"
            else f"SHOW CREATE TABLE {quote_full_name(full_name)}"
        )
        rows = self.spark.sql(command).collect()
        if not rows:
            raise RuntimeError(f"SHOW CREATE returned no rows for {full_name}")
        row = rows[0]
        return str(row[0])


class _TransientSqlError(Exception):
    """A statement failure worth retrying (network blip, warehouse warming)."""


# Substrings in a statement error that indicate a transient, retryable failure
# (warehouse spinning up, capacity, service maintenance) rather than a
# deterministic SQL error (syntax/permission/not-found), which must fail fast.
_RETRYABLE_STATEMENT_HINTS = (
    "temporarily_unavailable",
    "service_under_maintenance",
    "temporarily unavailable",
    "please try again",
    "try again later",
    "deadline_exceeded",
    "deadline exceeded",
    "warehouse is starting",
    "cluster is starting",
    "no worker",
    "capacity",
)


def _statement_error_is_retryable(message: str) -> bool:
    low = (message or "").lower()
    return any(hint in low for hint in _RETRYABLE_STATEMENT_HINTS)


class RestSqlExecutor:
    """Run SQL against a (possibly remote) workspace via the Statement
    Execution API instead of a local Spark session.

    Governance reads (tags, ABAC policies) query ``information_schema`` on the
    workspace that OWNS the objects. When the job runs on the target but
    inventories a remote source (``connectivity_mode=direct``), a local
    ``SparkSqlExecutor`` would hit the target's Spark session — where the source
    catalogs do not exist — so those reads come back empty. Pointing this
    executor at the source workspace (same SP creds as the REST inventory) makes
    tag/ABAC reads follow the source like everything else.

    Returns rows as plain lists so callers can index/unpack them exactly like
    ``spark.sql(...).collect()`` rows (JSON_ARRAY renders every value as a
    string, so array columns arrive as JSON text — ``governance._json_list``
    already parses that).

    Production hardening: the reads are idempotent SELECT/DESCRIBE, so the whole
    statement is retried with exponential backoff + jitter on transient failures
    — network/HTTP errors surfacing from the client (which itself already retries
    429/5xx) and transient statement states (warehouse warming, capacity).
    Deterministic SQL errors (syntax, permission, not-found) fail fast without
    retry. Polling backs off up to a cap so a cold-warehouse wait does not hammer
    the API.
    """

    def __init__(
        self,
        client: "WorkspaceClient",
        warehouse_id: str,
        *,
        poll_seconds: float = 2.0,
        max_wait_seconds: float = 600.0,
        max_retries: int = 4,
        retry_base_seconds: float = 1.0,
        poll_cap_seconds: float = 15.0,
    ):
        if not warehouse_id:
            raise ValueError("warehouse_id is required for RestSqlExecutor")
        self.client = client
        self.warehouse_id = warehouse_id
        self.poll_seconds = poll_seconds
        self.max_wait_seconds = max_wait_seconds
        self.max_retries = max(0, int(max_retries))
        self.retry_base_seconds = retry_base_seconds
        self.poll_cap_seconds = poll_cap_seconds

    @classmethod
    def for_ddl_capture(
        cls, client: "WorkspaceClient", warehouse_id: str
    ) -> "RestSqlExecutor":
        """A ``RestSqlExecutor`` tuned for export-stage ``SHOW CREATE`` capture.

        DDL capture is warehouse-only and has no synthesized fallback (plan P2-A),
        so it must ride out a cold-warehouse warm-up and transient statement states
        rather than give up early: more retries, a longer per-statement deadline,
        and a slightly larger backoff base than the governance-read defaults. SHOW
        CREATE is an idempotent read, so retrying is always safe.
        """

        return cls(
            client,
            warehouse_id,
            max_retries=6,
            retry_base_seconds=2.0,
            max_wait_seconds=900.0,
        )

    def execute(self, sql: str) -> list[list[Any]]:
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._run_once(sql)
            except _TransientSqlError as exc:
                last_err = exc
                if attempt >= self.max_retries:
                    break
                delay = min(self.retry_base_seconds * (2 ** attempt), 30.0)
                time.sleep(delay + random.uniform(0, delay * 0.25))
        raise RuntimeError(
            f"statement failed after {self.max_retries + 1} attempt(s): {last_err}"
        )

    def _run_once(self, sql: str) -> list[list[Any]]:
        try:
            resp = self.client.post(
                "/api/2.0/sql/statements",
                {
                    "warehouse_id": self.warehouse_id,
                    "statement": sql,
                    "wait_timeout": "30s",
                    "on_wait_timeout": "CONTINUE",
                    "disposition": "INLINE",
                    "format": "JSON_ARRAY",
                },
            )
        except Exception as exc:  # noqa: BLE001 - client raises RuntimeError
            raise _TransientSqlError(f"submit failed: {exc}") from exc

        deadline = time.time() + self.max_wait_seconds
        delay = self.poll_seconds
        while True:
            state = str((resp.get("status") or {}).get("state") or "").upper()
            if state == "SUCCEEDED":
                break
            if state in {"FAILED", "CANCELED", "CLOSED"}:
                err = (resp.get("status") or {}).get("error") or {}
                msg = str(err.get("message") or err or sql[:120])
                if _statement_error_is_retryable(msg):
                    raise _TransientSqlError(f"statement {state}: {msg}")
                # Deterministic SQL error — do not retry.
                raise RuntimeError(f"statement {state}: {msg}")
            if time.time() > deadline:
                # A cold warehouse warms in well under this; a timeout here means
                # something is wrong that a resubmit won't fix — fail terminally.
                raise RuntimeError(
                    f"statement did not finish before "
                    f"{self.max_wait_seconds:.0f}s (state={state})"
                )
            time.sleep(delay + random.uniform(0, delay * 0.25))
            delay = min(delay * 1.5, self.poll_cap_seconds)
            try:
                resp = self.client.get(
                    f"/api/2.0/sql/statements/{resp.get('statement_id')}"
                )
            except Exception as exc:  # noqa: BLE001
                raise _TransientSqlError(f"poll failed: {exc}") from exc

        result = resp.get("result") or {}
        rows = [list(r) for r in (result.get("data_array") or [])]
        # Follow chunk links in case a governance read spans multiple chunks.
        next_link = result.get("next_chunk_internal_link")
        while next_link:
            try:
                chunk = self.client.get(next_link)
            except Exception as exc:  # noqa: BLE001
                raise _TransientSqlError(f"chunk fetch failed: {exc}") from exc
            rows.extend(list(r) for r in (chunk.get("data_array") or []))
            next_link = chunk.get("next_chunk_internal_link")
        return rows

    def show_create(self, object_type: str, full_name: str) -> str:
        """Capture full-fidelity DDL from the (remote) source via SHOW CREATE.

        Used by the export stage in direct mode, where the job runs on the target
        but the source objects only exist on the source workspace — so the DDL
        must be read over the source warehouse, not the local Spark session.
        Functions have no ``SHOW CREATE FUNCTION`` in Databricks SQL, so the
        caller synthesizes them from inventory instead.
        """
        if str(object_type).upper() == "FUNCTION":
            raise RuntimeError(
                "SHOW CREATE FUNCTION is not supported in Databricks SQL; "
                "functions are synthesized from inventory"
            )
        rows = self.execute(f"SHOW CREATE TABLE {quote_full_name(full_name)}")
        if not rows:
            raise RuntimeError(f"SHOW CREATE returned no rows for {full_name}")
        first = rows[0]
        return str(first[0] if not isinstance(first, str) else first)


class ImportEngine:
    def __init__(
        self,
        target: WorkspaceClient,
        cfg: SyncConfig,
        sql_executor: Optional[SqlExecutor] = None,
    ):
        self.target = target
        self.cfg = cfg
        self.mapper = MappingResolver(cfg.mappings)
        self.sql = sql_executor

    def run(self, objects: Iterable[UCObject]) -> List[ImportResult]:
        objects_list = list(objects)
        results: list[ImportResult] = []
        for level, order, obj in plan(objects_list):
            try:
                results.append(self._import_one(obj, level, order))
            except Exception as exc:  # noqa: BLE001 - per-object audit is required
                target_name = self.mapper.target_full_name(obj.full_name) or ""
                results.append(
                    ImportResult(
                        object_type=obj.object_type.value,
                        source_full_name=obj.full_name,
                        target_full_name=target_name,
                        full_name=obj.full_name,
                        action=self.cfg.import_mode,
                        status="ERROR",
                        message=str(exc),
                        error_code=type(exc).__name__,
                        dependency_level=level,
                        import_order=order,
                    )
                )
        # Column masks / row filters are applied last, once every table and the
        # functions they reference exist in the target.
        results.extend(self._apply_policies(objects_list, start_order=len(results)))
        return results

    def _apply_policies(
        self, objects: Iterable[UCObject], *, start_order: int
    ) -> list[ImportResult]:
        results: list[ImportResult] = []
        order = start_order
        for obj in objects:
            if obj.object_type not in POLICY_TABLE_TYPES:
                continue
            if not obj.column_masks() and not obj.row_filter():
                continue
            order += 1
            target_table = self.mapper.target_full_name(obj.full_name)
            if not target_table and self.cfg.execution_mode == "CROSS_WORKSPACE":
                target_table = obj.full_name
            if not target_table:
                results.append(
                    self._result(
                        obj,
                        "",
                        "SKIP",
                        "ERROR",
                        0,
                        order,
                        "Catalog mapping is missing for policy application",
                        "CATALOG_MAPPING_MISSING",
                    )
                )
                continue
            statements = self._policy_statements(obj, target_table)
            if self.cfg.dry_run:
                results.append(
                    self._result(
                        obj,
                        target_table,
                        "DRY_RUN",
                        "SKIPPED",
                        0,
                        order,
                        f"policy statements={len(statements)}",
                    )
                )
                continue
            if not self.sql:
                results.append(
                    self._result(
                        obj,
                        target_table,
                        "APPLY_POLICY",
                        "ERROR",
                        0,
                        order,
                        "SQL executor is required to apply policies",
                        "SQL_EXECUTOR_REQUIRED",
                    )
                )
                continue
            try:
                for statement in statements:
                    self.sql.execute(statement)
            except Exception as exc:  # noqa: BLE001 - per-object audit is required
                message = str(exc)
                # Single-user (assigned) clusters cannot apply masks / row
                # filters; flag as MANUAL_ACTION_REQUIRED with actionable guidance
                # rather than a bare error.
                if _is_policy_unsupported_error(message):
                    results.append(
                        self._result(
                            obj,
                            target_table,
                            "MANUAL",
                            "MANUAL_ACTION_REQUIRED",
                            0,
                            order,
                            f"{POLICY_COMPUTE_HINT} {message[:400]}",
                            "POLICY_COMPUTE_UNSUPPORTED",
                        )
                    )
                    continue
                results.append(
                    self._result(
                        obj,
                        target_table,
                        "APPLY_POLICY",
                        "ERROR",
                        0,
                        order,
                        message,
                        type(exc).__name__,
                    )
                )
                continue
            results.append(
                self._result(
                    obj,
                    target_table,
                    "APPLY_POLICY",
                    "SUCCESS",
                    0,
                    order,
                    statements[0][:1000] if statements else "",
                )
            )
        return results

    def _map_function_name(self, function_full_name: str) -> Optional[str]:
        mapped = self.mapper.target_full_name(function_full_name)
        if mapped:
            return mapped
        if self.cfg.execution_mode == "CROSS_WORKSPACE":
            return function_full_name
        return None

    def _policy_statements(
        self, obj: UCObject, target_table: str
    ) -> list[str]:
        """Build target-side mask / row-filter ALTER statements via the mapper."""

        target = quote_full_name(target_table)
        statements: list[str] = []
        for mask in obj.column_masks():
            column = mask.get("column_name")
            function_name = mask.get("function_name")
            if not column or not function_name:
                continue
            target_fn = self._map_function_name(str(function_name))
            if not target_fn:
                continue
            using = mask.get("using_column_names") or []
            using_clause = (
                " USING COLUMNS ("
                + ", ".join(quote_identifier(str(col)) for col in using)
                + ")"
                if using
                else ""
            )
            statements.append(
                f"ALTER TABLE {target} ALTER COLUMN "
                f"{quote_identifier(str(column))} "
                f"SET MASK {quote_full_name(target_fn)}{using_clause}"
            )
        row_filter = obj.row_filter()
        if row_filter and row_filter.get("function_name"):
            target_fn = self._map_function_name(str(row_filter["function_name"]))
            if target_fn:
                columns = ", ".join(
                    quote_identifier(str(col))
                    for col in row_filter.get("input_column_names") or []
                )
                statements.append(
                    f"ALTER TABLE {target} SET ROW FILTER "
                    f"{quote_full_name(target_fn)} ON ({columns})"
                )
        return statements

    def _import_one(self, obj: UCObject, level: int, order: int) -> ImportResult:
        digest = canonical_hash(obj)
        location_mapping = self._location_mapping(obj)
        target_full_name = (
            str(location_mapping.get("target_external_location") or "")
            if obj.object_type.value == "EXTERNAL_LOCATION"
            and location_mapping
            else self.mapper.target_full_name(obj.full_name)
        )
        if (
            not target_full_name
            and self.cfg.execution_mode == "CROSS_WORKSPACE"
        ):
            target_full_name = obj.full_name
        if not target_full_name:
            error_code = (
                "LOCATION_MAPPING_MISSING"
                if obj.object_type.value == "EXTERNAL_LOCATION"
                else "CATALOG_MAPPING_MISSING"
            )
            return self._result(
                obj,
                "",
                "SKIP",
                "ERROR",
                level,
                order,
                (
                    "External location mapping is missing"
                    if error_code == "LOCATION_MAPPING_MISSING"
                    else "Catalog mapping is missing"
                ),
                error_code,
            )
        if obj.object_type.value in {"EXTERNAL_LOCATION", "EXTERNAL_TABLE"}:
            if not location_mapping:
                return self._result(
                    obj,
                    target_full_name,
                    "SKIP",
                    "ERROR",
                    level,
                    order,
                    "No source-to-target location mapping matched the object path",
                    "LOCATION_MAPPING_MISSING",
                )
        if self.cfg.dry_run:
            return self._result(
                obj,
                target_full_name,
                "DRY_RUN",
                "SKIPPED",
                level,
                order,
                f"hash={digest[:12]}",
            )
        if obj.object_type.value in {"EXTERNAL_LOCATION", "EXTERNAL_TABLE"}:
            existing_result = self._existing_external_result(
                obj, target_full_name, level, order
            )
            if existing_result:
                return existing_result
        if obj.object_type.value in {"CATALOG", "FUNCTION"} and self._target_exists(
            obj.object_type.value, target_full_name
        ):
            return self._result(
                obj,
                target_full_name,
                "NOOP",
                "SUCCESS",
                level,
                order,
                f"target {obj.object_type.value.lower()} already exists",
            )
        if not self.sql:
            return self._result(
                obj,
                target_full_name,
                "CREATE",
                "ERROR",
                level,
                order,
                "SQL executor is required for metadata import",
                "SQL_EXECUTOR_REQUIRED",
            )

        statement = self._statement(obj, target_full_name)
        if not statement:
            return self._result(
                obj,
                target_full_name,
                "MANUAL",
                "MANUAL_ACTION_REQUIRED",
                level,
                order,
                f"{obj.object_type.value} requires an explicit migration adapter",
                "UNSUPPORTED_OBJECT_TYPE",
            )
        try:
            self.sql.execute(statement)
        except Exception as exc:
            raise RuntimeError(f"{exc}\nSQL: {statement}") from exc
        return self._result(
            obj,
            target_full_name,
            "CREATE_OR_SKIP",
            "SUCCESS",
            level,
            order,
            statement[:1000],
        )

    def _statement(self, obj: UCObject, target_full_name: str) -> Optional[str]:
        source = quote_full_name(obj.full_name)
        target = quote_full_name(target_full_name)
        kind = obj.object_type.value
        if kind == "EXTERNAL_LOCATION":
            target = quote_identifier(target_full_name)
        if kind == "CATALOG":
            storage_root = self.mapper.managed_storage_root(target_full_name)
            escaped_root = storage_root.replace("'", "''") if storage_root else ""
            managed = (
                f" MANAGED LOCATION '{escaped_root}'"
                if storage_root
                else ""
            )
            return f"CREATE CATALOG IF NOT EXISTS {target}{managed}"
        if kind == "SCHEMA":
            return f"CREATE SCHEMA IF NOT EXISTS {target}"
        if kind == "TABLE":
            # Metadata-only local copy: target table is empty by design.
            if self.cfg.execution_mode == "LOCAL":
                return f"CREATE TABLE IF NOT EXISTS {target} LIKE {source}"
            return self._managed_table_ddl(obj, target)
        if kind == "VOLUME":
            return f"CREATE VOLUME IF NOT EXISTS {target}"
        if kind == "EXTERNAL_LOCATION":
            mapping = self._location_mapping(obj)
            if not mapping:
                return None
            target_url = self._escape_literal(
                mapping.get("target_external_location_url")
                or mapping["target_location"]
            )
            credential = quote_identifier(mapping["target_credential"])
            comment = self._comment_clause(obj.definition.get("comment"))
            return (
                f"CREATE EXTERNAL LOCATION IF NOT EXISTS {target} "
                f"URL '{target_url}' WITH (STORAGE CREDENTIAL {credential})"
                f"{comment}"
            )
        if kind == "EXTERNAL_TABLE":
            mapping = self._location_mapping(obj)
            if not mapping:
                return None
            target_location = self.mapper.rewrite_location(
                obj.storage_location
                or str(obj.definition.get("storage_location") or "")
            )
            if not target_location:
                return None
            return self._external_table_ddl(obj, target, target_location)
        if kind == "MATERIALIZED_VIEW" and self.cfg.execution_mode == "LOCAL":
            ddl = self.sql.show_create(kind, obj.full_name)
            return self._rewrite_ddl(ddl, obj.full_name, target_full_name)
        if kind in {"VIEW", "DYNAMIC_VIEW"}:
            definition = str(
                obj.definition.get("view_definition")
                or obj.definition.get("view_original_text")
                or ""
            ).strip()
            if not definition:
                if self.cfg.execution_mode == "LOCAL":
                    ddl = self.sql.show_create(kind, obj.full_name)
                    return self._rewrite_ddl(
                        ddl, obj.full_name, target_full_name
                    )
                return None
            comment = self._comment_clause(obj.definition.get("comment"))
            ddl = f"CREATE VIEW {target}{comment} AS {definition}"
            return self._rewrite_ddl(ddl, obj.full_name, target_full_name)
        if kind == "METRIC_VIEW":
            ddl = create_ddl_for_object(obj)
            return (
                self._rewrite_ddl(ddl, obj.full_name, target_full_name)
                if ddl
                else None
            )
        if kind == "FUNCTION":
            ddl = self._function_ddl(obj, target)
            if not ddl and self.cfg.execution_mode == "LOCAL":
                ddl = self.sql.show_create(kind, obj.full_name)
            return (
                self._rewrite_ddl(ddl, obj.full_name, target_full_name)
                if ddl
                else None
            )
        if kind == "EXTERNAL_VOLUME":
            source_location = obj.storage_location or str(
                obj.definition.get("storage_location") or ""
            )
            target_location = self.mapper.rewrite_location(source_location) or (
                source_location
                if self.cfg.execution_mode == "CROSS_WORKSPACE"
                else ""
            )
            if not target_location:
                return None
            comment = self._comment_clause(obj.definition.get("comment"))
            return (
                f"CREATE EXTERNAL VOLUME IF NOT EXISTS {target} "
                f"LOCATION '{self._escape_literal(target_location)}'{comment}"
            )
        return None

    def _existing_external_result(
        self,
        obj: UCObject,
        target_full_name: str,
        level: int,
        order: int,
    ) -> Optional[ImportResult]:
        if self.target is None:
            return None
        path = (
            f"/api/2.1/unity-catalog/external-locations/{target_full_name}"
            if obj.object_type.value == "EXTERNAL_LOCATION"
            else f"/api/2.1/unity-catalog/tables/{target_full_name}"
        )
        try:
            target = self.target.get(path)
        except Exception as exc:  # noqa: BLE001
            if "HTTP 404" in str(exc):
                return None
            raise

        mapping = self._location_mapping(obj) or {}
        source_location = obj.storage_location or str(
            obj.definition.get("storage_location")
            or obj.definition.get("url")
            or ""
        )
        if obj.object_type.value == "EXTERNAL_LOCATION":
            expected_location = str(
                mapping.get("target_external_location_url")
                or mapping.get("target_location")
                or ""
            ).rstrip("/")
            actual_location = str(target.get("url") or "").rstrip("/")
            expected_credential = str(mapping.get("target_credential") or "")
            actual_credential = str(target.get("credential_name") or "")
            matches = (
                expected_location == actual_location
                and expected_credential == actual_credential
            )
        else:
            expected_location = (
                self.mapper.rewrite_location(source_location) or ""
            ).rstrip("/")
            actual_location = str(
                target.get("storage_location") or ""
            ).rstrip("/")
            expected_credential = ""
            actual_credential = ""
            matches = expected_location == actual_location

        if matches:
            return self._result(
                obj,
                target_full_name,
                "NOOP",
                "SUCCESS",
                level,
                order,
                "target already exists with the mapped storage configuration",
            )
        return self._result(
            obj,
            target_full_name,
            "SKIP",
            "ERROR",
            level,
            order,
            (
                f"Existing target conflicts with mapping: expected "
                f"location={expected_location!r}, actual "
                f"location={actual_location!r}, expected "
                f"credential={expected_credential!r}, actual "
                f"credential={actual_credential!r}"
            ),
            "EXTERNAL_STORAGE_MAPPING_CONFLICT",
        )

    def _location_mapping(self, obj: UCObject) -> Optional[dict[str, str]]:
        source_location = obj.storage_location or str(
            obj.definition.get("storage_location")
            or obj.definition.get("url")
            or ""
        )
        if obj.object_type.value == "EXTERNAL_LOCATION":
            return self.mapper.external_location_mapping(
                obj.name,
                source_location,
                str(obj.definition.get("credential_name") or ""),
            )
        if obj.object_type.value == "EXTERNAL_TABLE":
            return self.mapper.location_mapping_for_url(source_location)
        return None

    def _target_exists(self, object_type: str, full_name: str) -> bool:
        if self.target is None:
            return False
        endpoint = {
            "CATALOG": "catalogs",
            "FUNCTION": "functions",
        }[object_type]
        try:
            self.target.get(f"/api/2.1/unity-catalog/{endpoint}/{full_name}")
            return True
        except Exception as exc:  # noqa: BLE001
            if "HTTP 404" in str(exc):
                return False
            raise

    @staticmethod
    def _escape_literal(value: Any) -> str:
        return str(value).replace("'", "''")

    @classmethod
    def _comment_clause(cls, value: Any) -> str:
        return (
            f" COMMENT '{cls._escape_literal(value)}'"
            if value is not None and str(value)
            else ""
        )

    def _managed_table_ddl(
        self, obj: UCObject, target: str, location: str = ""
    ) -> Optional[str]:
        columns = obj.definition.get("columns") or []
        if not columns:
            return None
        definitions = []
        for column in sorted(columns, key=lambda item: item.get("position", 0)):
            name = quote_identifier(str(column["name"]))
            data_type = str(
                column.get("type_text") or column.get("type_name") or ""
            ).strip()
            if not data_type:
                return None
            nullable = "" if column.get("nullable", True) else " NOT NULL"
            comment = self._comment_clause(column.get("comment"))
            definitions.append(f"{name} {data_type}{nullable}{comment}")
        table_comment = self._comment_clause(obj.definition.get("comment"))
        data_format = str(
            obj.definition.get("data_source_format") or "DELTA"
        ).upper()
        # Runtime-generated Delta protocol properties are not portable.
        portable_properties = {
            key: value
            for key, value in (obj.properties or {}).items()
            if not str(key).lower().startswith("delta.")
        }
        properties = ""
        if portable_properties:
            pairs = ", ".join(
                f"'{self._escape_literal(key)}' = "
                f"'{self._escape_literal(value)}'"
                for key, value in sorted(portable_properties.items())
            )
            properties = f" TBLPROPERTIES ({pairs})"
        location_clause = (
            f" LOCATION '{self._escape_literal(location)}'" if location else ""
        )
        return (
            f"CREATE TABLE IF NOT EXISTS {target} "
            f"({', '.join(definitions)}) USING {data_format}"
            f"{location_clause}{table_comment}{properties}"
        )

    def _external_table_ddl(
        self, obj: UCObject, target: str, target_location: str
    ) -> Optional[str]:
        return self._managed_table_ddl(obj, target, location=target_location)

    def _function_ddl(self, obj: UCObject, target: str) -> Optional[str]:
        params = (obj.definition.get("input_params") or {}).get("parameters") or []
        declarations = []
        for param in sorted(params, key=lambda item: item.get("position", 0)):
            raw_name = str(param["name"])
            name = (
                raw_name
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw_name)
                else quote_identifier(raw_name)
            )
            data_type = str(
                param.get("type_text") or param.get("type_name") or ""
            ).strip()
            if not data_type:
                return None
            declarations.append(f"{name} {data_type}")
        return_type = str(
            obj.definition.get("full_data_type")
            or obj.definition.get("data_type")
            or ""
        ).strip()
        body = str(obj.definition.get("routine_definition") or "").strip()
        if not return_type or not body:
            return None
        comment = self._comment_clause(obj.definition.get("comment"))
        return (
            f"CREATE FUNCTION {target}"
            f"({', '.join(declarations)}) RETURNS {return_type}"
            f"{comment} RETURN {body}"
        )

    def _rewrite_ddl(
        self, ddl: str, source_full_name: str, target_full_name: str
    ) -> str:
        rewritten = ddl
        source_quoted = quote_full_name(source_full_name)
        target_quoted = quote_full_name(target_full_name)
        rewritten = rewritten.replace(source_quoted, target_quoted, 1)
        rewritten = re.sub(
            rf"(?<![\w`]){re.escape(source_full_name)}(?![\w`])",
            target_full_name,
            rewritten,
            count=1,
        )
        for source_catalog, target_catalog in self.cfg.catalog_mapping.items():
            rewritten = rewritten.replace(
                quote_identifier(source_catalog) + ".",
                quote_identifier(target_catalog) + ".",
            )
            rewritten = re.sub(
                rf"(?<![\w`]){re.escape(source_catalog)}\.",
                target_catalog + ".",
                rewritten,
            )
        replace_mode = self.cfg.import_mode.upper() in {
            "CREATE_OR_UPDATE",
            "RECONCILE",
        }
        rewritten = re.sub(
            r"(?i)^CREATE\s+(?:OR\s+REPLACE\s+)?(MATERIALIZED\s+)?VIEW\s+",
            lambda match: (
                f"CREATE OR REPLACE {match.group(1) or ''}VIEW "
                if replace_mode
                else f"CREATE {match.group(1) or ''}VIEW IF NOT EXISTS "
            ),
            rewritten,
            count=1,
        )
        rewritten = re.sub(
            r"(?i)^CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+",
            # Databricks SQL resolves SQL-function parameters correctly through
            # the OR REPLACE form. CREATE-only can parse the declaration but
            # then lose the parameter binding during analysis.
            "CREATE OR REPLACE FUNCTION ",
            rewritten,
            count=1,
        )
        return rewritten

    def _result(
        self,
        obj: UCObject,
        target_full_name: str,
        action: str,
        status: str,
        level: int,
        order: int,
        message: str = "",
        error_code: str = "",
    ) -> ImportResult:
        source_location = obj.storage_location or str(
            obj.definition.get("storage_location")
            or obj.definition.get("url")
            or ""
        )
        location_mapping = self._location_mapping(obj) or {}
        target_location = (
            self.mapper.rewrite_location(source_location)
            if obj.object_type.value == "EXTERNAL_TABLE"
            else (
                location_mapping.get("target_external_location_url")
                or location_mapping.get("target_location")
            )
        )
        return ImportResult(
            object_type=obj.object_type.value,
            source_full_name=obj.full_name,
            target_full_name=target_full_name,
            full_name=obj.full_name,
            action=action,
            status=status,
            message=message,
            error_code=error_code,
            source_location=source_location,
            target_location=str(target_location or ""),
            target_external_location=str(
                location_mapping.get("target_external_location") or ""
            ),
            target_credential=str(
                location_mapping.get("target_credential") or ""
            ),
            dependency_level=level,
            import_order=order,
        )
