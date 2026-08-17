"""Inventory service — discover UC objects from source."""

from __future__ import annotations

from typing import Iterable, List

from uc_sync.config import SyncConfig
from uc_sync.filters import allowed
from uc_sync.mapping import MappingResolver
from uc_sync.models import LastModifiedSource, ObjectType, UCObject
from uc_sync.workspace_client import WorkspaceClient


def _ts(obj: dict, key: str = "updated_at") -> tuple[int | None, LastModifiedSource]:
    val = obj.get(key)
    if val is None:
        return None, LastModifiedSource.NOT_AVAILABLE
    return int(val), LastModifiedSource.REST_API


def _is_dynamic_view(table: dict) -> bool:
    """Identify views whose definition depends on the querying principal.

    Unity Catalog exposes dynamic views through the tables API as ``VIEW``.
    The API has no separate dynamic-view type, so use the documented
    identity/group functions as the classification signal.
    """
    definition = " ".join(
        str(table.get(key) or "")
        for key in ("view_definition", "view_original_text")
    ).lower()
    return any(
        marker in definition
        for marker in (
            "current_user(",
            "session_user(",
            "is_member(",
            "is_account_group_member(",
        )
    )


def _is_metric_view(table: dict) -> bool:
    """Identify UC metric views across REST/runtime representation variants."""
    table_type = str(table.get("table_type") or "").upper()
    if table_type == "METRIC_VIEW":
        return True
    properties = table.get("properties") or {}
    property_markers = " ".join(
        str(properties.get(key) or "")
        for key in (
            "view.subType",
            "view_subtype",
            "table_type",
            "tableType",
        )
    ).upper()
    metadata_markers = " ".join(
        str(table.get(key) or "")
        for key in (
            "view_type",
            "view_subtype",
            "view_definition_format",
            "data_source_format",
        )
    ).upper()
    if "METRIC_VIEW" in property_markers or "METRIC" in metadata_markers:
        return True
    # Some API/runtime versions expose metric views as VIEW and return their
    # YAML definition in view_definition with an explicit metric marker.
    definition = str(
        table.get("view_definition")
        or table.get("view_original_text")
        or ""
    ).lstrip()
    return (
        table_type == "VIEW"
        and (
            "WITH METRICS" in definition.upper()
            or bool(table.get("view_with_metrics"))
            or bool(table.get("is_metric_view"))
        )
    )


SECURABLE_TYPE_FOR_OBJECT = {
    ObjectType.CATALOG: "catalog",
    ObjectType.SCHEMA: "schema",
    ObjectType.TABLE: "table",
    ObjectType.EXTERNAL_TABLE: "table",
    ObjectType.VIEW: "table",
    ObjectType.DYNAMIC_VIEW: "table",
    ObjectType.METRIC_VIEW: "table",
    ObjectType.MATERIALIZED_VIEW: "table",
    ObjectType.STREAMING_TABLE: "table",
    ObjectType.VOLUME: "volume",
    ObjectType.EXTERNAL_VOLUME: "volume",
    ObjectType.FUNCTION: "function",
    ObjectType.EXTERNAL_LOCATION: "external-location",
    ObjectType.STORAGE_CREDENTIAL: "storage-credential",
    ObjectType.SERVICE_CREDENTIAL: "credential",
    ObjectType.CONNECTION: "connection",
    ObjectType.FOREIGN_CATALOG: "foreign-catalog",
    ObjectType.SHARE: "share",
    ObjectType.RECIPIENT: "recipient",
    ObjectType.PROVIDER: "provider",
}


def classify_principal(principal: str) -> str:
    value = str(principal or "").strip()
    lowered = value.lower()
    if not value:
        return "UNKNOWN"
    if "@" in value and " " not in value:
        return "USER"
    # Azure/Databricks service principals are typically UUID-shaped.
    if len(value) == 36 and value.count("-") == 4:
        return "SERVICE_PRINCIPAL"
    if lowered.endswith(".gserviceaccount.com"):
        return "SERVICE_PRINCIPAL"
    if lowered.startswith("sp-") or "service-principal" in lowered:
        return "SERVICE_PRINCIPAL"
    return "GROUP"


