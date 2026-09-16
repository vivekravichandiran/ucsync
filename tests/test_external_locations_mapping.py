"""Task 2: the single external-storage mapping file (external_locations.csv).

Column shape auto-selects behavior: 2 columns → BYO (prefix-swap, no SC/EL create);
3 columns (+access_connector_id) → CREATE (SC/EL created). Precedence at import:
exact object_locations override → external_locations base-path swap → skip.
"""

from __future__ import annotations

from pathlib import Path

from uc_sync.location_mapping import (
    ObjectLocations,
    parse_external_locations,
    load_external_locations_csv,
)
from uc_sync.package_import import PackageImportEngine


# --- loader: shape detection + rewrite --------------------------------------


def test_two_column_file_is_byo_no_storage_create():
    ext = parse_external_locations([
        {"source_base_path": "abfss://data@srcacct.dfs.core.windows.net",
         "target_base_path": "abfss://data@tgtacct.dfs.core.windows.net"},
    ])
    assert ext.creates_storage is False
    # Prefix swap keeps the remainder byte-for-byte.
    assert ext.rewrite(
        "abfss://data@srcacct.dfs.core.windows.net/ap/invoices_ext"
    ) == "abfss://data@tgtacct.dfs.core.windows.net/ap/invoices_ext"
    # An already-target path has no source match but is recognised as covered.
    assert ext.rewrite("abfss://data@tgtacct.dfs.core.windows.net/ap/x") is None
    assert ext.covers_target("abfss://data@tgtacct.dfs.core.windows.net/ap/x")


def test_three_column_file_creates_storage_and_carries_connector():
    ext = parse_external_locations([
        {"source_base_path": "abfss://data@srcacct.dfs.core.windows.net",
         "target_base_path": "abfss://data@tgtacct.dfs.core.windows.net",
         "access_connector_id": "/subscriptions/x/accessConnectors/tgt-conn"},
    ])
    assert ext.creates_storage is True
    rows = ext.to_location_mappings()
    assert rows[0]["source_location"] == "abfss://data@srcacct.dfs.core.windows.net"
    assert rows[0]["target_access_connector_id"].endswith("tgt-conn")


def test_first_matching_row_wins():
    ext = parse_external_locations([
        {"source_base_path": "abfss://a@one", "target_base_path": "abfss://a@ONE"},
        {"source_base_path": "abfss://b@two", "target_base_path": "abfss://b@TWO"},
    ])
    assert ext.rewrite("abfss://b@two/p") == "abfss://b@TWO/p"


def test_load_from_csv_two_and_three_column(tmp_path: Path):
    two = tmp_path / "ext2.csv"
    two.write_text(
        "source_base_path,target_base_path\n"
        "abfss://data@src,abfss://data@tgt\n",
        encoding="utf-8",
    )
    assert load_external_locations_csv(str(two)).creates_storage is False

    # Back-compat: the legacy 3-column ai27_target_mapping.csv column names load via
    # aliases (source_location/target_location/target_access_connector_id).
    three = tmp_path / "ai27_target_mapping.csv"
    three.write_text(
        "source_location,target_location,target_access_connector_id\n"
        "abfss://data@src,abfss://data@tgt,/subscriptions/x/ac/conn\n",
        encoding="utf-8",
    )
    ext = load_external_locations_csv(str(three))
    assert ext.creates_storage is True
    assert ext.rewrite("abfss://data@src/p") == "abfss://data@tgt/p"


# --- import-time placement precedence ---------------------------------------


class RecordingSql:
    def __init__(self):
        self.statements: list[str] = []
        self.tables: set[str] = set()

    def execute(self, sql: str):
        self.statements.append(sql)
        u = sql.strip().upper()
        if u.startswith("CREATE EXTERNAL TABLE") or u.startswith("CREATE TABLE"):
            import re
            m = re.search(r"TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([`\w.]+)", sql, re.I)
            if m:
                self.tables.add(m.group(1).replace("`", ""))
        if u.startswith("DESCRIBE"):
            name = sql.split()[-1].replace("`", "").rstrip(";")
            if name not in self.tables:
                raise RuntimeError(f"NOT_FOUND {name}")
        return None


def _ext_bundle(root: Path, location: str) -> None:
    (root / "ddl").mkdir(parents=True)
    (root / "inventory").mkdir()
    (root / "inventory" / "objects.json").write_text("[]", encoding="utf-8")
    (root / "ddl" / "EXTERNAL_TABLE_c__hr__ext.sql").write_text(
        f"CREATE EXTERNAL TABLE `c`.`hr`.`ext` (id INT) LOCATION '{location}';\n",
        encoding="utf-8",
    )


def _ext_location(sql: RecordingSql) -> str:
    import re
    stmt = next(s for s in sql.statements if "CREATE EXTERNAL TABLE" in s.upper())
    return re.search(r"LOCATION\s+'([^']*)'", stmt, re.I).group(1)


