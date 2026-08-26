"""Tests for column mask / row filter capture, export, migrate, and apply.

Covers the full path a policy travels: inventory extraction from the REST payload
→ synthesized ``ALTER TABLE`` DDL → stripping inline clauses out of captured
``SHOW CREATE TABLE`` → replaying the bindings in the late import phase.

The DDL fixtures below are real ``SHOW CREATE TABLE`` output captured from
``ai27_uctest`` (see plans/column-masks-and-row-filters.md §3).
"""

from __future__ import annotations

import json
from pathlib import Path

from uc_sync.config import SyncConfig
from uc_sync.export import ExportService
from uc_sync.import_engine import ImportEngine
from uc_sync.inventory import (
    _column_masks_from_payload,
    _row_filter_from_payload,
)
from uc_sync.models import ObjectType, UCObject
from uc_sync.package_import import PackageImportEngine
from uc_sync.rewrite import strip_inline_policy_clauses, strip_managed_storage_clauses
from uc_sync.sql_ddl import (
    mask_statements_for_object,
    policy_statements_for_object,
    row_filter_statements_for_object,
)


def _table_with_policies() -> UCObject:
    """A table carrying a mask (ssn), a mask with USING COLUMNS (val), and a filter."""

    return UCObject(
        object_type=ObjectType.TABLE,
        name="employees",
        full_name="c.hr.employees",
        catalog="c",
        schema="hr",
        definition={
            "columns": [{"name": "ssn", "position": 0, "type_text": "STRING"}],
            "column_masks": [
                {
                    "column_name": "ssn",
                    "function_name": "c.sec.mask_ssn",
                    "using_column_names": [],
                },
                {
                    "column_name": "val",
                    "function_name": "c.sec.mask_by_region",
                    "using_column_names": ["region"],
                },
            ],
            "row_filter": {
                "function_name": "c.sec.hr_dept_filter",
                "input_column_names": ["dept"],
            },
        },
    )


# ---- REST payload extraction ------------------------------------------------


def test_extract_row_filter_and_masks_from_rest_payload():
    # Shape mirrors GET /api/2.1/unity-catalog/tables/{full_name}.
    payload = {
        "row_filter": {
            "function_name": "c.sec.hr_dept_filter",
            "input_column_names": ["dept"],
        },
        # Sibling inherited keys that must be ignored.
        "effective_row_filters": [{"function_name": "c.sec.inherited"}],
        "columns": [
            {"name": "id", "position": 0},
            {
                "name": "ssn",
                "position": 1,
                "mask": {"function_name": "c.sec.mask_ssn"},
                "effective_masks": [{"function_name": "c.sec.inherited_mask"}],
            },
            {
                "name": "val",
                "position": 2,
                "mask": {
                    "function_name": "c.sec.mask_by_region",
                    "using_column_names": ["region"],
                },
            },
        ],
    }
    assert _row_filter_from_payload(payload) == {
        "function_name": "c.sec.hr_dept_filter",
        "input_column_names": ["dept"],
    }
    masks = _column_masks_from_payload(payload)
    assert masks == [
        {
            "column_name": "ssn",
            "function_name": "c.sec.mask_ssn",
            "using_column_names": [],
        },
        {
            "column_name": "val",
            "function_name": "c.sec.mask_by_region",
            "using_column_names": ["region"],
        },
    ]


def test_extract_returns_empty_when_no_policies():
    payload = {"columns": [{"name": "id"}]}
    assert _row_filter_from_payload(payload) is None
    assert _column_masks_from_payload(payload) == []


def test_uc_object_accessors_fall_back_to_raw_columns():
    # No normalized column_masks; masks live only in the raw columns[i].mask.
    obj = UCObject(
        object_type=ObjectType.TABLE,
        name="t",
        full_name="c.s.t",
        definition={
            "columns": [
                {"name": "ssn", "mask": {"function_name": "c.sec.mask_ssn"}}
            ],
            "row_filter": {"function_name": "c.sec.f", "input_column_names": []},
        },
    )
    assert obj.column_masks() == [
        {
            "column_name": "ssn",
            "function_name": "c.sec.mask_ssn",
            "using_column_names": [],
        }
    ]
    assert obj.row_filter() == {"function_name": "c.sec.f", "input_column_names": []}


