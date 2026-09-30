"""Backlog item 6: the tag-op result must not claim a table was "dropped" when it was
not. A fresh shell this run created is dropped fail-closed → the governance-apply result
says "dropped". A pre-existing / create-disabled table is marked FAILURE in place (never
dropped) → the governance-apply result must say "not dropped — pre-existing", never
"dropped"."""

from __future__ import annotations

from pathlib import Path

from uc_sync.package_import import PackageImportEngine
from tests.test_failclosed_governance import GovSql, _write


def _gov_op_row(results, target):
    """The governance-apply (tag-op) result for a target — action MANUAL, PROTECTION_FAILED
    (distinct from the object's own create/skip result which is GOVERNANCE_FAILED)."""
    return next(
        r for r in results
        if r.target_full_name == target and r.error_code == "PROTECTION_FAILED"
        and r.policies_path
    )


def test_fresh_shell_tag_failure_says_dropped(tmp_path: Path):
    root = tmp_path / "migrated"
    _write(root, "ddl/TABLE_c__hr__fresh.sql", "CREATE TABLE `c`.`hr`.`fresh` (id INT);\n")
    _write(root, "tags/TABLE_c__hr__fresh.sql",
           "ALTER TABLE `c`.`hr`.`fresh` SET TAGS ('missing' = 'x');\n")
    _write(root, "inventory/objects.json", "[]")

    sql = GovSql(allowed_tags=set())  # tag 'missing' not allowed → fails
    results = PackageImportEngine(str(root), sql, dry_run=False).run()

    # A fresh shell → really dropped by the sweep.
    assert any("DROP TABLE" in s for s in sql.statements)
    row = _gov_op_row(results, "c.hr.fresh")
    assert "dropped" in row.message.lower()
    assert "not dropped" not in row.message.lower()


def test_preexisting_tag_failure_does_not_claim_dropped(tmp_path: Path):
    root = tmp_path / "migrated"
    _write(root, "ddl/TABLE_c__hr__pre.sql", "CREATE TABLE `c`.`hr`.`pre` (id INT);\n")
    _write(root, "tags/TABLE_c__hr__pre.sql",
           "ALTER TABLE `c`.`hr`.`pre` SET TAGS ('missing' = 'x');\n")
    _write(root, "inventory/objects.json", "[]")

    # Table already present on target (data-bearing) → CREATE reports it exists, so it is
    # SKIP_EXISTING and never a fresh shell.
    class PreExistingSql(GovSql):
        def execute(self, sql: str):
            if sql.strip().upper().startswith("CREATE TABLE"):
                self.statements.append(sql)
                self.tables.add("c.hr.pre")
                raise RuntimeError("[TABLE_OR_VIEW_ALREADY_EXISTS] exists")
            return super().execute(sql)

    sql = PreExistingSql(allowed_tags=set())
    results = PackageImportEngine(str(root), sql, dry_run=False).run()

    # Never dropped (pre-existing, may hold data).
    assert not any("DROP TABLE" in s for s in sql.statements)
    row = _gov_op_row(results, "c.hr.pre")
    # The bug: the message used to claim "dropped fail-closed" here. It must not.
    assert "not dropped" in row.message.lower()
    assert "pre-existing" in row.message.lower()
