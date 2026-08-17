"""Unit tests for rewrite → migrate → package import → state helpers."""

from __future__ import annotations

import json
from pathlib import Path

from uc_sync.audit import (
    AUDIT_COLUMNS,
    add_missing_columns_sql,
    stage_audit_row,
)
from uc_sync.migrate_export import MigrateExportService
from uc_sync.package_import import PackageImportEngine, _split_statements
from uc_sync.rewrite import rewrite_text
from uc_sync.sync_state import state_row_from_import


class FakeSql:
    def __init__(self, fail_on: str | None = None):
        self.statements: list[str] = []
        self.fail_on = fail_on

    def execute(self, sql: str):
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError(f"boom: {sql[:80]}")
        self.statements.append(sql)


def test_rewrite_text_catalog_and_quoted():
    text = (
        "CREATE TABLE `ril_sandbox`.`ucsync_local_01`.`t1` AS "
        "SELECT * FROM ril_sandbox.ucsync_local_01.t0;"
    )
    out = rewrite_text(text, {"ril_sandbox": "ril_sandbox_ucsync_local"})
    assert "`ril_sandbox_ucsync_local`.`ucsync_local_01`.`t1`" in out
    assert "ril_sandbox_ucsync_local.ucsync_local_01.t0" in out
    assert "ril_sandbox." not in out.replace("ril_sandbox_ucsync_local", "")


def test_migrate_rewrites_files_and_renames(tmp_path: Path):
    source = tmp_path / "export_staging" / "run1"
    (source / "ddl").mkdir(parents=True)
    (source / "inventory").mkdir()
    (source / "ddl" / "TABLE_ril_sandbox__s__t.sql").write_text(
        "CREATE TABLE IF NOT EXISTS `ril_sandbox`.`s`.`t` (id INT);\n",
        encoding="utf-8",
    )
    (source / "inventory" / "objects.json").write_text(
        json.dumps(
            [
                {
                    "object_type": "TABLE",
                    "full_name": "ril_sandbox.s.t",
                    "catalog": "ril_sandbox",
                    "definition": "CREATE TABLE `ril_sandbox`.`s`.`t` (id INT)",
                    "definition_hash": "abc",
                    "object_id": "oid-1",
                }
            ]
        ),
        encoding="utf-8",
    )
    target = tmp_path / "export_migrated_staging" / "run1"
    result = MigrateExportService(
        source_root=str(source),
        target_root=str(target),
        catalog_mapping={"ril_sandbox": "ril_sandbox_ucsync_local"},
        run_id="run1",
    ).run(dry_run=False)

    assert result["migrated"] >= 2
    migrated_ddl = target / "ddl" / "TABLE_ril_sandbox_ucsync_local__s__t.sql"
    assert migrated_ddl.exists()
    assert "ril_sandbox_ucsync_local" in migrated_ddl.read_text(encoding="utf-8")
    inv = json.loads((target / "inventory" / "objects.json").read_text(encoding="utf-8"))
    assert inv[0]["full_name"] == "ril_sandbox.s.t"
    assert inv[0]["source_full_name"] == "ril_sandbox.s.t"
    assert inv[0]["target_full_name"] == "ril_sandbox_ucsync_local.s.t"
    assert inv[0]["catalog"] == "ril_sandbox_ucsync_local"
    assert "`ril_sandbox_ucsync_local`.`s`.`t`" in inv[0]["definition"]