# ---- ALTER statement synthesis ---------------------------------------------


def test_mask_statements_quote_and_use_columns():
    stmts = mask_statements_for_object(_table_with_policies())
    assert (
        "ALTER TABLE `c`.`hr`.`employees` ALTER COLUMN `ssn` "
        "SET MASK `c`.`sec`.`mask_ssn`;"
    ) in stmts
    assert (
        "ALTER TABLE `c`.`hr`.`employees` ALTER COLUMN `val` "
        "SET MASK `c`.`sec`.`mask_by_region` USING COLUMNS (`region`);"
    ) in stmts


def test_row_filter_statement():
    stmts = row_filter_statements_for_object(_table_with_policies())
    assert stmts == [
        "ALTER TABLE `c`.`hr`.`employees` SET ROW FILTER "
        "`c`.`sec`.`hr_dept_filter` ON (`dept`);"
    ]


def test_policy_statements_empty_for_non_table_and_plain_table():
    view = UCObject(object_type=ObjectType.VIEW, name="v", full_name="c.s.v")
    assert policy_statements_for_object(view) == []
    plain = UCObject(
        object_type=ObjectType.TABLE,
        name="t",
        full_name="c.s.t",
        definition={"columns": [{"name": "id"}]},
    )
    assert policy_statements_for_object(plain) == []


# ---- stripping inline clauses from SHOW CREATE ------------------------------

_CAPTURED = """CREATE TABLE ai27_uctest.hr.employees (
  id INT,
  name STRING COLLATE UTF8_BINARY,
  ssn STRING COLLATE UTF8_BINARY MASK `ai27_uctest`.`sec`.`mask_ssn`,
  dept STRING COLLATE UTF8_BINARY,
  email STRING COLLATE UTF8_BINARY)
USING delta
WITH ROW FILTER `ai27_uctest`.`sec`.`hr_dept_filter` ON (dept)
TBLPROPERTIES (
  'my.tag' = 'gold')"""

_CAPTURED_USING = (
    "CREATE TABLE c.sec.t (\n"
    "  val STRING COLLATE UTF8_BINARY MASK `c`.`sec`.`mask_by_region` "
    "USING COLUMNS(region),\n"
    "  region STRING COLLATE UTF8_BINARY)\n"
    "USING delta"
)


def test_strip_inline_policy_clauses_removes_mask_and_filter():
    out = strip_inline_policy_clauses(_CAPTURED)
    assert "MASK" not in out
    assert "ROW FILTER" not in out
    # Column and surrounding DDL survive intact.
    assert "ssn STRING COLLATE UTF8_BINARY," in out
    assert "USING delta" in out
    assert "'my.tag' = 'gold'" in out


def test_strip_inline_policy_clauses_removes_using_columns():
    out = strip_inline_policy_clauses(_CAPTURED_USING)
    assert "MASK" not in out
    assert "USING COLUMNS" not in out
    assert "val STRING COLLATE UTF8_BINARY," in out


def test_strip_is_noop_without_policies_and_idempotent():
    plain = "CREATE TABLE c.s.t (id INT)\nUSING delta"
    assert strip_inline_policy_clauses(plain) == plain
    once = strip_inline_policy_clauses(_CAPTURED)
    assert strip_inline_policy_clauses(once) == once


