"""Existing-catalog (Mode B) replication: object-locations config + auto-detect.

Covers the schema / external-volume / external-table location config, the
auto-detected existing-catalog mode (skip SC/EL/catalog creation when the mapped
target catalog already exists), and the catalog-rename safety (hyphens, no
double-rewrite).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uc_sync.location_mapping import (
    ObjectLocations,
    load_object_locations_csv,
    parse_object_locations,
)
from uc_sync.package_import import PackageImportEngine, rewrite_catalog_references


# --------------------------------------------------------------------------- #
# Fake SQL executor that models catalog existence for mode detection.
# --------------------------------------------------------------------------- #
class RecordingSql:
    def __init__(self, existing_catalogs: tuple[str, ...] = ()):
        self.statements: list[str] = []
        self.existing = {c for c in existing_catalogs}

    def execute(self, sql: str):
        self.statements.append(sql)
        upper = sql.strip().upper()
        if upper.startswith("DESCRIBE CATALOG"):
            name = sql.split()[-1].strip().strip("`;")
            if name not in self.existing:
                raise RuntimeError(f"NOT_FOUND: catalog `{name}` does not exist")
        return None


def _write_bundle(root: Path, files: dict[str, str]) -> None:
    (root / "ddl").mkdir(parents=True)
    (root / "inventory").mkdir()
    (root / "inventory" / "objects.json").write_text("[]", encoding="utf-8")
    for name, text in files.items():
        (root / "ddl" / name).write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Config loader
# --------------------------------------------------------------------------- #
def test_parse_object_locations_classifies_rows():
    ol = parse_object_locations([
        {"schema": "crm", "volume": "", "table": "", "location": "abfss://a/crm/"},
        {"schema": "orders", "volume": "archive", "table": "", "location": "abfss://a/arch"},
        {"schema": "sales", "volume": "", "table": "raw", "location": "abfss://a/raw"},
        {"schema": "", "volume": "", "table": "", "location": ""},  # blank row ignored
    ])
    assert ol.schema_location("crm") == "abfss://a/crm"  # trailing slash trimmed
    assert ol.volume_location("orders", "archive") == "abfss://a/arch"
    assert ol.table_location("sales", "raw") == "abfss://a/raw"
    assert ol.schema_location("orders") is None  # only a volume row
    assert bool(ol) is True
    assert bool(ObjectLocations({}, {}, {})) is False


def test_parse_object_locations_rejects_bad_rows():
    with pytest.raises(ValueError, match="both a schema and a location"):
        parse_object_locations([{"schema": "crm", "location": ""}])
    with pytest.raises(ValueError, match="both volume and table"):
        parse_object_locations(
            [{"schema": "s", "volume": "v", "table": "t", "location": "abfss://a"}]
        )


def test_load_object_locations_csv(tmp_path: Path):
    csv_path = tmp_path / "object_locations.csv"
    csv_path.write_text(
        "schema,volume,table,location\n"
        "crm,,,abfss://a/crm\n"
        "orders,archive,,abfss://a/orders/archive\n",
        encoding="utf-8",
    )
    ol = load_object_locations_csv(str(csv_path))
    assert ol.schema_location("crm") == "abfss://a/crm"
    assert ol.volume_location("orders", "archive") == "abfss://a/orders/archive"


# --------------------------------------------------------------------------- #
# Existing-catalog mode detection
# --------------------------------------------------------------------------- #
def test_existing_catalog_mode_skips_sc_el_catalog(tmp_path: Path):
    root = tmp_path / "migrated"
    _write_bundle(root, {
        "STORAGE_CREDENTIAL_src_cred.sql":
            "CREATE STORAGE CREDENTIAL src_cred "
            "WITH (AZURE_MANAGED_IDENTITY ACCESS_CONNECTOR_ID = '/c');",
        "EXTERNAL_LOCATION_src_el.sql":
            "CREATE EXTERNAL LOCATION src_el URL 'abfss://a/' "
            "WITH (STORAGE CREDENTIAL src_cred);",
        "CATALOG_src.sql": "CREATE CATALOG src;",
        "SCHEMA_src__crm.sql": "CREATE SCHEMA src.crm;",
    })
    sql = RecordingSql(existing_catalogs=("tgt",))
    results = PackageImportEngine(
        str(root), sql, dry_run=False,
        catalog_mapping={"src": "tgt"}, workspace_client=object(),
    ).run()
    by = {r.object_type: r for r in results}
    assert by["STORAGE_CREDENTIAL"].action == "SKIP_CREATE_DISABLED"
    assert by["EXTERNAL_LOCATION"].action == "SKIP_CREATE_DISABLED"
    assert by["CATALOG"].action == "SKIP_CREATE_DISABLED"
    # Catalog/SC/EL creation never reached the executor; the schema did.
    assert not any("CREATE CATALOG" in s.upper() for s in sql.statements)
    assert not any("CREATE STORAGE CREDENTIAL" in s.upper() for s in sql.statements)
    assert any("CREATE SCHEMA" in s.upper() for s in sql.statements)
    assert by["SCHEMA"].status == "SUCCESS"


def test_mode_a_when_target_catalog_absent(tmp_path: Path):
    root = tmp_path / "migrated"
    _write_bundle(root, {
        "CATALOG_src.sql": "CREATE CATALOG src;",
        "SCHEMA_src__crm.sql": "CREATE SCHEMA src.crm;",
    })
    sql = RecordingSql(existing_catalogs=())  # target does not exist -> Mode A
    results = PackageImportEngine(
        str(root), sql, dry_run=False,
        catalog_mapping={"src": "tgt"}, workspace_client=object(),
    ).run()
    by = {r.object_type: r for r in results}
    assert by["CATALOG"].action in ("CREATE_OR_SKIP", "SKIP_EXISTING")
    assert any("CREATE CATALOG" in s.upper() and "tgt" in s for s in sql.statements)


def test_no_workspace_client_never_enters_mode_b(tmp_path: Path):
    """Pure-SQL unit runs (no workspace client) keep Mode A even if the fake
    executor would report the catalog as existing."""
    root = tmp_path / "migrated"
    _write_bundle(root, {"CATALOG_src.sql": "CREATE CATALOG src;"})
    sql = RecordingSql(existing_catalogs=("tgt",))
    engine = PackageImportEngine(
        str(root), sql, dry_run=False, catalog_mapping={"src": "tgt"},
    )
    engine.run()
    assert engine._existing_catalog_mode is False


# --------------------------------------------------------------------------- #
# Schema managed location
# --------------------------------------------------------------------------- #
def test_schema_managed_location_injected_and_default_root(tmp_path: Path):
    root = tmp_path / "migrated"
    _write_bundle(root, {
        "SCHEMA_tgt__crm.sql": "CREATE SCHEMA tgt.crm COMMENT 'crm';",
        "SCHEMA_tgt__default.sql": "CREATE SCHEMA tgt.default;",
    })
    ol = ObjectLocations({"crm": "abfss://a/crm"}, {}, {})
    sql = RecordingSql()
    PackageImportEngine(str(root), sql, dry_run=False, object_locations=ol).run()
    schema_stmts = [s for s in sql.statements if "CREATE SCHEMA" in s.upper()]
    crm = next(s for s in schema_stmts if "crm" in s)
    default = next(s for s in schema_stmts if "default" in s)
    assert "MANAGED LOCATION 'abfss://a/crm'" in crm
    assert "COMMENT 'crm'" in crm  # comment preserved, clause inserted before it
    assert "MANAGED LOCATION" not in default.upper()  # unlisted -> catalog root


# --------------------------------------------------------------------------- #
# External volume / table location
# --------------------------------------------------------------------------- #
def test_external_volume_location_replaced(tmp_path: Path):
    root = tmp_path / "migrated"
    _write_bundle(root, {
        "EXTERNAL_VOLUME_tgt__orders__archive.sql":
            "CREATE EXTERNAL VOLUME tgt.orders.archive "
            "LOCATION 'abfss://source/orders/archive';",
    })
    ol = ObjectLocations({}, {("orders", "archive"): "abfss://target/orders/archive"}, {})
    sql = RecordingSql()
    PackageImportEngine(str(root), sql, dry_run=False, object_locations=ol).run()
    vol = next(s for s in sql.statements if "EXTERNAL VOLUME" in s.upper())
    assert "LOCATION 'abfss://target/orders/archive'" in vol
    assert "source" not in vol


def test_external_table_location_replaced(tmp_path: Path):
    root = tmp_path / "migrated"
    _write_bundle(root, {
        "EXTERNAL_TABLE_tgt__sales__raw.sql":
            "CREATE TABLE tgt.sales.raw (id INT) USING DELTA "
            "LOCATION 'abfss://source/raw';",
    })
    ol = ObjectLocations({}, {}, {("sales", "raw"): "abfss://target/raw"})
    sql = RecordingSql()
    PackageImportEngine(str(root), sql, dry_run=False, object_locations=ol).run()
    tbl = next(s for s in sql.statements if "USING DELTA" in s.upper())
    assert "LOCATION 'abfss://target/raw'" in tbl


def test_external_object_without_location_is_manual_in_mode_b(tmp_path: Path):
    """In existing-catalog mode an external object with no configured location is
    MANUAL_ACTION_REQUIRED (not a crash), and the rest of the import proceeds."""
    root = tmp_path / "migrated"
    _write_bundle(root, {
        "CATALOG_src.sql": "CREATE CATALOG src;",
        "SCHEMA_src__orders.sql": "CREATE SCHEMA src.orders;",
        "EXTERNAL_VOLUME_src__orders__archive.sql":
            "CREATE EXTERNAL VOLUME src.orders.archive LOCATION 'abfss://source/x';",
    })
    sql = RecordingSql(existing_catalogs=("tgt",))
    results = PackageImportEngine(
        str(root), sql, dry_run=False,
        catalog_mapping={"src": "tgt"}, workspace_client=object(),
    ).run()
    by = {r.object_type: r for r in results}
    assert by["EXTERNAL_VOLUME"].status == "MANUAL_ACTION_REQUIRED"
    assert by["EXTERNAL_VOLUME"].error_code == "EXTERNAL_LOCATION_MISSING"
    assert "object-locations" in by["EXTERNAL_VOLUME"].message
    # The external volume never reached the executor; the schema still did.
    assert not any("EXTERNAL VOLUME" in s.upper() for s in sql.statements)
    assert by["SCHEMA"].status == "SUCCESS"


# --------------------------------------------------------------------------- #
# Catalog rename safety
# --------------------------------------------------------------------------- #
def test_catalog_rename_hyphen_no_double_rewrite():
    m = {"mobility-prd": "mobility-prd-tgt"}
    assert rewrite_catalog_references(
        "CREATE SCHEMA `mobility-prd`.`crm`;", m
    ) == "CREATE SCHEMA `mobility-prd-tgt`.`crm`;"
    assert rewrite_catalog_references("USE CATALOG `mobility-prd`;", m) == (
        "USE CATALOG `mobility-prd-tgt`;"
    )
    # source is a prefix of the target -> must not become ...-tgt-tgt
    assert "mobility-prd-tgt-tgt" not in rewrite_catalog_references(
        "GRANT SELECT ON `mobility-prd`.s.t TO `g`;", m
    )
    # identity mapping is a no-op
    assert rewrite_catalog_references(
        "USE CATALOG `x`;", {"x": "x"}
    ) == "USE CATALOG `x`;"
