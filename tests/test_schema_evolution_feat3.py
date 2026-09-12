"""FEAT-3: governed schema evolution — on an incremental run, a source column not
present on the (pre-existing) target is added with the exact source type, and its
classic mask is applied. A same-tag new column is auto-covered by the existing ABAC
policy (no new policy)."""

from __future__ import annotations

import json
from pathlib import Path

from uc_sync.package_import import PackageImportEngine


class EvoSql:
    """Fake executor: DESCRIBE TABLE returns the OLD target columns; everything else
    is recorded."""

    def __init__(self, target_columns):
        self.statements: list[str] = []
        self._target_columns = list(target_columns)

    def execute(self, sql):
        self.statements.append(sql)
        if sql.strip().upper().startswith("DESCRIBE"):
            return [[c, "int", ""] for c in self._target_columns]
        return None


def _write(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _evo_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "migrated"
    _write(root, "ddl/CATALOG_c.sql", "CREATE CATALOG `c`;\n")
    _write(root, "ddl/SCHEMA_c__s.sql", "CREATE SCHEMA `c`.`s`;\n")
    _write(root, "ddl/TABLE_c__s__t.sql",
           "CREATE TABLE `c`.`s`.`t` (id INT, email STRING);\n")
    _write(root, "inventory/objects.json", json.dumps([
        {"object_type": "CATALOG", "full_name": "c", "target_full_name": "c",
         "tags": {}, "grants": [], "definition": {}},
        {"object_type": "SCHEMA", "full_name": "c.s", "target_full_name": "c.s",
         "tags": {}, "grants": [], "definition": {}},
        {"object_type": "TABLE", "full_name": "c.s.t", "target_full_name": "c.s.t",
         "tags": {}, "grants": [],
         "definition": {
             "columns": [
                 {"name": "id", "type_text": "int"},
                 {"name": "email", "type_text": "string", "comment": "new pii col"},
             ],
             "column_masks": [
                 {"column_name": "email", "function_name": "c.s.mask_email",
                  "using_column_names": []},
             ],
         }},
    ]))
    return root


def _baseline():
    # Table ddl/governance hashes differ from current (email column added) → CHANGED.
    return {
        "c": {"object_type": "CATALOG", "ddl_hash": "C", "governance_hash": "C",
              "grants": {}},
        "c.s": {"object_type": "SCHEMA", "ddl_hash": "S", "governance_hash": "S",
                "grants": {}},
        "c.s.t": {"object_type": "TABLE", "ddl_hash": "OLD",
                  "governance_hash": "OLD", "grants": {}},
    }


def test_incremental_adds_new_column_with_exact_type_and_mask():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        root = _evo_bundle(Path(d))
        sql = EvoSql(target_columns=["id"])  # target is missing `email`
        results = PackageImportEngine(
            str(root), sql, dry_run=False, prior_state=_baseline()
        ).run()

    # The new column was added with the exact source type.
    assert any(
        "ALTER TABLE `c`.`s`.`t` ADD COLUMN `email` string" in s for s in sql.statements
    ), sql.statements
    # Its classic mask was applied after the column exists.
    assert any(
        "ALTER COLUMN `email` SET MASK" in s for s in sql.statements
    ), sql.statements
    # A COLUMN result row records the addition.
    col = next(r for r in results if r.object_type == "COLUMN")
    assert col.action == "COLUMN_ADDED"
    assert col.status == "SUCCESS"
    assert col.target_full_name == "c.s.t.email"


def test_no_evolution_when_target_already_has_column():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        root = _evo_bundle(Path(d))
        sql = EvoSql(target_columns=["id", "email"])  # target already current
        results = PackageImportEngine(
            str(root), sql, dry_run=False, prior_state=_baseline()
        ).run()

    assert not any("ADD COLUMN" in s for s in sql.statements)
    assert not any(r.object_type == "COLUMN" for r in results)


def test_full_run_does_no_schema_evolution():
    """Schema evolution is incremental-only — a full run (no baseline) never ALTERs."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        root = _evo_bundle(Path(d))
        sql = EvoSql(target_columns=["id"])
        PackageImportEngine(str(root), sql, dry_run=False).run()  # no prior_state

    assert not any("ADD COLUMN" in s for s in sql.statements)
