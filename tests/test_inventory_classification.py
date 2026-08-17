from uc_sync.config import from_sources
from uc_sync.inventory import InventoryService, _is_dynamic_view, _is_metric_view


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
