"""Canonical UC object models and run status enums."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class ObjectType(str, Enum):
    CATALOG = "CATALOG"
    SCHEMA = "SCHEMA"
    TABLE = "TABLE"
    EXTERNAL_TABLE = "EXTERNAL_TABLE"
    VIEW = "VIEW"
    DYNAMIC_VIEW = "DYNAMIC_VIEW"
    METRIC_VIEW = "METRIC_VIEW"
    MATERIALIZED_VIEW = "MATERIALIZED_VIEW"
    STREAMING_TABLE = "STREAMING_TABLE"
    VOLUME = "VOLUME"
    EXTERNAL_VOLUME = "EXTERNAL_VOLUME"
    FUNCTION = "FUNCTION"
    MODEL = "MODEL"
    # Tier-A AI-asset types — discovered & reported (report-only, never migrated;
    # task 4). Reachable with catalog-scoped privileges, under catalog→schema.
    ONLINE_TABLE = "ONLINE_TABLE"
    VECTOR_INDEX = "VECTOR_INDEX"
    MONITOR = "MONITOR"
    UC_SECRET = "UC_SECRET"
    # FOREIGN table_type objects that only LOOK like tables — reported, never
    # migrated (bug #6). A Lakebase-synced table is a Postgres copy shown in UC for
    # governance visibility (data_source_format POSTGRESQL_FORMAT); a Vector Search
    # index (VECTOR_INDEX_FORMAT) classifies as ObjectType.VECTOR_INDEX above.
    LAKEBASE_TABLE = "LAKEBASE_TABLE"
    # A plain table owned by a DLT/SDP/Kafka pipeline (non-null top-level pipeline_id
    # from the tables API) — a pipeline event-log table or output. Reported, never
    # migrated (bug #8): the pipeline recreates its own event logs on the target.
    PIPELINE_TABLE = "PIPELINE_TABLE"
    STORAGE_CREDENTIAL = "STORAGE_CREDENTIAL"
    SERVICE_CREDENTIAL = "SERVICE_CREDENTIAL"
    EXTERNAL_LOCATION = "EXTERNAL_LOCATION"
    CONNECTION = "CONNECTION"
    FOREIGN_CATALOG = "FOREIGN_CATALOG"
    SHARE = "SHARE"
    RECIPIENT = "RECIPIENT"
    PROVIDER = "PROVIDER"
    ABAC_POLICY = "ABAC_POLICY"
    # A governed-tag definition (account-level tag policy + allowed values). Created
    # on the target BEFORE its values are assigned (FEAT-2), idempotent for a
    # same-account target where the tag is already visible.
    GOVERNED_TAG = "GOVERNED_TAG"
    GRANT = "GRANT"
    BINDING = "BINDING"


class LastModifiedSource(str, Enum):
    REST_API = "REST_API"
    SQL = "SQL"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class ValidationStatus(str, Enum):
    MATCH = "MATCH"
    DIFFERENT = "DIFFERENT"
    MISSING_TARGET = "MISSING_TARGET"
    EXTRA_TARGET = "EXTRA_TARGET"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"
    ERROR = "ERROR"


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"


@dataclass
class UCObject:
    object_type: ObjectType
    name: str
    full_name: str
    catalog: Optional[str] = None
    schema: Optional[str] = None
    object_id: Optional[str] = None
    owner: Optional[str] = None
    created_at: Optional[int] = None
    last_modified_at: Optional[int] = None
    last_modified_source: LastModifiedSource = LastModifiedSource.NOT_AVAILABLE
    table_type: Optional[str] = None
    data_source_format: Optional[str] = None
    storage_location: Optional[str] = None
    external_location_name: Optional[str] = None
    storage_credential_name: Optional[str] = None
    credential_type: Optional[str] = None
    credential_purpose: Optional[str] = None
    access_connector_id: Optional[str] = None
    user_assigned_managed_identity_id: Optional[str] = None
    credential_permissions: list[dict[str, Any]] = field(default_factory=list)
    definition: dict[str, Any] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    grants: list[dict[str, Any]] = field(default_factory=list)
    bindings: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    source_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["object_type"] = self.object_type.value
        data["last_modified_source"] = self.last_modified_source.value
        return data

    def column_masks(self) -> list[dict[str, Any]]:
        """Directly-defined column masks for this table.

        Returns a list of ``{column_name, function_name, using_column_names}``.
        Prefers the normalized ``definition['column_masks']`` populated by
        inventory; falls back to the raw ``columns[i]['mask']`` shape returned by
        the UC Tables REST API. Inherited (``effective_masks``) policies are
        intentionally excluded.
        """

        normalized = self.definition.get("column_masks")
        if normalized:
            return [dict(item) for item in normalized if item]
        derived: list[dict[str, Any]] = []
        for column in self.definition.get("columns") or []:
            if not isinstance(column, dict):
                continue
            mask = column.get("mask")
            if isinstance(mask, dict) and mask.get("function_name"):
                derived.append(
                    {
                        "column_name": column.get("name"),
                        "function_name": mask.get("function_name"),
                        "using_column_names": list(
                            mask.get("using_column_names") or []
                        ),
                    }
                )
        return derived

    def row_filter(self) -> Optional[dict[str, Any]]:
        """Directly-defined row filter, or ``None``.

        Returns ``{function_name, input_column_names}``. Reads the normalized
        ``definition['row_filter']`` (top-level ``row_filter`` from the REST
        payload); inherited (``effective_row_filters``) policies are excluded.
        """

        raw = self.definition.get("row_filter")
        if isinstance(raw, dict) and raw.get("function_name"):
            return {
                "function_name": raw.get("function_name"),
                "input_column_names": list(raw.get("input_column_names") or []),
            }
        return None
