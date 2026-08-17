"""Unit tests for config merge and filters (no Databricks runtime required)."""

import json

from uc_sync.config import from_sources, parse_catalog_mapping
from uc_sync.filters import allowed
from uc_sync.models import ObjectType, UCObject


def test_widgets_override_yaml_catalogs():
    cfg = from_sources(
        {"catalogs": "ril_sandbox", "mode": "EXPORT", "dry_run": "true"},
        {
            "selection": {"catalogs": ["ril_raw", "ril_curated"]},
            "runtime": {"dry_run": False, "execution_mode": "CROSS_WORKSPACE"},
        },
    )
    assert cfg.catalogs == ["ril_sandbox"]
    assert cfg.mode == "EXPORT"
    assert cfg.dry_run is True


def test_filter_skips_information_schema_and_system():
    cfg = from_sources(
        {
            "catalogs": "ril_sandbox",
            "catalog_mapping_json": '{"ril_sandbox":"ril_sandbox_copy"}',
        },
        {},
    )
    sys_cat = UCObject(ObjectType.CATALOG, "system", "system", catalog="system")
    info = UCObject(
        ObjectType.SCHEMA,
        "information_schema",
        "ril_sandbox.information_schema",
        catalog="ril_sandbox",
        schema="information_schema",
    )
    ok = UCObject(
        ObjectType.TABLE,
        "t1",
        "ril_sandbox.edge.t1",
        catalog="ril_sandbox",
        schema="edge",
    )
    assert allowed(sys_cat, cfg) is False
    assert allowed(info, cfg) is False
    assert allowed(ok, cfg) is True


def test_local_catalog_mapping_drives_source_selection():
    cfg = from_sources(
        {
            "execution_mode": "LOCAL",
            "catalog_mapping_json": json.dumps(
                {"catalogs": {"source_a": "target_a", "source_b": "target_b"}}
            ),
        }
    )
    assert cfg.catalog_mapping == {
        "source_a": "target_a",
        "source_b": "target_b",
    }
    assert cfg.catalogs == ["source_a", "source_b"]
    assert not cfg.source_workspace_url
    assert not cfg.target_workspace_url


def test_mapping_path(tmp_path):
    path = tmp_path / "mapping.json"
    path.write_text('{"catalogs":{"one":"one_copy"}}', encoding="utf-8")
    assert parse_catalog_mapping(json_path=str(path)) == {"one": "one_copy"}