def test_migrate_pipeline_keeps_inline_policies():
    # strip_managed_storage_clauses is what MigrateExportService calls per file.
    # Inline masks / row filters are now KEPT so the CREATE TABLE carries its
    # protection atomically (functions import before tables). Collation is still
    # stripped; the reserved TBLPROPERTIES filter still runs.
    out = strip_managed_storage_clauses(_CAPTURED, "TABLE")
    assert "MASK `ai27_uctest`.`sec`.`mask_ssn`" in out
    assert "WITH ROW FILTER `ai27_uctest`.`sec`.`hr_dept_filter` ON (dept)" in out
    assert "COLLATE" not in out  # collation still stripped


def test_migrate_preserves_policy_alter_statements(tmp_path: Path):
    """Regression: migrate must NOT strip the ``SET MASK`` / ``SET ROW FILTER``
    clause out of the ALTER statement, and must preserve names verbatim.

    strip_inline_policy_clauses is only for CREATE DDL; running it on a policy
    file corrupts ``... SET MASK f`` into ``... SET`` → PARSE_SYNTAX_ERROR.
    """

    from uc_sync.migrate_export import MigrateExportService

    source = tmp_path / "export_staging" / "run1"
    (source / "policies").mkdir(parents=True)
    (source / "inventory").mkdir()
    (source / "policies" / "TABLE_c__hr__employees.sql").write_text(
        "ALTER TABLE `c`.`hr`.`employees` ALTER COLUMN `ssn` "
        "SET MASK `c`.`sec`.`mask_ssn`;\n"
        "ALTER TABLE `c`.`hr`.`employees` SET ROW FILTER "
        "`c`.`sec`.`hr_dept_filter` ON (`dept`);\n",
        encoding="utf-8",
    )
    (source / "inventory" / "objects.json").write_text("[]", encoding="utf-8")

    target = tmp_path / "export_migrated_staging" / "run1"
    MigrateExportService(
        source_root=str(source),
        target_root=str(target),
        run_id="run1",
    ).run(dry_run=False)

    # Names are never mapped → file name and identifiers are preserved.
    out = (target / "policies" / "TABLE_c__hr__employees.sql").read_text(
        encoding="utf-8"
    )
    assert "`c`.`hr`.`employees`" in out
    # The binding clauses are intact (not stripped).
    assert "SET MASK `c`.`sec`.`mask_ssn`" in out
    assert "SET ROW FILTER `c`.`sec`.`hr_dept_filter` ON (`dept`)" in out


# ---- export writes the policies artifact ------------------------------------


def test_export_writes_policy_files(tmp_path: Path):
    volume = tmp_path / "vol"
    workspace = tmp_path / "ws"
    result = ExportService(
        str(volume), "run1", workspace_root=str(workspace)
    ).run([_table_with_policies()], dry_run=False)

    assert result["policy_files"] == 1
    policies = volume / "run_run1" / "policies"
    per_object = policies / "TABLE_c__hr__employees.sql"
    assert per_object.exists()
    body = per_object.read_text(encoding="utf-8")
    assert "SET MASK `c`.`sec`.`mask_ssn`" in body
    assert "SET ROW FILTER `c`.`sec`.`hr_dept_filter` ON (`dept`)" in body
    assert (policies / "all_policies.sql").exists()

    item = next(row for row in result["results"] if row["object_type"] == "TABLE")
    assert item["policies_path"].endswith("TABLE_c__hr__employees.sql")


# ---- package import applies policies in a late phase ------------------------


class RecordingSql:
    def __init__(self, fail_on: str | None = None):
        self.statements: list[str] = []
        self.fail_on = fail_on

    def execute(self, sql: str):
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError(self.fail_on)
        self.statements.append(sql)


def _policy_package(tmp_path: Path) -> Path:
    root = tmp_path / "migrated"
    (root / "ddl").mkdir(parents=True)
    (root / "policies").mkdir(parents=True)
    (root / "ddl" / "TABLE_tgt__hr__employees.sql").write_text(
        "CREATE TABLE IF NOT EXISTS `tgt`.`hr`.`employees` (ssn STRING);\n",
        encoding="utf-8",
    )
    (root / "policies" / "TABLE_tgt__hr__employees.sql").write_text(
        "-- Column masks / row filters\n"
        "ALTER TABLE `tgt`.`hr`.`employees` ALTER COLUMN `ssn` "
        "SET MASK `tgt`.`sec`.`mask_ssn`;\n"
        "ALTER TABLE `tgt`.`hr`.`employees` SET ROW FILTER "
        "`tgt`.`sec`.`f` ON (`dept`);\n",
        encoding="utf-8",
    )
    return root