class InventoryService:
    def __init__(self, source: WorkspaceClient, cfg: SyncConfig):
        self.source = source
        self.cfg = cfg
        self.mapper = MappingResolver(cfg.mappings)

    def run(self) -> List[UCObject]:
        objects: list[UCObject] = []
        catalogs = list(self._iter_catalogs())
        for cat in catalogs:
            objects.append(cat)
            if not allowed(cat, self.cfg):
                continue
            for schema in self._iter_schemas(cat.name):
                objects.append(schema)
                if not allowed(schema, self.cfg):
                    continue
                objects.extend(self._iter_tables(cat.name, schema.name))
                objects.extend(self._iter_volumes(cat.name, schema.name))
                objects.extend(self._iter_functions(cat.name, schema.name))
        table_objects = [
            obj
            for obj in objects
            if obj.object_type
            in {
                ObjectType.TABLE,
                ObjectType.EXTERNAL_TABLE,
                ObjectType.STREAMING_TABLE,
                ObjectType.MATERIALIZED_VIEW,
            }
        ]
        table_locations = [
            str(obj.storage_location)
            for obj in table_objects
            if obj.storage_location
        ]
        locations = list(self._iter_external_locations(table_locations))
        for table in table_objects:
            path = str(table.storage_location or "").rstrip("/")
            matches = [
                location
                for location in locations
                if path == str(location.storage_location or "").rstrip("/")
                or path.startswith(
                    str(location.storage_location or "").rstrip("/") + "/"
                )
            ]
            covering = max(
                matches,
                key=lambda item: len(str(item.storage_location or "")),
                default=None,
            )
            if covering:
                table.external_location_name = covering.name
                table.storage_credential_name = (
                    covering.storage_credential_name
                )
        objects.extend(locations)
        credential_names = {
            str(location.storage_credential_name)
            for location in locations
            if location.storage_credential_name
        }
        objects.extend(self._iter_storage_credentials(credential_names))
        filtered = [o for o in objects if allowed(o, self.cfg)]
        for obj in filtered:
            self._attach_grants(obj)
        return filtered

    def _attach_grants(self, obj: UCObject) -> None:
        if obj.grants:
            return
        if (
            obj.object_type == ObjectType.STORAGE_CREDENTIAL
            and obj.credential_permissions
        ):
            obj.grants = [
                {
                    "principal": assignment.get("principal"),
                    "principal_type": classify_principal(
                        str(assignment.get("principal") or "")
                    ),
                    "privileges": list(assignment.get("privileges") or []),
                }
                for assignment in obj.credential_permissions
                if isinstance(assignment, dict) and assignment.get("principal")
            ]
            return

        securable = SECURABLE_TYPE_FOR_OBJECT.get(obj.object_type)
        if not securable:
            return
        try:
            payload = self.source.get(
                f"/api/2.1/unity-catalog/permissions/{securable}/{obj.full_name}"
            )
            assignments = payload.get("privilege_assignments") or []
        except Exception as exc:  # noqa: BLE001 - keep inventory usable
            assignments = [
                {
                    "principal": "__PERMISSIONS_UNAVAILABLE__",
                    "privileges": [],
                    "error": str(exc),
                }
            ]

        grants = []
        for assignment in assignments:
            if not isinstance(assignment, dict):
                continue
            principal = str(assignment.get("principal") or "").strip()
            if not principal:
                continue
            grants.append(
                {
                    "principal": principal,
                    "principal_type": classify_principal(principal),
                    "privileges": [
                        str(item) for item in (assignment.get("privileges") or [])
                    ],
                    **(
                        {"error": assignment["error"]}
                        if assignment.get("error")
                        else {}
                    ),
                }
            )

        owner = str(obj.owner or "").strip()
        if owner and not any(item.get("principal") == owner for item in grants):
            grants.append(
                {
                    "principal": owner,
                    "principal_type": classify_principal(owner),
                    "privileges": ["OWNER"],
                }
            )
        obj.grants = grants

    def _iter_catalogs(self) -> Iterable[UCObject]:
        for c in self.source.paginate("/api/2.1/unity-catalog/catalogs", "catalogs"):
            if c.get("catalog_type") == "SYSTEM_CATALOG":
                continue
            updated_at, src = _ts(c)
            yield UCObject(
                object_type=ObjectType.CATALOG,
                name=c["name"],
                full_name=c["name"],
                catalog=c["name"],
                object_id=c.get("id"),
                owner=c.get("owner"),
                created_at=c.get("created_at"),
                last_modified_at=updated_at,
                last_modified_source=src,
                definition={
                    k: c.get(k)
                    for k in (
                        "comment",
                        "storage_root",
                        "isolation_mode",
                        "catalog_type",
                        "properties",
                    )
                    if k in c
                },
                source_metadata=c,
            )

    def _iter_schemas(self, catalog: str) -> Iterable[UCObject]:
        for s in self.source.paginate(
            "/api/2.1/unity-catalog/schemas",
            "schemas",
            catalog_name=catalog,
        ):
            updated_at, src = _ts(s)
            yield UCObject(
                object_type=ObjectType.SCHEMA,
                name=s["name"],
                full_name=s.get("full_name") or f"{catalog}.{s['name']}",
                catalog=catalog,
                schema=s["name"],
                owner=s.get("owner"),
                created_at=s.get("created_at"),
                last_modified_at=updated_at,
                last_modified_source=src,
                definition={"comment": s.get("comment"), "properties": s.get("properties")},
                source_metadata=s,
            )

    def _iter_tables(self, catalog: str, schema: str) -> Iterable[UCObject]:
        for t in self.source.paginate(
            "/api/2.1/unity-catalog/tables",
            "tables",
            catalog_name=catalog,
            schema_name=schema,
        ):
            # List responses can omit storage_location, columns, and provider.
            # Fetch each table so inventory and location rewrite reports always
            # contain the authoritative table type and path.
            full_name = t.get("full_name") or f"{catalog}.{schema}.{t['name']}"
            detail = self.source.get(
                f"/api/2.1/unity-catalog/tables/{full_name}"
            )
            t = {**t, **detail}
            updated_at, src = _ts(t)
            table_type = (t.get("table_type") or "MANAGED").upper()
            if table_type in {"VIEW", "METRIC_VIEW"}:
                otype = (
                    ObjectType.METRIC_VIEW
                    if _is_metric_view(t)
                    else (
                        ObjectType.DYNAMIC_VIEW
                        if _is_dynamic_view(t)
                        else ObjectType.VIEW
                    )
                )
            elif table_type == "EXTERNAL":
                otype = ObjectType.EXTERNAL_TABLE
            elif table_type == "MATERIALIZED_VIEW":
                otype = ObjectType.MATERIALIZED_VIEW
            elif table_type == "STREAMING_TABLE":
                otype = ObjectType.STREAMING_TABLE
            else:
                otype = ObjectType.TABLE
            yield UCObject(
                object_type=otype,
                name=t["name"],
                full_name=t.get("full_name") or f"{catalog}.{schema}.{t['name']}",
                catalog=catalog,
                schema=schema,
                object_id=t.get("table_id"),
                owner=t.get("owner"),
                created_at=t.get("created_at"),
                last_modified_at=updated_at,
                last_modified_source=src,
                table_type=table_type,
                data_source_format=t.get("data_source_format"),
                storage_location=t.get("storage_location"),
                definition={
                    "table_type": table_type,
                    "data_source_format": t.get("data_source_format"),
                    "storage_location": t.get("storage_location"),
                    "columns": t.get("columns"),
                    "properties": t.get("properties"),
                    "comment": t.get("comment"),
                    "view_definition": t.get("view_definition"),
                    "view_original_text": t.get("view_original_text"),
                    "view_dependencies": t.get("view_dependencies"),
                    "view_definition_format": t.get("view_definition_format"),
                    "view_type": t.get("view_type"),
                    "view_subtype": t.get("view_subtype"),
                    "view_with_metrics": t.get("view_with_metrics"),
                },
                properties=t.get("properties") or {},
                source_metadata=t,
            )

    def _iter_external_locations(
        self, table_locations: list[str]
    ) -> Iterable[UCObject]:
        configured_names = {
            str(item.get("source_external_location") or "")
            for item in self.mapper.location_mappings()
        }
        configured_roots = {
            str(item.get("source_location") or "").rstrip("/")
            for item in self.mapper.location_mappings()
        }
        legacy_names = set(
            (self.cfg.mappings.get("external_locations") or {}).keys()
        )
        for location in self.source.paginate(
            "/api/2.1/unity-catalog/external-locations",
            "external_locations",
        ):
            name = str(location["name"])
            url = str(location.get("url") or "").rstrip("/")
            covers_table = any(
                path == url or path.startswith(url + "/")
                for path in table_locations
                if url
            )
            configured = (
                name in configured_names
                or name in legacy_names
                or url in configured_roots
            )
            if not covers_table and not configured:
                continue
            resolved = self.mapper.external_location_mapping(
                name,
                url,
                str(location.get("credential_name") or ""),
            )
            if resolved and not self.mapper.location_mapping_for_url(url):
                self.cfg.mappings.setdefault("location_mappings", []).append(
                    resolved
                )
            updated_at, src = _ts(location)
            yield UCObject(
                object_type=ObjectType.EXTERNAL_LOCATION,
                name=name,
                full_name=name,
                object_id=location.get("id"),
                owner=location.get("owner"),
                created_at=location.get("created_at"),
                last_modified_at=updated_at,
                last_modified_source=src,
                storage_location=url,
                external_location_name=name,
                storage_credential_name=location.get("credential_name"),
                definition={
                    "url": url,
                    "credential_name": location.get("credential_name"),
                    "read_only": location.get("read_only"),
                    "comment": location.get("comment"),
                },
                source_metadata=location,
            )

    def _iter_storage_credentials(
        self, credential_names: set[str]
    ) -> Iterable[UCObject]:
        for summary in self.source.paginate(
            "/api/2.1/unity-catalog/storage-credentials",
            "storage_credentials",
        ):
            name = str(summary["name"])
            if name not in credential_names:
                continue
            detail = self.source.get(
                f"/api/2.1/unity-catalog/storage-credentials/{name}"
            )
            credential = {**summary, **detail}
            try:
                permission_data = self.source.get(
                    "/api/2.1/unity-catalog/permissions/"
                    f"storage-credential/{name}"
                )
                permissions = (
                    permission_data.get("privilege_assignments") or []
                )
            except Exception as exc:  # noqa: BLE001 - preserve inventory
                permissions = [
                    {
                        "status": "UNAVAILABLE",
                        "error": str(exc),
                    }
                ]
            owner = credential.get("owner")
            if owner and not any(
                assignment.get("principal") == owner
                for assignment in permissions
                if isinstance(assignment, dict)
            ):
                permissions.append(
                    {
                        "principal": owner,
                        "privileges": ["OWNER"],
                    }
                )
            azure_mi = credential.get("azure_managed_identity") or {}
            aws_role = credential.get("aws_iam_role") or {}
            gcp_sa = credential.get("gcp_service_account_key") or {}
            if azure_mi:
                credential_type = "AZURE_MANAGED_IDENTITY"
            elif aws_role:
                credential_type = "AWS_IAM_ROLE"
            elif gcp_sa:
                credential_type = "GCP_SERVICE_ACCOUNT"
            else:
                credential_type = str(
                    credential.get("securable_kind")
                    or credential.get("credential_type")
                    or "UNKNOWN"
                )
            updated_at, src = _ts(credential)
            yield UCObject(
                object_type=ObjectType.STORAGE_CREDENTIAL,
                name=name,
                full_name=name,
                object_id=credential.get("id"),
                owner=credential.get("owner"),
                created_at=credential.get("created_at"),
                last_modified_at=updated_at,
                last_modified_source=src,
                storage_credential_name=name,
                credential_type=credential_type,
                credential_purpose=str(
                    credential.get("purpose") or "STORAGE"
                ),
                access_connector_id=azure_mi.get("access_connector_id"),
                user_assigned_managed_identity_id=(
                    azure_mi.get("managed_identity_id")
                    or azure_mi.get("user_assigned_managed_identity_id")
                ),
                credential_permissions=permissions,
                definition={
                    "read_only": credential.get("read_only"),
                    "comment": credential.get("comment"),
                    "path_filters": credential.get("path_filters"),
                },
                source_metadata={
                    key: value
                    for key, value in credential.items()
                    if key
                    not in {
                        "azure_service_principal",
                        "databricks_gcp_service_account",
                        "gcp_service_account_key",
                    }
                },
            )

    def _iter_volumes(self, catalog: str, schema: str) -> Iterable[UCObject]:
        for v in self.source.paginate(
            "/api/2.1/unity-catalog/volumes",
            "volumes",
            catalog_name=catalog,
            schema_name=schema,
        ):
            updated_at, src = _ts(v)
            vtype = (v.get("volume_type") or "MANAGED").upper()
            otype = (
                ObjectType.EXTERNAL_VOLUME
                if vtype == "EXTERNAL"
                else ObjectType.VOLUME
            )
            yield UCObject(
                object_type=otype,
                name=v["name"],
                full_name=v.get("full_name") or f"{catalog}.{schema}.{v['name']}",
                catalog=catalog,
                schema=schema,
                owner=v.get("owner"),
                created_at=v.get("created_at"),
                last_modified_at=updated_at,
                last_modified_source=src,
                storage_location=v.get("storage_location"),
                definition={
                    "volume_type": vtype,
                    "storage_location": v.get("storage_location"),
                    "comment": v.get("comment"),
                },
                source_metadata=v,
            )

    def _iter_functions(self, catalog: str, schema: str) -> Iterable[UCObject]:
        for f in self.source.paginate(
            "/api/2.1/unity-catalog/functions",
            "functions",
            catalog_name=catalog,
            schema_name=schema,
        ):
            # The list endpoint returns function summaries and can omit input
            # parameters/routine text. Fetch each function so export/import
            # reports and cross-workspace DDL contain the complete definition.
            full_name = f.get("full_name") or f"{catalog}.{schema}.{f['name']}"
            detail = self.source.get(
                f"/api/2.1/unity-catalog/functions/{full_name}"
            )
            f = {**f, **detail}
            updated_at, src = _ts(f)
            yield UCObject(
                object_type=ObjectType.FUNCTION,
                name=f["name"],
                full_name=f.get("full_name") or full_name,
                catalog=catalog,
                schema=schema,
                owner=f.get("owner"),
                created_at=f.get("created_at"),
                last_modified_at=updated_at,
                last_modified_source=src,
                definition={
                    k: f.get(k)
                    for k in (
                        "input_params",
                        "data_type",
                        "full_data_type",
                        "routine_body",
                        "routine_definition",
                        "parameter_style",
                        "is_deterministic",
                        "comment",
                    )
                    if k in f
                },
                source_metadata=f,
            )
