"""Object names containing ``__`` survive the export file-name round trip.

The export encodes ``.`` as ``__`` in bundle file names, so decoding the file name
turned ``c.s.orders__daily`` into ``c.s.orders.daily`` and the import ran CREATE /
GRANT / SET TAGS against a 4-part name (REQUIRES_SINGLE_PART_NAMESPACE). The import
now takes the real name from the bundle inventory.
"""

from __future__ import annotations

import json
from pathlib import Path

from uc_sync.export import _safe_filename
from uc_sync.package_import import PackageImportEngine

_NAMES = (
    "c.s.orders__daily",          # one '__' inside the table name
    "c.s.orders___eu__daily",     # '___' and '__'
    "c.stage_.t",                 # a part ending in '_' next to a dot
)


class _FakeSql:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement):
        self.statements.append(statement)
        return []


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "migrated"
    for d in ("ddl", "grants", "tags", "inventory"):
        (root / d).mkdir(parents=True)
    rows = [
        {"object_type": "CATALOG", "full_name": "c", "name": "c"},
        {"object_type": "SCHEMA", "full_name": "c.s", "name": "s"},
        {"object_type": "SCHEMA", "full_name": "c.stage_", "name": "stage_"},
    ]
    (root / "ddl" / "CATALOG_c.sql").write_text(
        "CREATE CATALOG IF NOT EXISTS `c`;\n", encoding="utf-8")
    for schema in ("s", "stage_"):
        stem = _safe_filename("SCHEMA", f"c.{schema}")
        (root / "ddl" / f"{stem}.sql").write_text(
            f"CREATE SCHEMA IF NOT EXISTS `c`.`{schema}`;\n", encoding="utf-8")
    for full_name in _NAMES:
        cat, sch, tbl = full_name.split(".")
        quoted = f"`{cat}`.`{sch}`.`{tbl}`"
        stem = _safe_filename("TABLE", full_name)
        (root / "ddl" / f"{stem}.sql").write_text(
            f"CREATE TABLE IF NOT EXISTS {quoted} (id INT);\n", encoding="utf-8")
        (root / "grants" / f"{stem}.sql").write_text(
            f"GRANT SELECT ON TABLE {quoted} TO `analysts`;\n", encoding="utf-8")
        (root / "tags" / f"{stem}.sql").write_text(
            f"ALTER TABLE {quoted} SET TAGS ('tier' = 'gold');\n", encoding="utf-8")
        rows.append({"object_type": "TABLE", "full_name": full_name, "name": tbl})
    (root / "inventory" / "objects.json").write_text(json.dumps(rows), encoding="utf-8")
    return root


def test_double_underscore_names_import_under_their_real_name(tmp_path: Path):
    sql = _FakeSql()
    results = PackageImportEngine(str(_bundle(tmp_path)), sql, dry_run=False).run()
    tables = {r.target_full_name: r for r in results if r.object_type == "TABLE"
              and r.action != "APPLY_TAGS"}
    executed = "\n".join(sql.statements)
    for full_name in _NAMES:
        assert full_name in tables, sorted(tables)
        assert tables[full_name].status == "SUCCESS", tables[full_name].message
        assert tables[full_name].source_full_name == full_name
        cat, sch, tbl = full_name.split(".")
        quoted = f"`{cat}`.`{sch}`.`{tbl}`"
        assert f"TABLE IF NOT EXISTS {quoted}" in executed
        assert f"GRANT SELECT ON TABLE {quoted}" in executed
        assert f"ALTER TABLE {quoted} SET TAGS" in executed
    # Nothing was ever addressed by a mangled (decoded) name.
    for mangled in ("orders.daily", "orders._eu", "stage._t"):
        assert mangled not in executed
        assert not any(mangled in r.target_full_name for r in results)


def test_filter_tables_selects_double_underscore_name(tmp_path: Path):
    sql = _FakeSql()
    results = PackageImportEngine(
        str(_bundle(tmp_path)), sql, dry_run=False,
        select_tables=["c.s.orders___eu__daily"],
    ).run()
    by_name = {r.target_full_name: r for r in results
               if r.object_type == "TABLE" and r.action != "APPLY_TAGS"}
    assert by_name["c.s.orders___eu__daily"].action != "SKIP_FILTERED"
    assert by_name["c.s.orders___eu__daily"].status == "SUCCESS"
    assert by_name["c.s.orders__daily"].action == "SKIP_FILTERED"
