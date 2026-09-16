from uc_sync.config import from_sources
from uc_sync.inventory import InventoryService, _is_dynamic_view, _is_metric_view
from uc_sync.models import ObjectType


class _ForeignTablesSource:
    """A source with a Lakebase-synced table, a Vector Search index, and a plain
    managed table (bug #6). List + per-object get both return the same rows."""

    _ROWS = {
        "c.s.lakebase_sync": {
            "name": "lakebase_sync", "full_name": "c.s.lakebase_sync",
            "table_type": "FOREIGN", "data_source_format": "POSTGRESQL_FORMAT",
        },
        "c.s.vs_index": {
            "name": "vs_index", "full_name": "c.s.vs_index",
            "table_type": "FOREIGN", "data_source_format": "VECTOR_INDEX_FORMAT",
        },
        "c.s.orders": {
            "name": "orders", "full_name": "c.s.orders",
            "table_type": "MANAGED", "data_source_format": "DELTA",
        },
    }

    def paginate(self, path, items_key=None, **q):
        if path.endswith("/tables"):
            return iter(list(self._ROWS.values()))
        return iter([])

    def get(self, path, **q):
        return self._ROWS.get(path.rsplit("/tables/", 1)[-1], {})


def test_foreign_tables_classified_report_only():
    cfg = from_sources({"execution_mode": "LOCAL", "catalogs": "c"})
    tables = list(InventoryService(_ForeignTablesSource(), cfg)._iter_tables("c", "s"))
    by_name = {t.full_name: t for t in tables}
    assert by_name["c.s.lakebase_sync"].object_type == ObjectType.LAKEBASE_TABLE
    assert by_name["c.s.lakebase_sync"].definition["in_scope_for_migration"] is False
    assert by_name["c.s.vs_index"].object_type == ObjectType.VECTOR_INDEX
    assert by_name["c.s.vs_index"].definition["in_scope_for_migration"] is False
    # A plain managed table is unaffected and remains in scope.
    assert by_name["c.s.orders"].object_type == ObjectType.TABLE
    assert "in_scope_for_migration" not in by_name["c.s.orders"].definition


class _PipelineTablesSource:
    """A source with a pipeline event-log table (non-null pipeline_id), a pipeline
    output table (also pipeline_id), and a plain table (bug #8)."""

    _ROWS = {
        "c.s.event_log": {
            "name": "event_log", "full_name": "c.s.event_log",
            "table_type": "MANAGED", "data_source_format": "DELTA",
            "pipeline_id": "pl-1234",
        },
        "c.s.orders": {
            "name": "orders", "full_name": "c.s.orders",
            "table_type": "MANAGED", "data_source_format": "DELTA",
            "pipeline_id": None,
        },
    }

    def paginate(self, path, items_key=None, **q):
        if path.endswith("/tables"):
            return iter(list(self._ROWS.values()))
        return iter([])

    def get(self, path, **q):
        return self._ROWS.get(path.rsplit("/tables/", 1)[-1], {})


def test_pipeline_managed_table_classified_report_only():
    cfg = from_sources({"execution_mode": "LOCAL", "catalogs": "c"})
    tables = list(InventoryService(_PipelineTablesSource(), cfg)._iter_tables("c", "s"))
    by_name = {t.full_name: t for t in tables}
    assert by_name["c.s.event_log"].object_type == ObjectType.PIPELINE_TABLE
    assert by_name["c.s.event_log"].definition["in_scope_for_migration"] is False
    assert by_name["c.s.event_log"].definition["pipeline_id"] == "pl-1234"
    # A plain table (pipeline_id empty) is migrated normally.
    assert by_name["c.s.orders"].object_type == ObjectType.TABLE
    assert "in_scope_for_migration" not in by_name["c.s.orders"].definition


