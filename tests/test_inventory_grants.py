"""Tests for inventory grant attachment and principal classification."""

from __future__ import annotations

from types import SimpleNamespace

from uc_sync.inventory import InventoryService, classify_principal
from uc_sync.models import ObjectType, UCObject


def test_classify_principal():
    assert classify_principal("alice@example.com") == "USER"
    assert classify_principal("data_engineers") == "GROUP"
    assert (
        classify_principal("11111111-2222-3333-4444-555555555555")
        == "SERVICE_PRINCIPAL"
    )


def test_attach_grants_fetches_permissions_and_injects_owner():
    class Client:
        def get(self, path):
            assert path.endswith("/permissions/table/c.s.t")
            return {
                "privilege_assignments": [
                    {"principal": "readers", "privileges": ["SELECT"]}
                ]
            }

    service = InventoryService(Client(), cfg=SimpleNamespace(mappings={}))
    obj = UCObject(
        object_type=ObjectType.TABLE,
        name="t",
        full_name="c.s.t",
        owner="alice@example.com",
    )
    service._attach_grants(obj)
    principals = {item["principal"]: item for item in obj.grants}
    assert principals["readers"]["privileges"] == ["SELECT"]
    assert principals["readers"]["principal_type"] == "GROUP"
    assert principals["alice@example.com"]["privileges"] == ["OWNER"]
    assert principals["alice@example.com"]["principal_type"] == "USER"


def test_attach_grants_reuses_storage_credential_permissions():
    service = InventoryService(SimpleNamespace(), cfg=SimpleNamespace(mappings={}))
    obj = UCObject(
        object_type=ObjectType.STORAGE_CREDENTIAL,
        name="cred",
        full_name="cred",
        credential_permissions=[
            {
                "principal": "11111111-2222-3333-4444-555555555555",
                "privileges": ["CREATE EXTERNAL LOCATION"],
            }
        ],
    )
    service._attach_grants(obj)
    assert len(obj.grants) == 1
    assert obj.grants[0]["principal_type"] == "SERVICE_PRINCIPAL"