def test_package_import_executes_and_records_failure(tmp_path: Path):
    root = tmp_path / "migrated"
    (root / "ddl").mkdir(parents=True)
    (root / "inventory").mkdir()
    (root / "ddl" / "SCHEMA_ril_sandbox_ucsync_local__s.sql").write_text(
        "CREATE SCHEMA IF NOT EXISTS `ril_sandbox_ucsync_local`.`s`;\n",
        encoding="utf-8",
    )
    (root / "ddl" / "TABLE_ril_sandbox_ucsync_local__s__t.sql").write_text(
        "CREATE TABLE IF NOT EXISTS `ril_sandbox_ucsync_local`.`s`.`t` (id INT);\n",
        encoding="utf-8",
    )
    (root / "inventory" / "objects.json").write_text(
        json.dumps(
            [
                {
                    "object_type": "SCHEMA",
                    "full_name": "ril_sandbox.s",
                    "source_full_name": "ril_sandbox.s",
                    "target_full_name": "ril_sandbox_ucsync_local.s",
                },
                {
                    "object_type": "TABLE",
                    "full_name": "ril_sandbox.s.t",
                    "source_full_name": "ril_sandbox.s.t",
                    "target_full_name": "ril_sandbox_ucsync_local.s.t",
                    "definition_hash": "h1",
                    "object_id": "oid-t",
                },
            ]
        ),
        encoding="utf-8",
    )
    sql = FakeSql(fail_on="CREATE TABLE")
    results = PackageImportEngine(str(root), sql, dry_run=False).run()
    by_type = {row.object_type: row for row in results}
    assert by_type["SCHEMA"].status == "SUCCESS"
    assert by_type["SCHEMA"].source_full_name == "ril_sandbox.s"
    assert by_type["SCHEMA"].target_full_name == "ril_sandbox_ucsync_local.s"
    assert by_type["TABLE"].status == "FAILURE"
    assert by_type["TABLE"].source_definition_hash == "h1"


def test_split_statements_preserves_dollar_blocks():
    sql = """
CREATE VIEW v AS $$
SELECT 1;
SELECT 2;
$$;
GRANT SELECT ON VIEW v TO `user`;
"""
    statements = _split_statements(sql)
    assert len(statements) == 2
    assert "$$" in statements[0]
    assert statements[1].startswith("GRANT")


def test_normalize_create_adds_if_not_exists():
    from uc_sync.package_import import _normalize_create_statement

    assert "IF NOT EXISTS" in _normalize_create_statement(
        "CREATE TABLE cat.s.t (id INT);"
    )
    assert _normalize_create_statement(
        "CREATE OR REPLACE VIEW cat.s.v AS SELECT 1;"
    ).startswith("CREATE OR REPLACE VIEW")
    assert "OR REPLACE" in _normalize_create_statement(
        "CREATE VIEW cat.s.v AS SELECT 1;"
    )


def _package_with(tmp_path: Path, filename: str, ddl: str, grants: str = "") -> Path:
    root = tmp_path / "migrated"
    (root / "ddl").mkdir(parents=True)
    (root / "ddl" / filename).write_text(ddl, encoding="utf-8")
    if grants:
        (root / "grants").mkdir(parents=True)
        (root / "grants" / filename).write_text(grants, encoding="utf-8")
    return root


def test_already_exists_treated_as_success(tmp_path: Path):
    root = _package_with(
        tmp_path, "TABLE_tgt__s__t.sql", "CREATE TABLE tgt.s.t (id INT);\n"
    )

    class ExistsSql:
        def execute(self, sql: str):
            if sql.upper().startswith("CREATE"):
                raise RuntimeError("[TABLE_OR_VIEW_ALREADY_EXISTS] exists")

    result = PackageImportEngine(str(root), ExistsSql(), dry_run=False).run()[0]
    assert result.status == "SUCCESS"
    assert result.action == "SKIP_EXISTING"


def test_unqualified_create_runs_under_target_context(tmp_path: Path):
    """SHOW CREATE emits `schema.view`, which must not hit the default catalog."""
    root = _package_with(
        tmp_path,
        "VIEW_tgt__s__v.sql",
        "CREATE VIEW s.v AS SELECT 1;\n",
    )
    sql = FakeSql()
    result = PackageImportEngine(str(root), sql, dry_run=False).run()[0]
    assert result.status == "SUCCESS"
    assert sql.statements[0] == "USE CATALOG `tgt`"
    assert sql.statements[1] == "USE SCHEMA `s`"
    assert sql.statements[2].startswith("CREATE OR REPLACE VIEW")


