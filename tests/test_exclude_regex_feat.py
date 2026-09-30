"""Unit tests for the exclude_regex table-exclusion widget (backlog item 2)."""

import json
from pathlib import Path

from uc_sync.config import from_sources
from uc_sync.filters import allowed
from uc_sync.install_jobs import load_job_spec
from uc_sync.models import ObjectType, UCObject


def _tbl(cat, sch, name):
    return UCObject(ObjectType.TABLE, name, f"{cat}.{sch}.{name}", catalog=cat, schema=sch)


def test_from_sources_single_regex():
    cfg = from_sources({"catalogs": "c", "exclude_regex": r".*_TEMP$"})
    assert cfg.exclude_regex == [r".*_TEMP$"]


def test_from_sources_csv_regex_splits_into_independent_patterns():
    cfg = from_sources({"catalogs": "c",
                        "exclude_regex": r".*_TEMP$, sales\.public\.orders_raw$"})
    assert cfg.exclude_regex == [r".*_TEMP$", r"sales\.public\.orders_raw$"]


def test_from_sources_blank_regex_is_empty_no_op():
    cfg = from_sources({"catalogs": "c", "exclude_regex": ""})
    assert cfg.exclude_regex == []


def test_allowed_drops_matching_tables_keeps_rest_and_parents():
    cfg = from_sources({"catalogs": "sales", "exclude_regex": r".*_TEMP$"})
    keep = _tbl("sales", "public", "orders")
    drop = _tbl("sales", "public", "orders_TEMP")
    catalog = UCObject(ObjectType.CATALOG, "sales", "sales", catalog="sales")
    schema = UCObject(ObjectType.SCHEMA, "public", "sales.public",
                      catalog="sales", schema="public")
    assert allowed(keep, cfg) is True
    assert allowed(drop, cfg) is False
    # Parents are not caught by a .*_TEMP$ table pattern (include_parents unaffected).
    assert allowed(catalog, cfg) is True
    assert allowed(schema, cfg) is True


def test_allowed_multiple_regexes_each_independent():
    cfg = from_sources(
        {"catalogs": "sales",
         "exclude_regex": r".*_TEMP$, sales\.public\.orders_raw$"})
    assert allowed(_tbl("sales", "public", "orders_raw"), cfg) is False
    assert allowed(_tbl("sales", "public", "orders_TEMP"), cfg) is False
    assert allowed(_tbl("sales", "public", "orders"), cfg) is True


def test_allowed_bare_substring_matches_as_documented():
    # A bare `orders` substring-matches orders_archive (documented caveat).
    cfg = from_sources({"catalogs": "sales", "exclude_regex": r"orders"})
    assert allowed(_tbl("sales", "public", "orders_archive"), cfg) is False


def _values(**over):
    v = {
        "output_volume_path": "/Volumes/c/s/v",
        "ops_catalog": "ops", "ops_schema": "sync",
        "notebook_dir": "/Workspace/uc",
        "exclude_regex": r".*_TEMP$",
    }
    v.update(over)
    return v


def _inventory_task_params(spec):
    for t in spec["tasks"]:
        bp = t.get("notebook_task", {}).get("base_parameters") or {}
        if "catalogs" in bp:  # the 01_Inventory task
            return bp
    return None


def test_inventory_specs_carry_substituted_exclude_regex():
    for key in ("airgap_source", "e2e_dry_run", "e2e_live"):
        spec = load_job_spec(key, _values())
        params = _inventory_task_params(spec)
        assert params is not None, key
        assert params["exclude_regex"] == r".*_TEMP$", key


def test_import_only_spec_has_no_exclude_regex():
    spec = load_job_spec("airgap_import_target", _values())
    for t in spec["tasks"]:
        bp = t.get("notebook_task", {}).get("base_parameters") or {}
        assert "exclude_regex" not in bp