def _inline_mask_package(tmp_path: Path, mask_fn: str = "`tgt`.`sec`.`mask_ssn`") -> Path:
    """A bundle whose CREATE TABLE carries its column mask INLINE (the new atomic
    fail-closed shape), with the mask function created before the table."""
    root = tmp_path / "migrated"
    (root / "ddl").mkdir(parents=True)
    (root / "ddl" / "FUNCTION_tgt__sec__mask_ssn.sql").write_text(
        "CREATE FUNCTION IF NOT EXISTS `tgt`.`sec`.`mask_ssn`(v STRING) "
        "RETURNS STRING RETURN '***';\n",
        encoding="utf-8",
    )
    (root / "ddl" / "TABLE_tgt__hr__employees.sql").write_text(
        "CREATE TABLE IF NOT EXISTS `tgt`.`hr`.`employees` "
        f"(ssn STRING MASK {mask_fn});\n",
        encoding="utf-8",
    )
    return root


def test_package_import_keeps_inline_mask_atomic(tmp_path: Path):
    """Plan #A/#2: with the function created first, the inline MASK stays in the
    CREATE TABLE (not stripped, no separate APPLY_POLICY phase) and the table is
    SUCCESS with the clause intact."""
    root = _inline_mask_package(tmp_path)
    sql = RecordingSql()
    results = PackageImportEngine(str(root), sql, dry_run=False).run()

    # No classic-masks phase runs any more.
    assert not any(r.action == "APPLY_POLICY" for r in results)
    # The function CREATE runs before the table CREATE (functions ranked first).
    fn_idx = next(i for i, s in enumerate(sql.statements) if "CREATE FUNCTION" in s)
    tbl_idx = next(i for i, s in enumerate(sql.statements) if "CREATE TABLE" in s)
    assert fn_idx < tbl_idx
    # The MASK clause is kept inline on the CREATE TABLE (atomic protection).
    create = sql.statements[tbl_idx]
    assert "MASK `tgt`.`sec`.`mask_ssn`" in create
    table_row = next(r for r in results if r.object_type == "TABLE")
    assert table_row.status == "SUCCESS"


def test_package_import_inline_mask_missing_function_fails_closed(tmp_path: Path):
    """Plan #3(a): an inline MASK referencing a function that does not exist makes
    the CREATE TABLE itself fail — the table is never created, nothing leaks."""
    root = _inline_mask_package(tmp_path, mask_fn="`other_cat`.`sec`.`mask_x`")
    # Drop the function file so the referenced mask function is truly absent.
    (root / "ddl" / "FUNCTION_tgt__sec__mask_ssn.sql").unlink()

    class NoFunctionSql:
        def __init__(self):
            self.statements = []

        def execute(self, sql: str):
            if "CREATE TABLE" in sql and "other_cat" in sql:
                raise RuntimeError(
                    "[ROUTINE_NOT_FOUND] The function `other_cat`.`sec`.`mask_x` "
                    "cannot be found"
                )
            if sql.upper().startswith("DESCRIBE"):
                raise RuntimeError("[TABLE_OR_VIEW_NOT_FOUND] not found")
            self.statements.append(sql)

    results = PackageImportEngine(str(root), NoFunctionSql(), dry_run=False).run()
    table_row = next(r for r in results if r.object_type == "TABLE")
    assert table_row.status == "FAILURE"


