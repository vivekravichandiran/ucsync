"""Inventory service — discover UC objects from source."""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

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


def _row_filter_from_payload(table: dict) -> dict | None:
    """Extract the directly-defined row filter from a UC Tables REST payload.

    The Tables API returns the directly-defined filter under the top-level
    ``row_filter`` key as ``{function_name, input_column_names}``. Sibling keys
    ``row_filters`` (a wrapper) and ``effective_row_filters`` (which include
    inherited policies) are intentionally ignored so inventory records only what
    is defined on this table.
    """

    raw = table.get("row_filter")
    if isinstance(raw, dict) and raw.get("function_name"):
        return {
            "function_name": raw.get("function_name"),
            "input_column_names": list(raw.get("input_column_names") or []),
        }
    return None


def _column_masks_from_payload(table: dict) -> list[dict]:
    """Extract directly-defined column masks from a UC Tables REST payload.

    Each column carries its directly-defined mask under ``columns[i].mask`` as
    ``{function_name[, using_column_names]}``. The per-column ``column_masks``
    wrapper and ``effective_masks`` (inherited) keys are ignored.
    """

    masks: list[dict] = []
    for column in table.get("columns") or []:
        if not isinstance(column, dict):
            continue
        mask = column.get("mask")
        if isinstance(mask, dict) and mask.get("function_name"):
            masks.append(
                {
                    "column_name": column.get("name"),
                    "function_name": mask.get("function_name"),
                    "using_column_names": list(
                        mask.get("using_column_names") or []
                    ),
                }
            )
    return masks


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
    def __init__(
        self, source: WorkspaceClient, cfg: SyncConfig, sql_executor: object = None
    ):
        self.source = source
        self.cfg = cfg
        self.sql = sql_executor
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
        # Catalog/schema managed-location roots also reference external locations
        # that no table sits directly under (e.g. an EL backing only the catalog
        # root). Feed them into discovery so those ELs are created on the target.
        for obj in objects:
            # Only in-scope catalogs/schemas — _iter_catalogs yields every catalog
            # in the metastore, so an unguarded loop would pull in every catalog's
            # external location.
            if obj.object_type in {ObjectType.CATALOG, ObjectType.SCHEMA} and allowed(
                obj, self.cfg
            ):
                root = (
                    (obj.definition or {}).get("storage_root")
                    or (obj.definition or {}).get("storage_location")
                    or obj.storage_location
                    or (obj.source_metadata or {}).get("storage_root")
                )
                if root:
                    table_locations.append(str(root))
            # External volumes reference an external location by path too.
            if obj.object_type == ObjectType.EXTERNAL_VOLUME and obj.storage_location:
                table_locations.append(str(obj.storage_location))
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
        if self.sql is not None:
            self._attach_governance(filtered)
        # Tier-A AI-asset discovery (task 4): report-only inventory of the UC object
        # types reachable with catalog-scoped privileges (registered models, online
        # tables, vector-search indexes, quality monitors, UC secrets). Appended
        # AFTER the component filter so they always surface ("we checked, you have
        # none" vs "we never looked"); every row carries in_scope_for_migration=false.
        # Migration scope is unchanged — nothing here is ever created on the target.
        tier_a = self._iter_tier_a_assets(catalogs, filtered)
        # Governed-tag definitions (FEAT-2): the account-level tag policies actually
        # used by the in-scope objects, captured so the import can CREATE them on the
        # target before any SET TAGS (idempotent for a same-account target).
        governed = self._iter_governed_tags(filtered)
        return filtered + governed + tier_a

    def _iter_governed_tags(self, objects: list[UCObject]) -> list[UCObject]:
        """Emit a GOVERNED_TAG object per governed tag actually assigned on an
        in-scope object (FEAT-2). Its definition carries the allowed-value list read
        from the source account's tag-policies API. Best-effort: no policies (API
        absent / not permitted) → nothing emitted, and the assign phase is unchanged.
        """
        from uc_sync.governance import read_governed_tag_policies

        policies = read_governed_tag_policies(self.source)
        if not policies:
            return []
        used_keys: set[str] = set()
        for obj in objects:
            for key in (obj.tags or {}):
                used_keys.add(str(key))
            for col_tags in (obj.definition or {}).get("column_tags", {}).values():
                for key in (col_tags or {}):
                    used_keys.add(str(key))
        governed: list[UCObject] = []
        for key in sorted(used_keys):
            if key not in policies:
                continue  # free-form tag — no governed-tag definition to create
            governed.append(
                UCObject(
                    object_type=ObjectType.GOVERNED_TAG,
                    name=key,
                    full_name=key,
                    definition={"allowed_values": policies[key]},
                )
            )
        return governed

    def _iter_tier_a_assets(
        self, catalogs: list[UCObject], in_scope: list[UCObject]
    ) -> list[UCObject]:
        """Best-effort discovery of the report-only Tier-A AI-asset types.

        Every collector is wrapped so a missing privilege / evolving API yields an
        empty result, never a failure (report-only). Discovery walks the same
        in-scope catalog→schema surface as the main inventory. Quality monitors have
        no bulk list endpoint (bug #3), so they are probed **per in-scope table**
        from the objects the main inventory already resolved.
        """
        assets: list[UCObject] = []
        for cat in catalogs:
            if not allowed(cat, self.cfg):
                continue
            for schema in self._iter_schemas(cat.name):
                if not allowed(schema, self.cfg):
                    continue
                for collector in (
                    self._iter_registered_models,
                    self._iter_online_tables,
                    self._iter_vector_indexes,
                    self._iter_uc_secrets,
                ):
                    try:
                        assets.extend(collector(cat.name, schema.name))
                    except Exception as exc:  # noqa: BLE001 - report-only, never fail
                        print(
                            f"[inventory] Tier-A {collector.__name__} skipped for "
                            f"{cat.name}.{schema.name}: {exc!r}"
                        )
        assets.extend(self._iter_quality_monitors(in_scope))
        return assets

    def _tier_a_object(
        self, object_type: ObjectType, full_name: str, **definition: object
    ) -> UCObject:
        parts = full_name.split(".")
        return UCObject(
            object_type=object_type,
            name=parts[-1] if parts else full_name,
            full_name=full_name,
            catalog=parts[0] if parts else None,
            schema=parts[1] if len(parts) > 2 else None,
            definition={"in_scope_for_migration": False, **definition},
        )

    def _iter_registered_models(self, catalog: str, schema: str) -> Iterable[UCObject]:
        for m in self.source.paginate(
            "/api/2.1/unity-catalog/models",
            "registered_models",
            catalog_name=catalog,
            schema_name=schema,
        ):
            full_name = m.get("full_name") or f"{catalog}.{schema}.{m.get('name')}"
            version_count = ""
            try:
                versions = list(
                    self.source.paginate(
                        f"/api/2.1/unity-catalog/models/{full_name}/versions",
                        "model_versions",
                    )
                )
                version_count = len(versions)
            except Exception:  # noqa: BLE001 - version count is best-effort
                version_count = ""
            yield self._tier_a_object(
                ObjectType.MODEL, full_name,
                comment=m.get("comment"), owner=m.get("owner"),
                version_count=version_count,
            )

    def _iter_online_tables(self, catalog: str, schema: str) -> Iterable[UCObject]:
        # Online tables are derived from a source table; there is no per-schema list
        # endpoint, so this is best-effort and typically empty. VERIFY LIVE.
        return []

    # Table-like types a Lakehouse/quality monitor can be attached to. (Streaming
    # tables, MVs and views can also carry monitors; probe the concrete table family.)
    _MONITORABLE_TYPES = {
        ObjectType.TABLE,
        ObjectType.EXTERNAL_TABLE,
        ObjectType.MATERIALIZED_VIEW,
        ObjectType.STREAMING_TABLE,
    }

    def _iter_quality_monitors(self, in_scope: list[UCObject]) -> Iterable[UCObject]:
        """Report-only Lakehouse/quality monitors (bug #3).

        There is **no bulk list** for monitors (the Data Quality API's list is
        currently unimplemented), so probe each in-scope table with the per-object
        get (``GET /api/2.1/unity-catalog/tables/{full_name}/monitor`` — the deprecated
        quality-monitors get, which mirrors ``data-quality get-monitor``). A monitored
        table returns its monitor config; an unmonitored one returns a clean not-found,
        which we skip silently. Report-only: a probe failure never fails inventory.
        """
        for table in in_scope:
            if table.object_type not in self._MONITORABLE_TYPES:
                continue
            monitor = self._probe_table_monitor(table.full_name)
            if not monitor:
                continue
            yield self._tier_a_object(
                ObjectType.MONITOR,
                f"{table.full_name}#monitor",
                monitored_table=table.full_name,
                status=monitor.get("status"),
                monitor_version=monitor.get("monitor_version"),
                assets_dir=monitor.get("assets_dir"),
                output_schema_name=monitor.get("output_schema_name"),
                profile_metrics_table_name=monitor.get("profile_metrics_table_name"),
                drift_metrics_table_name=monitor.get("drift_metrics_table_name"),
            )

    def _probe_table_monitor(self, table_full_name: str) -> Optional[dict[str, Any]]:
        """Return the monitor config for a table, or None if it has none / cannot be
        read. A not-found (no monitor) is the common, expected case and is quiet."""
        try:
            monitor = self.source.get(
                f"/api/2.1/unity-catalog/tables/{table_full_name}/monitor"
            )
        except Exception as exc:  # noqa: BLE001 - not-found (no monitor) is expected
            msg = str(exc)
            if "404" not in msg and "does not exist" not in msg.lower() \
                    and "not found" not in msg.lower() \
                    and "cannot find" not in msg.lower():
                # A genuine error (e.g. a permission gap) — note it, but never fail
                # inventory (monitors are report-only).
                print(
                    f"[inventory] monitor probe skipped for {table_full_name}: {exc!r}"
                )
            return None
        return monitor if isinstance(monitor, dict) and monitor else None

    def _iter_vector_indexes(self, catalog: str, schema: str) -> Iterable[UCObject]:
        # Vector-search indexes are listed per endpoint, not per schema. VERIFY LIVE.
        return []

    def _iter_uc_secrets(self, catalog: str, schema: str) -> Iterable[UCObject]:
        # UC (schema-level) secrets are a newer API distinct from workspace secret
        # scopes. VERIFY LIVE. Report-only, best-effort empty by default.
        return []

    def _attach_governance(self, objects: list[UCObject]) -> None:
        """Attach governed-tag assignments and inventory ABAC policies via SQL."""

        from uc_sync.governance import read_abac_policies, read_tags

        catalogs = sorted(
            {
                obj.catalog or obj.name
                for obj in objects
                if obj.object_type == ObjectType.CATALOG
            }
        )
        for catalog in catalogs:
            try:
                tags = read_tags(self.sql, catalog)
            except Exception as exc:  # noqa: BLE001 - keep inventory usable
                print(f"[inventory] tag read failed for {catalog}: {exc!r}")
                tags = {"objects": {}, "columns": {}}
            object_tags = tags.get("objects", {})
            column_tags = tags.get("columns", {})
            for obj in objects:
                if obj.full_name in object_tags:
                    obj.tags = {**(obj.tags or {}), **object_tags[obj.full_name]}
                if obj.full_name in column_tags:
                    obj.definition = {
                        **(obj.definition or {}),
                        "column_tags": column_tags[obj.full_name],
                    }
            try:
                policies = read_abac_policies(self.sql, catalog)
            except Exception as exc:  # noqa: BLE001
                print(f"[inventory] ABAC read failed for {catalog}: {exc!r}")
                policies = []
            objects.extend(policies)

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
            data_source_format = str(t.get("data_source_format") or "").upper()
            report_only_foreign = False
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
            elif table_type == "FOREIGN":
                # FOREIGN objects only LOOK like tables and cannot be recreated with
                # ordinary table SQL (bug #6): a Lakebase-synced table (Postgres copy,
                # POSTGRESQL_FORMAT) or a Vector Search index (VECTOR_INDEX_FORMAT).
                # Classify to their own report-only types so they are never sent to
                # SHOW CREATE / CREATE; any other FOREIGN format is reported too.
                report_only_foreign = True
                if data_source_format == "VECTOR_INDEX_FORMAT":
                    otype = ObjectType.VECTOR_INDEX
                else:
                    otype = ObjectType.LAKEBASE_TABLE
            else:
                otype = ObjectType.TABLE
            # Bug #8: a table owned by a DLT/SDP/Kafka pipeline carries a non-null
            # top-level pipeline_id (event-log table or pipeline output). It cannot
            # be recreated with ordinary table SQL (UC demands a managing pipeline;
            # event-log schemas don't exist on the target). Reclassify a plain
            # pipeline-managed table to a report-only type; pipeline-output MVs /
            # streaming tables are already report-only via their own types.
            pipeline_id = t.get("pipeline_id")
            report_only_pipeline = False
            if pipeline_id and otype in {ObjectType.TABLE, ObjectType.EXTERNAL_TABLE}:
                otype = ObjectType.PIPELINE_TABLE
                report_only_pipeline = True
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
                    "row_filter": _row_filter_from_payload(t),
                    "column_masks": _column_masks_from_payload(t),
                    # FOREIGN (Lakebase-synced / Vector Search, bug #6) and pipeline-
                    # managed (bug #8) objects are reported, never migrated.
                    **(
                        {"in_scope_for_migration": False}
                        if (report_only_foreign or report_only_pipeline)
                        else {}
                    ),
                    **({"pipeline_id": pipeline_id} if pipeline_id else {}),
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
