"""Config-resolution matrix for the mapping_file_path removal (backlog item 1).

The removal must be behaviour-identical for the storage-path precedence: (a)
external_locations only, (b) location_mapping_csv_path only, (c) both — location_mappings
+ the SC/EL create toggles must resolve exactly as before.
"""

from __future__ import annotations

from pathlib import Path

from uc_sync.config import from_sources


def _write_ext_two_col(tmp_path: Path) -> str:
    p = tmp_path / "ext2.csv"
    p.write_text(
        "source_base_path,target_base_path\n"
        "abfss://data@src,abfss://data@tgt\n",
        encoding="utf-8",
    )
    return str(p)


def _write_ext_three_col(tmp_path: Path) -> str:
    p = tmp_path / "ext3.csv"
    p.write_text(
        "source_location,target_location,target_access_connector_id\n"
        "abfss://data@src,abfss://data@tgt,/subscriptions/x/ac/conn\n",
        encoding="utf-8",
    )
    return str(p)


def _write_location_csv(tmp_path: Path) -> str:
    p = tmp_path / "loc.csv"
    p.write_text(
        "source_location,target_location,target_external_location,target_credential\n"
        "abfss://leg@src,abfss://leg@tgt,tgt_el,tgt_cred\n",
        encoding="utf-8",
    )
    return str(p)


def test_field_is_removed_from_config():
    cfg = from_sources({"catalogs": "c"})
    assert not hasattr(cfg, "mapping_file_path")


def test_a_external_locations_only_two_col_byo(tmp_path):
    cfg = from_sources({"catalogs": "c",
                        "external_locations_path": _write_ext_two_col(tmp_path)})
    # 2-col file = BYO → SC/EL creation forced OFF.
    assert cfg.create_storage_credentials is False
    assert cfg.create_external_locations is False
    assert cfg.external_locations_create_storage is False
    # location_mappings fed from the file.
    lm = cfg.mappings["location_mappings"]
    assert lm and lm[0]["source_location"].startswith("abfss://data@src")


def test_a_external_locations_only_three_col_creates(tmp_path):
    cfg = from_sources({"catalogs": "c",
                        "external_locations_path": _write_ext_three_col(tmp_path)})
    # 3-col file = create SC/EL ON (Mode-A parity).
    assert cfg.create_storage_credentials is True
    assert cfg.create_external_locations is True
    assert cfg.external_locations_create_storage is True


def test_b_location_mapping_csv_only(tmp_path):
    cfg = from_sources({"catalogs": "c",
                        "location_mapping_csv_path": _write_location_csv(tmp_path)})
    lm = cfg.mappings["location_mappings"]
    assert lm and lm[0]["source_location"] == "abfss://leg@src"
    assert lm[0]["target_external_location"] == "tgt_el"
    # No external_locations file → SC/EL toggles stay at their BYO defaults (off).
    assert cfg.create_storage_credentials is False
    assert cfg.create_external_locations is False


def test_c_both_location_csv_wins_for_mappings_ext_sets_toggles(tmp_path):
    # When both are supplied: location_mapping_csv_path is authoritative for
    # location_mappings (its own code path), and the external_locations file's column
    # shape still sets the SC/EL toggles.
    cfg = from_sources({
        "catalogs": "c",
        "location_mapping_csv_path": _write_location_csv(tmp_path),
        "external_locations_path": _write_ext_three_col(tmp_path),
    })
    lm = cfg.mappings["location_mappings"]
    # location_mappings come from the legacy CSV, NOT the external_locations file.
    assert lm[0]["source_location"] == "abfss://leg@src"
    # 3-col external_locations file still turns SC/EL creation on.
    assert cfg.create_storage_credentials is True
    assert cfg.create_external_locations is True


def test_neither_input_is_inert(tmp_path):
    cfg = from_sources({"catalogs": "c"})
    assert cfg.mappings["location_mappings"] == []
    assert cfg.create_storage_credentials is False
    assert cfg.create_external_locations is False