def test_package_import_policies_dir_no_longer_replayed(tmp_path: Path):
    """The vestigial ``policies/`` artifact is no longer applied: classic masks are
    inline now, so no APPLY_POLICY statements are issued."""
    root = _policy_package(tmp_path)  # plain CREATE + a policies/ ALTER file
    sql = RecordingSql()
    results = PackageImportEngine(str(root), sql, dry_run=False).run()
    assert not any(r.action == "APPLY_POLICY" for r in results)
    assert not any("SET MASK" in s for s in sql.statements)
    assert not any("SET ROW FILTER" in s for s in sql.statements)


def test_direct_import_policy_unsupported_cluster_is_manual():
    cfg = SyncConfig(mappings={"catalogs": {"c": "tgt"}}, dry_run=False)

    class AssignedClusterSql:
        def execute(self, sql: str):
            raise RuntimeError(
                "ErrorClass=INVALID_PARAMETER_VALUE."
                "ROW_COLUMN_ACCESS_POLICIES_NOT_SUPPORTED_ON_ASSIGNED_CLUSTERS"
            )

    engine = ImportEngine(target=None, cfg=cfg, sql_executor=AssignedClusterSql())
    results = engine._apply_policies([_table_with_policies()], start_order=0)
    assert results[0].status == "MANUAL_ACTION_REQUIRED"
    assert results[0].error_code == "POLICY_COMPUTE_UNSUPPORTED"


# ---- direct import path maps function names via the mapper ------------------


def test_direct_import_policy_statements_use_target_names():
    cfg = SyncConfig(mappings={"catalogs": {"c": "tgt"}}, dry_run=False)
    engine = ImportEngine(target=None, cfg=cfg, sql_executor=None)
    stmts = engine._policy_statements(_table_with_policies(), "tgt.hr.employees")
    assert (
        "ALTER TABLE `tgt`.`hr`.`employees` ALTER COLUMN `ssn` "
        "SET MASK `tgt`.`sec`.`mask_ssn`" in stmts
    )
    assert any(
        "SET ROW FILTER `tgt`.`sec`.`hr_dept_filter` ON (`dept`)" in s
        for s in stmts
    )


def test_direct_import_applies_policies_after_objects():
    cfg = SyncConfig(mappings={"catalogs": {"c": "tgt"}}, dry_run=False)
    sql = RecordingSql()
    engine = ImportEngine(target=None, cfg=cfg, sql_executor=sql)
    results = engine._apply_policies([_table_with_policies()], start_order=0)
    assert results and results[0].action == "APPLY_POLICY"
    assert results[0].status == "SUCCESS"
    assert any("SET MASK `tgt`.`sec`.`mask_ssn`" in s for s in sql.statements)


# ---- validation diffs policies (compared by schema.object suffix) -----------


def test_validation_policy_comparison_matches_across_catalogs():
    from uc_sync.validation import ValidationService

    obj = _table_with_policies()  # functions in catalog "c"
    # Target payload with the same policies but a different catalog ("tgt").
    target = {
        "columns": [
            {"name": "ssn", "mask": {"function_name": "tgt.sec.mask_ssn"}},
            {
                "name": "val",
                "mask": {
                    "function_name": "tgt.sec.mask_by_region",
                    "using_column_names": ["region"],
                },
            },
        ],
        "row_filter": {
            "function_name": "tgt.sec.hr_dept_filter",
            "input_column_names": ["dept"],
        },
    }
    ok, detail = ValidationService._policy_comparison(obj, target)
    assert ok and detail == ""


def test_validation_policy_comparison_flags_missing_filter():
    from uc_sync.validation import ValidationService

    obj = _table_with_policies()
    target = {  # masks present, row filter dropped on target
        "columns": [
            {"name": "ssn", "mask": {"function_name": "tgt.sec.mask_ssn"}},
            {
                "name": "val",
                "mask": {
                    "function_name": "tgt.sec.mask_by_region",
                    "using_column_names": ["region"],
                },
            },
        ],
    }
    ok, detail = ValidationService._policy_comparison(obj, target)
    assert not ok
    assert "row filter differs" in detail