def test_location_overlap_is_a_failure_not_a_skip(tmp_path: Path):
    root = _package_with(
        tmp_path,
        "EXTERNAL_VOLUME_tgt__s__v.sql",
        "CREATE EXTERNAL VOLUME tgt.s.v LOCATION 'abfss://x/y';\n",
    )

    class OverlapSql:
        def execute(self, sql: str):
            upper = sql.upper()
            if upper.startswith("CREATE"):
                raise RuntimeError(
                    "[INVALID_PARAMETER_VALUE.LOCATION_OVERLAP] Input path url "
                    "'abfss://x/y' overlaps with other external volumes"
                )
            if upper.startswith("DESCRIBE"):
                raise RuntimeError("[UC_VOLUME_NOT_FOUND] Volume does not exist")
            if upper.startswith("USE "):
                return None
            raise RuntimeError(f"unexpected sql: {sql}")

    result = PackageImportEngine(str(root), OverlapSql(), dry_run=False).run()[0]
    assert result.status == "FAILURE"
    assert result.error_code == "LOCATION_OVERLAP"
    assert "location_mapping_csv_path" in result.message


def test_location_overlap_skips_when_object_already_exists(tmp_path: Path):
    root = _package_with(
        tmp_path,
        "TABLE_tgt__s__t.sql",
        "CREATE TABLE tgt.s.t (id INT) LOCATION 'abfss://x/y';\n",
    )

    class OverlapExistingSql:
        def execute(self, sql: str):
            upper = sql.upper()
            if upper.startswith("CREATE"):
                raise RuntimeError(
                    "[INVALID_PARAMETER_VALUE.LOCATION_OVERLAP] Input path url "
                    "'abfss://x/y' overlaps with other external tables"
                )
            # USE / DESCRIBE succeed → object is present on target.
            return None

    result = PackageImportEngine(
        str(root), OverlapExistingSql(), dry_run=False
    ).run()[0]
    assert result.status == "SUCCESS"
    assert result.action == "SKIP_EXISTING"
    assert "already exists" in result.message.lower()


def test_skip_is_rejected_when_object_absent(tmp_path: Path):
    root = _package_with(
        tmp_path, "VOLUME_tgt__s__v.sql", "CREATE VOLUME tgt.s.v;\n"
    )

    class LyingSql:
        def execute(self, sql: str):
            if sql.upper().startswith("CREATE"):
                raise RuntimeError("[ALREADY_EXISTS] volume exists")
            if sql.upper().startswith("DESCRIBE"):
                raise RuntimeError("[UC_VOLUME_NOT_FOUND] does not exist")

    result = PackageImportEngine(str(root), LyingSql(), dry_run=False).run()[0]
    assert result.status == "FAILURE"
    assert "not present in the target" in result.message


def test_grant_not_found_fails_the_object(tmp_path: Path):
    root = _package_with(
        tmp_path,
        "VOLUME_tgt__s__v.sql",
        "CREATE VOLUME tgt.s.v;\n",
        grants="ALTER VOLUME tgt.s.v OWNER TO `someone`;\n",
    )

    class MissingAfterCreate:
        def execute(self, sql: str):
            if sql.upper().startswith("ALTER"):
                raise RuntimeError("[UC_VOLUME_NOT_FOUND] Volume does not exist")

    result = PackageImportEngine(str(root), MissingAfterCreate(), dry_run=False).run()[0]
    assert result.status == "FAILURE"
    assert "object missing after create" in result.message


def test_grant_warning_still_allows_success(tmp_path: Path):
    root = _package_with(
        tmp_path,
        "TABLE_tgt__s__t.sql",
        "CREATE TABLE tgt.s.t (id INT);\n",
        grants="ALTER TABLE tgt.s.t OWNER TO `someone`;\n",
    )

    class PermissionDenied:
        def execute(self, sql: str):
            if sql.upper().startswith("ALTER"):
                raise RuntimeError("PERMISSION_DENIED: cannot set owner")

    result = PackageImportEngine(str(root), PermissionDenied(), dry_run=False).run()[0]
    assert result.status == "SUCCESS"
    assert "grant warning" in result.message