def test_base_path_swap_places_external_object(tmp_path: Path):
    root = tmp_path / "migrated"
    _ext_bundle(root, "abfss://data@srcacct.dfs.core.windows.net/hr/ext")
    ext = parse_external_locations([
        {"source_base_path": "abfss://data@srcacct.dfs.core.windows.net",
         "target_base_path": "abfss://data@tgtacct.dfs.core.windows.net"},
    ])
    sql = RecordingSql()
    engine = PackageImportEngine(str(root), sql, dry_run=False, external_locations=ext)
    engine._existing_catalog_mode = True  # BYO / Mode B
    results = engine.run()
    ext_row = next(r for r in results if r.object_type == "EXTERNAL_TABLE")
    assert ext_row.status == "SUCCESS"
    assert _ext_location(sql) == (
        "abfss://data@tgtacct.dfs.core.windows.net/hr/ext"
    )


def test_object_locations_override_beats_base_path_swap(tmp_path: Path):
    root = tmp_path / "migrated"
    _ext_bundle(root, "abfss://data@srcacct.dfs.core.windows.net/hr/ext")
    ext = parse_external_locations([
        {"source_base_path": "abfss://data@srcacct.dfs.core.windows.net",
         "target_base_path": "abfss://data@tgtacct.dfs.core.windows.net"},
    ])
    override = ObjectLocations(
        schemas={}, volumes={}, tables={("hr", "ext"): "abfss://exact@acct/precise"}
    )
    sql = RecordingSql()
    engine = PackageImportEngine(
        str(root), sql, dry_run=False,
        object_locations=override, external_locations=ext,
    )
    engine._existing_catalog_mode = True
    engine.run()
    assert _ext_location(sql) == "abfss://exact@acct/precise"


def test_unmatched_base_path_is_skipped_not_failed(tmp_path: Path):
    root = tmp_path / "migrated"
    _ext_bundle(root, "abfss://data@otheracct.dfs.core.windows.net/hr/ext")
    ext = parse_external_locations([
        {"source_base_path": "abfss://data@srcacct.dfs.core.windows.net",
         "target_base_path": "abfss://data@tgtacct.dfs.core.windows.net"},
    ])
    sql = RecordingSql()
    engine = PackageImportEngine(str(root), sql, dry_run=False, external_locations=ext)
    engine._existing_catalog_mode = True
    results = engine.run()
    ext_row = next(r for r in results if r.object_type == "EXTERNAL_TABLE")
    # No base-path matched → clean MANUAL skip, never created, never a bare failure.
    assert ext_row.status == "MANUAL_ACTION_REQUIRED"
    assert ext_row.error_code == "EXTERNAL_LOCATION_MISSING"
    assert "c.hr.ext" not in sql.tables


def test_config_two_column_forces_storage_create_off(tmp_path: Path):
    """A 2-column external_locations file is BYO: from_sources forces the storage-
    credential + external-location create toggles OFF and feeds location_mappings;
    a 3-column file leaves them creatable."""
    from uc_sync.config import from_sources

    two = tmp_path / "ext2.csv"
    two.write_text(
        "source_base_path,target_base_path\nabfss://src,abfss://tgt\n",
        encoding="utf-8",
    )
    cfg = from_sources({
        "stage": "IMPORT", "output_volume_path": "/Volumes/c/s/v",
        "ops_catalog": "c", "ops_schema": "s",
        "external_locations_path": str(two),
    })
    assert cfg.external_locations_create_storage is False
    assert cfg.create_storage_credentials is False
    assert cfg.create_external_locations is False
    assert cfg.mappings["location_mappings"]  # fed from the one file

    three = tmp_path / "ext3.csv"
    three.write_text(
        "source_base_path,target_base_path,access_connector_id\n"
        "abfss://src,abfss://tgt,/subscriptions/x/ac/conn\n",
        encoding="utf-8",
    )
    cfg3 = from_sources({
        "stage": "IMPORT", "output_volume_path": "/Volumes/c/s/v",
        "ops_catalog": "c", "ops_schema": "s",
        "external_locations_path": str(three),
    })
    assert cfg3.external_locations_create_storage is True
    assert cfg3.create_storage_credentials is True
    assert cfg3.create_external_locations is True


def test_already_target_location_is_kept_not_skipped(tmp_path: Path):
    """When the LOCATION was already prefix-swapped upstream (at export), the object
    is already placed under a target base — keep it, do not re-skip."""
    root = tmp_path / "migrated"
    _ext_bundle(root, "abfss://data@tgtacct.dfs.core.windows.net/hr/ext")
    ext = parse_external_locations([
        {"source_base_path": "abfss://data@srcacct.dfs.core.windows.net",
         "target_base_path": "abfss://data@tgtacct.dfs.core.windows.net"},
    ])
    sql = RecordingSql()
    engine = PackageImportEngine(str(root), sql, dry_run=False, external_locations=ext)
    engine._existing_catalog_mode = True
    results = engine.run()
    ext_row = next(r for r in results if r.object_type == "EXTERNAL_TABLE")
    assert ext_row.status == "SUCCESS"
    assert _ext_location(sql) == "abfss://data@tgtacct.dfs.core.windows.net/hr/ext"
