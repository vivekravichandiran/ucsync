"""Tests for component presets and include-object-type filtering."""

from __future__ import annotations

from uc_sync.components import resolve_components
from uc_sync.config import from_sources
from uc_sync.filters import allowed
from uc_sync.models import ObjectType, UCObject


def test_resolve_tables_preset_includes_parents():
    types = resolve_components("tables")
    assert "TABLE" in types
    assert "EXTERNAL_TABLE" in types
    assert "CATALOG" in types
    assert "SCHEMA" in types
    assert "VIEW" not in types


def test_resolve_tables_views_and_plus_syntax():
    a = set(resolve_components("tables_views", include_parents=False))
    b = set(resolve_components("tables+views", include_parents=False))
    assert a == b
    assert {
        "TABLE",
        "VIEW",
        "DYNAMIC_VIEW",
        "METRIC_VIEW",
        "EXTERNAL_TABLE",
    } <= a


def test_resolve_dynamic_views_only():
    types = resolve_components("dynamic_views", include_parents=False)
    assert types == ["DYNAMIC_VIEW"]


def test_resolve_explicit_csv():
    types = resolve_components("TABLE,VIEW,FUNCTION", include_parents=False)
    assert types == ["FUNCTION", "TABLE", "VIEW"]


def test_config_components_wire_into_include_object_types():
    cfg = from_sources(
        {
            "execution_mode": "LOCAL",
            "catalog_mapping_json": '{"src":"tgt"}',
            "components": "tables+views",
        }
    )
    assert cfg.components == "tables+views"
    assert "TABLE" in cfg.include_object_types
    assert "VIEW" in cfg.include_object_types
    assert "CATALOG" in cfg.include_object_types


def test_filter_honors_component_include_list():
    cfg = from_sources(
        {
            "execution_mode": "LOCAL",
            "catalog_mapping_json": '{"ril_sandbox":"ril_sandbox_copy"}',
            "components": "tables",
        }
    )
    table = UCObject(
        ObjectType.TABLE,
        "t1",
        "ril_sandbox.edge.t1",
        catalog="ril_sandbox",
        schema="edge",
    )
    view = UCObject(
        ObjectType.VIEW,
        "v1",
        "ril_sandbox.edge.v1",
        catalog="ril_sandbox",
        schema="edge",
    )
    schema = UCObject(
        ObjectType.SCHEMA,
        "edge",
        "ril_sandbox.edge",
        catalog="ril_sandbox",
        schema="edge",
    )
    assert allowed(table, cfg) is True
    assert allowed(schema, cfg) is True
    assert allowed(view, cfg) is False


def test_unknown_component_raises():
    try:
        resolve_components("not_a_real_component")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Unknown component" in str(exc)