def test_every_audit_row_has_unified_status():
    stages = {
        "INVENTORY": {"full_name": "src.s.t", "object_type": "TABLE"},
        "EXPORT": {"full_name": "src.s.t", "object_type": "TABLE", "status": "SUCCESS"},
        "MIGRATE": {
            "source_full_name": "src.s.t",
            "target_full_name": "tgt.s.t",
            "object_type": "TABLE",
            "status": "SUCCESS",
        },
        "IMPORT": {
            "source_full_name": "src.s.t",
            "target_full_name": "tgt.s.t",
            "object_type": "TABLE",
            "status": "FAILURE",
        },
        "VALIDATION": {"source_full_name": "src.s.t", "status": "MATCH"},
    }
    for stage, result in stages.items():
        row = stage_audit_row(run_id="r1", stage=stage, result=result)
        assert row["status"], f"{stage} row has no unified status"
        assert row["full_name"] == "src.s.t"
        assert set(row) == set(AUDIT_COLUMNS)

    assert stage_audit_row(
        run_id="r1", stage="IMPORT", result={"status": "SKIP_EXISTING"}
    )["status"] == "SUCCESS"


def test_add_missing_columns_upgrades_old_table():
    old = [name for name in AUDIT_COLUMNS if name not in {"status", "target_full_name"}]
    alter = add_missing_columns_sql("cat.s.audit", old)
    assert alter is not None
    assert "status STRING" in alter
    assert "target_full_name STRING" in alter
    assert add_missing_columns_sql("cat.s.audit", AUDIT_COLUMNS) is None


def test_migrate_results_carry_object_identity(tmp_path: Path):
    source = tmp_path / "export_staging" / "run1"
    (source / "ddl").mkdir(parents=True)
    (source / "ddl" / "EXTERNAL_TABLE_ril_sandbox__s__ext.sql").write_text(
        "CREATE TABLE `ril_sandbox`.`s`.`ext` (id INT);\n", encoding="utf-8"
    )
    result = MigrateExportService(
        source_root=str(source),
        target_root=str(tmp_path / "migrated"),
        catalog_mapping={"ril_sandbox": "ril_sandbox_ucsync_local"},
        run_id="run1",
    ).run(dry_run=False)

    row = next(r for r in result["results"] if r["artifact"] == "ddl")
    assert row["object_type"] == "EXTERNAL_TABLE"
    assert row["source_full_name"] == "ril_sandbox.s.ext"
    assert row["target_full_name"] == "ril_sandbox_ucsync_local.s.ext"
    # Success rows must not put paths into the error column.
    assert row["error_message"] == ""

    audit = stage_audit_row(run_id="r1", stage="MIGRATE", result=row)
    assert audit["status"] == "SUCCESS"
    assert audit["full_name"] == "ril_sandbox.s.ext"
    assert audit["error_message"] is None


def test_stage_audit_and_state_rows():
    audit = stage_audit_row(
        run_id="r1",
        stage="IMPORT",
        result={
            "object_type": "TABLE",
            "source_full_name": "src.s.t",
            "target_full_name": "tgt.s.t",
            "status": "SUCCESS",
            "message": "ok",
        },
    )
    assert audit["import_status"] == "SUCCESS"
    assert audit["full_name"] == "src.s.t"

    pending = stage_audit_row(
        run_id="r1",
        stage="EXPORT",
        result={"full_name": "src.s.t", "object_type": "TABLE", "status": "SUCCESS"},
    )
    assert pending["export_status"] == "SUCCESS"
    assert pending["import_status"] == "PENDING"

    state = state_row_from_import(
        batch_id="b1",
        run_id="r1",
        result={
            "object_type": "TABLE",
            "source_full_name": "src.s.t",
            "target_full_name": "tgt.s.t",
            "status": "FAILURE",
            "error_code": "RuntimeError",
            "message": "nope",
        },
        ran_by="tester",
        utility_version="0.0.0",
    )
    assert state["last_sync_status"] == "FAILURE"
    assert state["batch_id"] == "b1"
    assert state["last_synced_by"] == "tester"