def test_foreign_types_never_capture_ddl():
    """Bug #6: report-only FOREIGN objects are never sent to SHOW CREATE / synthesis."""
    from uc_sync.export import ExportService
    from uc_sync.models import UCObject

    objs = [
        UCObject(object_type=ObjectType.LAKEBASE_TABLE, name="lb",
                 full_name="c.s.lb", definition={"in_scope_for_migration": False}),
        UCObject(object_type=ObjectType.VECTOR_INDEX, name="vi",
                 full_name="c.s.vi", definition={"in_scope_for_migration": False}),
    ]
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        result = ExportService(f"{d}/v", "r1", workspace_root=f"{d}/w").run(
            objs, dry_run=False
        )
    # No DDL captured, and neither is a hard ERROR (they are report-only, not failures).
    assert result["ddl_files"] == 0
    assert all(r["status"] != "ERROR" for r in result["results"])


def test_identity_aware_view_is_dynamic():
    assert _is_dynamic_view(
        {"view_definition": "SELECT * FROM t WHERE owner = current_user()"}
    )


def test_group_aware_view_is_dynamic():
    assert _is_dynamic_view(
        {
            "view_original_text": (
                "SELECT * FROM t WHERE is_account_group_member('admins')"
            )
        }
    )


def test_regular_view_is_not_dynamic():
    assert not _is_dynamic_view({"view_definition": "SELECT id FROM t"})


def test_metric_view_is_detected_by_table_type():
    assert _is_metric_view({"table_type": "METRIC_VIEW"})


def test_metric_view_is_detected_by_rest_markers():
    assert _is_metric_view(
        {
            "table_type": "VIEW",
            "view_definition_format": "YAML",
            "properties": {"view.subType": "METRIC_VIEW"},
        }
    )


def test_regular_view_is_not_metric_view():
    assert not _is_metric_view(
        {"table_type": "VIEW", "view_definition": "SELECT id FROM t"}
    )


def test_function_inventory_fetches_complete_definition():
    class Source:
        def paginate(self, *_args, **_kwargs):
            return iter(
                [
                    {
                        "name": "add_one",
                        "full_name": "source.analytics.add_one",
                    }
                ]
            )

        def get(self, path):
            assert path.endswith("/source.analytics.add_one")
            return {
                "input_params": {
                    "parameters": [{"name": "value", "type_text": "bigint"}]
                },
                "full_data_type": "BIGINT",
                "routine_definition": "value + 1",
            }

    cfg = from_sources(
        {
            "execution_mode": "LOCAL",
            "catalog_mapping_json": '{"source":"target"}',
        }
    )
    function = list(
        InventoryService(Source(), cfg)._iter_functions("source", "analytics")
    )[0]
    assert function.definition["input_params"]["parameters"][0]["name"] == "value"
    assert function.definition["routine_definition"] == "value + 1"


def test_storage_credential_inventory_includes_connector_and_permissions():
    class Source:
        def paginate(self, path, *_args, **_kwargs):
            assert path.endswith("/storage-credentials")
            return iter([{"name": "target_credential"}])

        def get(self, path):
            if "/permissions/storage-credential/" in path:
                return {
                    "privilege_assignments": [
                        {
                            "principal": "admins",
                            "privileges": ["CREATE EXTERNAL LOCATION"],
                        }
                    ]
                }
            assert path.endswith("/storage-credentials/target_credential")
            return {
                "name": "target_credential",
                "securable_kind": "STORAGE_CREDENTIAL_AZURE_MI",
                "azure_managed_identity": {
                    "access_connector_id": (
                        "/subscriptions/s/resourceGroups/r/providers/"
                        "Microsoft.Databricks/accessConnectors/c"
                    ),
                    "managed_identity_id": "uami-id",
                },
                "read_only": False,
            }

    cfg = from_sources(
        {
            "execution_mode": "LOCAL",
            "catalog_mapping_json": '{"source":"target"}',
        }
    )
    credential = list(
        InventoryService(Source(), cfg)._iter_storage_credentials(
            {"target_credential"}
        )
    )[0]

    assert credential.credential_type == "AZURE_MANAGED_IDENTITY"
    assert credential.credential_purpose == "STORAGE"
    assert credential.access_connector_id.endswith("/accessConnectors/c")
    assert credential.user_assigned_managed_identity_id == "uami-id"
    assert credential.credential_permissions[0]["principal"] == "admins"
