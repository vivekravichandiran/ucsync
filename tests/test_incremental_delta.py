"""Task 1: incremental (delta) sync.

Covers the delta planner (fingerprint diffs → actions) and the import-engine gating
(unchanged → zero writes; only changed governance / grants touched; removals reported
never actioned; run mode auto-detected; force_full re-seeds).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from uc_sync.delta import DeltaPlan
from uc_sync.fingerprints import (
    ddl_fingerprint,
    governance_fingerprint,
    grant_fingerprint_set,
)
from uc_sync.package_import import PackageImportEngine


def _baseline_row(row: dict) -> dict:
    return {
        "object_type": row["object_type"],
        "ddl_hash": ddl_fingerprint(row),
        "governance_hash": governance_fingerprint(row),
        "grants": grant_fingerprint_set(row),
    }


def _baseline(rows: list[dict]) -> dict:
    return {r["full_name"]: _baseline_row(r) for r in rows}


# --- DeltaPlan unit tests ---------------------------------------------------


def _rows():
    return [
        {"object_type": "TABLE", "full_name": "c.s.t", "owner": "me",
         "tags": {"cls": "OK"}, "grants": [
             {"principal": "u@x.com", "privileges": ["SELECT"]}],
         "definition": {"columns": [{"name": "id"}],
                        "column_tags": {"id": {"pii": "NONE"}}}},
        {"object_type": "VIEW", "full_name": "c.s.v", "owner": "me",
         "tags": {}, "grants": [],
         "definition": {"view_definition": "SELECT * FROM c.s.t"}},
        {"object_type": "FUNCTION", "full_name": "c.s.f", "owner": "me",
         "tags": {}, "grants": [], "definition": {"routine_definition": "RETURN 1"}},
    ]


def test_no_baseline_is_full_run_all_created_new():
    plan = DeltaPlan(_rows(), None)
    assert plan.incremental is False
    assert plan.action("c.s.t") == "CREATED_NEW"
    # On a full run nothing is gated.
    assert plan.should_skip_object("c.s.t") is False
    assert plan.governance_changed("c.s.t") is True


def test_identical_baseline_all_unchanged():
    rows = _rows()
    plan = DeltaPlan(rows, _baseline(rows))
    assert plan.incremental is True
    for name in ("c.s.t", "c.s.v", "c.s.f"):
        assert plan.action(name) == "UNCHANGED"
        assert plan.should_skip_object(name) is True
        assert plan.governance_changed(name) is False
    assert plan.unchanged_count() == 3
    assert plan.delta_rows() == []  # nothing changed → empty Delta sheet


def test_new_object_is_created_new():
    rows = _rows()
    base = _baseline(rows[:1])  # only c.s.t known
    plan = DeltaPlan(rows, base)
    assert plan.action("c.s.t") == "UNCHANGED"
    assert plan.action("c.s.v") == "CREATED_NEW"
    assert plan.should_skip_object("c.s.v") is False


def test_changed_view_replaced_changed_table_reported():
    rows = _rows()
    base = _baseline(rows)
    # Mutate DDL: the view text and the table columns.
    rows[1]["definition"]["view_definition"] = "SELECT id FROM c.s.t"
    rows[0]["definition"]["columns"] = [{"name": "id"}, {"name": "extra"}]
    plan = DeltaPlan(rows, base)
    assert plan.action("c.s.v") == "REPLACED"     # view → CREATE OR REPLACE
    assert plan.action("c.s.t") == "CHANGED"      # table → report-only
    assert plan.should_skip_object("c.s.t") is False


def test_new_tag_on_unchanged_table_is_governance_updated():
    rows = _rows()
    base = _baseline(rows)
    rows[0]["tags"] = {"cls": "SECRET"}  # tag change only (DDL unchanged)
    plan = DeltaPlan(rows, base)
    assert plan.action("c.s.t") == "GOVERNANCE_UPDATED"
    assert plan.governance_changed("c.s.t") is True
    assert plan.should_skip_object("c.s.t") is False


def test_grant_added_and_removed():
    rows = _rows()
    base = _baseline(rows)
    # Add a privilege to an existing principal + drop one entirely on source.
    rows[0]["grants"] = [
        {"principal": "u@x.com", "privileges": ["SELECT", "MODIFY"]},
    ]
    # Baseline had an extra principal that source no longer grants.
    base["c.s.t"]["grants"] = {
        "u@x.com": ["SELECT"], "gone@x.com": ["SELECT"]
    }
    plan = DeltaPlan(rows, base)
    delta = plan.get("c.s.t")
    assert delta.grants_added == {"u@x.com": ["MODIFY", "SELECT"]}
    assert delta.grants_removed == {"gone@x.com": ["SELECT"]}
    actions = {(r["action"], r["object"]) for r in plan.delta_rows()}
    assert ("GRANT_ADDED", "c.s.t") in actions
    assert ("GRANT_REMOVED", "c.s.t") in actions


def test_source_absent_reported_not_dropped():
    rows = _rows()
    base = _baseline(rows)
    base["c.s.gone"] = {"object_type": "TABLE", "ddl_hash": "x",
                        "governance_hash": "y", "grants": {}}
    plan = DeltaPlan(rows, base)
    absent = [r for r in plan.delta_rows() if r["action"] == "SOURCE_ABSENT"]
    assert len(absent) == 1 and absent[0]["object"] == "c.s.gone"


def test_force_full_ignores_baseline():
    rows = _rows()
    plan = DeltaPlan(rows, _baseline(rows), force_full=True)
    assert plan.incremental is False
    assert plan.action("c.s.t") == "CREATED_NEW"


# --- Engine integration -----------------------------------------------------


class RecordingSql:
    def __init__(self):
        self.statements: list[str] = []
        self.tables: set[str] = set()

    def execute(self, sql: str):
        self.statements.append(sql)
        u = sql.strip().upper()
        if u.startswith(("CREATE TABLE", "CREATE EXTERNAL TABLE")):
            m = re.search(r"TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([`\w.]+)", sql, re.I)
            if m:
                self.tables.add(m.group(1).replace("`", ""))
        if u.startswith("DESCRIBE"):
            name = sql.split()[-1].replace("`", "").rstrip(";")
            if name not in self.tables:
                raise RuntimeError(f"NOT_FOUND {name}")
        return None


def _write(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _bundle(tmp_path: Path, inventory: list[dict]) -> Path:
    root = tmp_path / "migrated"
    _write(root, "ddl/CATALOG_c.sql", "CREATE CATALOG `c`;\n")
    _write(root, "ddl/SCHEMA_c__s.sql", "CREATE SCHEMA `c`.`s`;\n")
    _write(root, "ddl/TABLE_c__s__t.sql", "CREATE TABLE `c`.`s`.`t` (id INT);\n")
    _write(root, "grants/TABLE_c__s__t.sql",
           "GRANT SELECT ON TABLE `c`.`s`.`t` TO `u@x.com`;\n")
    _write(root, "tags/TABLE_c__s__t.sql",
           "ALTER TABLE `c`.`s`.`t` SET TAGS ('cls' = 'OK');\n")
    _write(root, "inventory/objects.json", json.dumps(inventory))
    return root


def _inventory():
    return [
        {"object_type": "CATALOG", "full_name": "c", "target_full_name": "c",
         "tags": {}, "grants": [], "definition": {}},
        {"object_type": "SCHEMA", "full_name": "c.s", "target_full_name": "c.s",
         "tags": {}, "grants": [], "definition": {}},
        {"object_type": "TABLE", "full_name": "c.s.t", "target_full_name": "c.s.t",
         "tags": {"cls": "OK"},
         "grants": [{"principal": "u@x.com", "privileges": ["SELECT"]}],
         "definition": {"columns": [{"name": "id"}]}},
    ]


def test_full_run_seeds_then_unchanged_rerun_is_zero_writes(tmp_path: Path):
    inv = _inventory()
    root = _bundle(tmp_path, inv)

    # First run: no baseline → full, everything executed.
    sql = RecordingSql()
    r1 = PackageImportEngine(str(root), sql, dry_run=False).run()
    assert PackageImportEngine  # sanity
    assert any(s.upper().startswith("CREATE TABLE") for s in sql.statements)
    assert any("SET TAGS" in s for s in sql.statements)
    assert any("GRANT SELECT" in s for s in sql.statements)
    tbl = next(r for r in r1 if r.object_type == "TABLE")
    assert tbl.delta_action == "CREATED_NEW"
    assert tbl.ddl_hash and tbl.governance_hash  # fingerprints recorded for state

    # Build the baseline exactly as the state upsert would (one row per object).
    baseline = {
        row["full_name"]: {
            "object_type": row["object_type"],
            "ddl_hash": ddl_fingerprint(row),
            "governance_hash": governance_fingerprint(row),
            "grants": grant_fingerprint_set(row),
        }
        for row in inv
    }

    # Second run: identical source + baseline → incremental, ALL unchanged, ZERO
    # writes (no CREATE, no GRANT, no SET TAGS, no DESCRIBE).
    sql2 = RecordingSql()
    r2 = PackageImportEngine(
        str(root), sql2, dry_run=False, prior_state=baseline
    ).run()
    assert sql2.statements == [], sql2.statements
    assert all(r.status in ("SUCCESS",) for r in r2)
    assert all(r.delta_action == "UNCHANGED" for r in r2)


def test_incremental_applies_only_governance_delta(tmp_path: Path):
    inv = _inventory()
    baseline = {
        row["full_name"]: {
            "object_type": row["object_type"],
            "ddl_hash": ddl_fingerprint(row),
            "governance_hash": governance_fingerprint(row),
            "grants": grant_fingerprint_set(row),
        }
        for row in inv
    }
    # Source now tags the table differently (governance delta only).
    inv[2]["tags"] = {"cls": "SECRET"}
    root = _bundle(tmp_path, inv)
    # Reflect the new tag in the tag file too.
    _write(root, "tags/TABLE_c__s__t.sql",
           "ALTER TABLE `c`.`s`.`t` SET TAGS ('cls' = 'SECRET');\n")

    sql = RecordingSql()
    engine = PackageImportEngine(
        str(root), sql, dry_run=False, prior_state=baseline
    )
    results = engine.run()
    assert engine.delta_plan.incremental is True
    # Catalog/schema unchanged → skipped (no CREATE for them).
    assert not any(s.upper().startswith("CREATE CATALOG") for s in sql.statements)
    assert not any(s.upper().startswith("CREATE SCHEMA") for s in sql.statements)
    # The table's governance was re-applied (its fingerprint changed).
    assert any("SET TAGS" in s and "SECRET" in s for s in sql.statements)
    tbl = next(r for r in results if r.object_type == "TABLE")
    assert tbl.delta_action == "GOVERNANCE_UPDATED"


def test_incremental_never_revokes_removed_grant(tmp_path: Path):
    inv = _inventory()
    baseline = {
        row["full_name"]: {
            "object_type": row["object_type"],
            "ddl_hash": ddl_fingerprint(row),
            "governance_hash": governance_fingerprint(row),
            "grants": grant_fingerprint_set(row),
        }
        for row in inv
    }
    # Baseline had an extra grant that source no longer has.
    baseline["c.s.t"]["grants"]["old@x.com"] = ["SELECT"]
    root = _bundle(tmp_path, inv)
    sql = RecordingSql()
    engine = PackageImportEngine(
        str(root), sql, dry_run=False, prior_state=baseline
    )
    engine.run()
    # Never a REVOKE — removals are report-only.
    assert not any("REVOKE" in s.upper() for s in sql.statements)
    removed = [r for r in engine.delta_plan.delta_rows()
               if r["action"] == "GRANT_REMOVED"]
    assert removed and removed[0]["object"] == "c.s.t"
