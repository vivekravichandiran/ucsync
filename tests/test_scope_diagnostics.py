"""Tests for schema scoping aliases, streaming tables, and scope diagnostics."""

from __future__ import annotations

from uc_sync.components import resolve_components, undiscoverable_types
from uc_sync.config import from_sources
from uc_sync.filters import allowed, schema_selected
from uc_sync.inventory import InventoryService
from uc_sync.models import ObjectType, UCObject


class FakeClient:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, path, key, **params):
        for item in self.pages.get(key, []):
            if key == "schemas" and item["catalog_name"] != params.get("catalog_name"):
                continue
            yield item

    def get(self, path):
        return {}


def _cfg(**widgets):
    return from_sources(
        {
            "catalogs": "ril_sandbox",
            "catalog_mapping_json": '{"ril_sandbox":"target"}',
            **widgets,
        },
        {},
    )


def test_bare_schema_name_selects_objects():
    cfg = _cfg(schemas="edge")
    schema = UCObject(
        ObjectType.SCHEMA, "edge", "ril_sandbox.edge", catalog="ril_sandbox"
    )
    table = UCObject(
        ObjectType.TABLE,
        "t1",
        "ril_sandbox.edge.t1",
        catalog="ril_sandbox",
        schema="edge",
    )
    other = UCObject(
        ObjectType.TABLE,
        "t2",
        "ril_sandbox.other.t2",
        catalog="ril_sandbox",
        schema="other",
    )

    assert allowed(schema, cfg)
    assert allowed(table, cfg)
    assert not allowed(other, cfg)


def test_qualified_schema_name_still_selects_objects():
    cfg = _cfg(schemas="ril_sandbox.edge")
    table = UCObject(
        ObjectType.TABLE,
        "t1",
        "ril_sandbox.edge.t1",
        catalog="ril_sandbox",
        schema="edge",
    )

    assert allowed(table, cfg)
    assert schema_selected(["ril_sandbox.edge"], "ril_sandbox", "edge")
    assert not schema_selected(["ril_sandbox.edge"], "other", "edge")


def test_streaming_table_is_classified_from_table_type():
    client = FakeClient(
        {
            "catalogs": [{"name": "ril_sandbox"}],
            "schemas": [{"name": "edge", "catalog_name": "ril_sandbox"}],
            "tables": [
                {
                    "name": "st1",
                    "full_name": "ril_sandbox.edge.st1",
                    "table_type": "STREAMING_TABLE",
                    "columns": [],
                },
                {
                    "name": "mt1",
                    "full_name": "ril_sandbox.edge.mt1",
                    "table_type": "MANAGED",
                    "columns": [],
                },
            ],
            "volumes": [],
            "functions": [],
        }
    )

    objects = InventoryService(client, _cfg(schemas="edge")).run()
    by_name = {obj.name: obj.object_type for obj in objects}

    assert by_name["st1"] == ObjectType.STREAMING_TABLE
    assert by_name["mt1"] == ObjectType.TABLE


def test_undiscoverable_types_flags_unsupported_selections():
    assert undiscoverable_types(resolve_components("models")) == ["MODEL"]
    assert undiscoverable_types(resolve_components("grants")) == ["GRANT"]
    assert undiscoverable_types(resolve_components("tables_views")) == []
    assert undiscoverable_types(resolve_components("ALL")) == []
    unsupported_storage = undiscoverable_types(resolve_components("storage"))
    assert "STORAGE_CREDENTIAL" not in unsupported_storage
    assert "SERVICE_CREDENTIAL" in unsupported_storage
