"""Unit tests for rewrite → migrate → package import → state helpers."""

from __future__ import annotations

import json
from pathlib import Path

from uc_sync.audit import (
    AUDIT_COLUMNS,
    add_missing_columns_sql,
    stage_audit_row,
)
from uc_sync.mapping import MappingResolver
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


def _resolver_mappings():
    """Mappings dict with one longest-prefix ADLS path rewrite."""
    return {
        "location_mappings": [
            {
                "source_location": "abfss://src@acct.dfs.core.windows.net/root",
                "target_location": "abfss://tgt@acct.dfs.core.windows.net/migrated",
            }
        ]
    }


def test_rewrite_text_paths_only_leaves_identifiers_untouched():
    """Names are never rewritten; only storage URLs are mapped to target paths."""
    resolver = MappingResolver(_resolver_mappings())
    text = (
        "CREATE TABLE `ril_sandbox`.`s`.`t1` "
        "LOCATION 'abfss://src@acct.dfs.core.windows.net/root/t1';"
    )
    out = rewrite_text(text, location_resolver=resolver)
    # Identifiers preserved verbatim (no catalog renaming).
    assert "`ril_sandbox`.`s`.`t1`" in out
    # Storage URL rewritten to the mapped target path.
    assert "abfss://tgt@acct.dfs.core.windows.net/migrated/t1" in out
    # With no resolver, text is returned unchanged.
    assert rewrite_text(text) == text


def test_migrate_rewrites_paths_and_preserves_names(tmp_path: Path):
    source = tmp_path / "export_staging" / "run1"
    (source / "ddl").mkdir(parents=True)
    (source / "inventory").mkdir()
    (source / "ddl" / "EXTERNAL_TABLE_ril_sandbox__s__t.sql").write_text(
        "CREATE TABLE IF NOT EXISTS `ril_sandbox`.`s`.`t` (id INT) "
        "LOCATION 'abfss://src@acct.dfs.core.windows.net/root/t';\n",
        encoding="utf-8",
    )
    (source / "inventory" / "objects.json").write_text(
        json.dumps(
            [
                {
                    "object_type": "EXTERNAL_TABLE",
                    "full_name": "ril_sandbox.s.t",
                    "catalog": "ril_sandbox",
                    "storage_location": "abfss://src@acct.dfs.core.windows.net/root/t",
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
        mappings=_resolver_mappings(),
        run_id="run1",
    ).run(dry_run=False)

    assert result["migrated"] >= 2
    # File name is NOT renamed — catalog names are never mapped.
    migrated_ddl = target / "ddl" / "EXTERNAL_TABLE_ril_sandbox__s__t.sql"
    assert migrated_ddl.exists()
    ddl_text = migrated_ddl.read_text(encoding="utf-8")
    assert "`ril_sandbox`.`s`.`t`" in ddl_text
    # External-table storage path IS rewritten to the target ADLS location.
    assert "abfss://tgt@acct.dfs.core.windows.net/migrated/t" in ddl_text
    inv = json.loads((target / "inventory" / "objects.json").read_text(encoding="utf-8"))
    assert inv[0]["full_name"] == "ril_sandbox.s.t"
    assert inv[0]["source_full_name"] == "ril_sandbox.s.t"
    assert inv[0]["target_full_name"] == "ril_sandbox.s.t"
    assert inv[0]["catalog"] == "ril_sandbox"
    assert inv[0]["storage_location"] == (
        "abfss://tgt@acct.dfs.core.windows.net/migrated/t"
    )


def test_migrate_rewrites_each_credential_to_its_own_connector(tmp_path: Path):
    """Per-catalog credentials must each be rewritten to the target connector of
    the source location they back — not all to the first mapping row's connector
    (regression: finance+sales credentials both got the gov connector, so their
    external locations failed UC's managed-identity validation)."""
    source = tmp_path / "export_staging" / "run1"
    (source / "ddl").mkdir(parents=True)
    (source / "inventory").mkdir()
    # Two credentials, each backing an external location in a distinct account.
    for cred, connector in (
        ("fin_cred", "src-fin"),
        ("sal_cred", "src-sal"),
    ):
        (source / "ddl" / f"STORAGE_CREDENTIAL_{cred}.sql").write_text(
            f"CREATE STORAGE CREDENTIAL `{cred}` WITH AZURE_MANAGED_IDENTITY "
            f"(ACCESS_CONNECTOR_ID = '/subscriptions/SRC/connectors/{connector}');\n",
            encoding="utf-8",
        )
    (source / "inventory" / "objects.json").write_text(
        json.dumps(
            [
                {
                    "object_type": "EXTERNAL_LOCATION",
                    "full_name": "fin_el",
                    "storage_location": "abfss://data@fin.dfs.core.windows.net",
                    "storage_credential_name": "fin_cred",
                },
                {
                    "object_type": "EXTERNAL_LOCATION",
                    "full_name": "sal_el",
                    "storage_location": "abfss://data@sal.dfs.core.windows.net",
                    "storage_credential_name": "sal_cred",
                },
            ]
        ),
        encoding="utf-8",
    )
    mappings = {
        "location_mappings": [
            {
                "source_location": "abfss://data@fin.dfs.core.windows.net",
                "target_location": "abfss://data@tgtfin.dfs.core.windows.net",
                "target_access_connector_id": "/subscriptions/TGT/connectors/tgt-fin",
            },
            {
                "source_location": "abfss://data@sal.dfs.core.windows.net",
                "target_location": "abfss://data@tgtsal.dfs.core.windows.net",
                "target_access_connector_id": "/subscriptions/TGT/connectors/tgt-sal",
            },
        ]
    }
    target = tmp_path / "migrated"
    MigrateExportService(
        source_root=str(source),
        target_root=str(target),
        mappings=mappings,
        run_id="run1",
    ).run(dry_run=False)

    fin = (target / "ddl" / "STORAGE_CREDENTIAL_fin_cred.sql").read_text()
    sal = (target / "ddl" / "STORAGE_CREDENTIAL_sal_cred.sql").read_text()
    assert "/subscriptions/TGT/connectors/tgt-fin" in fin
    assert "/subscriptions/TGT/connectors/tgt-sal" in sal
    # The finance credential must NOT get the sales connector, and vice versa.
    assert "tgt-sal" not in fin
    assert "tgt-fin" not in sal


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


def test_import_scope_filter_selects_subset(tmp_path: Path):
    """The import scope filter creates only in-scope objects; a table outside the
    filter is SKIP_FILTERED, while the catalog/schema it needs still come along."""
    root = tmp_path / "migrated"
    (root / "ddl").mkdir(parents=True)
    (root / "inventory").mkdir()
    for fn, ddl in (
        ("CATALOG_c.sql", "CREATE CATALOG IF NOT EXISTS `c`;\n"),
        ("SCHEMA_c__s.sql", "CREATE SCHEMA IF NOT EXISTS `c`.`s`;\n"),
        ("TABLE_c__s__keep.sql", "CREATE TABLE IF NOT EXISTS `c`.`s`.`keep` (id INT);\n"),
        ("TABLE_c__s__drop.sql", "CREATE TABLE IF NOT EXISTS `c`.`s`.`drop` (id INT);\n"),
        ("FUNCTION_c__s__fn.sql", "CREATE FUNCTION IF NOT EXISTS `c`.`s`.`fn`() RETURNS INT RETURN 1;\n"),
    ):
        (root / "ddl" / fn).write_text(ddl, encoding="utf-8")
    (root / "inventory" / "objects.json").write_text("[]", encoding="utf-8")

    sql = FakeSql()
    results = PackageImportEngine(
        str(root), sql, dry_run=False, select_tables=["c.s.keep"],
    ).run()
    by_name = {r.target_full_name: r for r in results}

    # The selected table + its parents + non-table securables are created.
    for name in ("c", "c.s", "c.s.keep", "c.s.fn"):  # function comes along
        assert by_name[name].action != "SKIP_FILTERED"
        assert by_name[name].status == "SUCCESS"
    # The unselected table is skipped, and its CREATE never ran.
    assert by_name["c.s.drop"].action == "SKIP_FILTERED"
    assert not any("`drop`" in s for s in sql.statements)


def test_rewrite_catalog_references():
    from uc_sync.package_import import rewrite_catalog_references
    m = {"src_cat": "tgt_cat"}
    assert rewrite_catalog_references("CREATE CATALOG src_cat", m) == "CREATE CATALOG tgt_cat"
    assert rewrite_catalog_references(
        "CREATE TABLE src_cat.s.t (id INT)", m) == "CREATE TABLE tgt_cat.s.t (id INT)"
    assert rewrite_catalog_references(
        "CREATE TABLE `src_cat`.`s`.`t`", m) == "CREATE TABLE `tgt_cat`.`s`.`t`"
    assert rewrite_catalog_references("USE CATALOG src_cat", m) == "USE CATALOG tgt_cat"
    # unmapped catalog and substring look-alikes are untouched (word boundary)
    assert rewrite_catalog_references("CREATE TABLE other.s.t", m) == "CREATE TABLE other.s.t"
    assert rewrite_catalog_references("src_cat_extra.s.t", m) == "src_cat_extra.s.t"


def test_import_applies_catalog_mapping(tmp_path: Path):
    """A catalog mapping replays the (source-named) bundle under the target
    catalog: every executed statement is rewritten, and result tracking shows
    the target name."""
    root = tmp_path / "migrated"
    (root / "ddl").mkdir(parents=True)
    (root / "inventory").mkdir()
    (root / "ddl" / "CATALOG_src_cat.sql").write_text(
        "CREATE CATALOG src_cat;\n", encoding="utf-8")
    (root / "ddl" / "TABLE_src_cat__s__t.sql").write_text(
        "CREATE TABLE src_cat.s.t (id INT);\n", encoding="utf-8")
    (root / "inventory" / "objects.json").write_text("[]", encoding="utf-8")

    sql = FakeSql()
    results = PackageImportEngine(
        str(root), sql, dry_run=False, catalog_mapping={"src_cat": "tgt_cat"},
    ).run()

    assert any("CATALOG" in s.upper() and "tgt_cat" in s for s in sql.statements)
    # The table create carries the fully-qualified 3-level target name.
    assert any("`tgt_cat`.`s`.`t`" in s for s in sql.statements)
    # source catalog name never reaches the executor
    assert not any("src_cat" in s for s in sql.statements)
    by = {r.object_type: r for r in results}
    assert by["TABLE"].target_full_name == "tgt_cat.s.t"


def test_import_results_produce_audit_and_state_rows():
    """The 03_Import ops-table glue: every PackageImportResult must convert into a
    well-formed uc_sync_audit (IMPORT) row and a uc_sync_state upsert row."""
    from uc_sync.audit import AUDIT_COLUMNS, stage_audit_row
    from uc_sync.package_import import PackageImportResult
    from uc_sync.sync_state import STATE_COLUMNS, state_row_from_import

    results = [
        PackageImportResult(
            object_type="TABLE",
            source_full_name="src.s.t",
            target_full_name="tgt.s.t",
            full_name="tgt.s.t",
            action="CREATE",
            status="SUCCESS",
            source_definition_hash="h1",
        ),
        PackageImportResult(
            object_type="VIEW",
            source_full_name="src.s.v",
            target_full_name="tgt.s.v",
            full_name="tgt.s.v",
            action="CREATE",
            status="FAILURE",
            error_code="BOOM",
            message="nope",
        ),
    ]
    for r in results:
        rd = r.to_dict()
        audit = stage_audit_row(run_id="r1", stage="IMPORT", result=rd)
        assert set(audit) == set(AUDIT_COLUMNS)
        assert audit["operation_mode"] == "IMPORT"
        assert audit["import_status"] == audit["status"]
        assert audit["target_full_name"] == r.target_full_name

        state = state_row_from_import(
            batch_id="b1", run_id="r1", result=rd, ran_by="me", utility_version="9.9",
        )
        assert set(state) == set(STATE_COLUMNS)
        assert state["source_full_name"] == "src.s.t" if r.status == "SUCCESS" else True
        assert state["last_synced_by"] == "me"

    ok = stage_audit_row(run_id="r1", stage="IMPORT", result=results[0].to_dict())
    bad = state_row_from_import(
        batch_id="b1", run_id="r1", result=results[1].to_dict(), ran_by="me",
        utility_version="9.9",
    )
    assert ok["status"] == "SUCCESS"
    assert bad["last_sync_status"] == "FAILURE"


def _owner_package(tmp_path: Path) -> Path:
    """An external location + a catalog whose managed location is that EL, with
    an `OWNER TO <source-owner>` grant on the EL (as the export bundle emits)."""
    root = tmp_path / "migrated"
    (root / "ddl").mkdir(parents=True)
    (root / "grants").mkdir(parents=True)
    (root / "inventory").mkdir(parents=True)
    (root / "ddl" / "EXTERNAL_LOCATION_ai27_el.sql").write_text(
        "CREATE EXTERNAL LOCATION `ai27_el` URL 'abfss://data@x/' "
        "WITH (STORAGE CREDENTIAL `ai27_cred`);\n",
        encoding="utf-8",
    )
    (root / "ddl" / "CATALOG_ai27_cat.sql").write_text(
        "CREATE CATALOG `ai27_cat` MANAGED LOCATION 'abfss://data@x/';\n",
        encoding="utf-8",
    )
    (root / "grants" / "EXTERNAL_LOCATION_ai27_el.sql").write_text(
        "ALTER EXTERNAL LOCATION `ai27_el` OWNER TO `source_owner@databricks.com`;\n",
        encoding="utf-8",
    )
    (root / "inventory" / "objects.json").write_text("[]", encoding="utf-8")
    return root


def test_owner_transfer_deferred_until_after_creates():
    """`ALTER … OWNER TO` must run AFTER the catalog is created, not right after
    the EL — otherwise the run principal loses CREATE MANAGED STORAGE on the EL
    before the catalog (the PERMISSION_DENIED cascade we hit)."""
    from uc_sync.package_import import _is_owner_statement, _owner_statement_target

    # helper-level checks
    stmt = "ALTER EXTERNAL LOCATION `ai27_el` OWNER TO `o@x`;"
    assert _is_owner_statement(stmt)
    assert _owner_statement_target(stmt) == ("EXTERNAL_LOCATION", "ai27_el")
    assert _owner_statement_target("ALTER TABLE `c`.`s`.`t` OWNER TO `g`") == (
        "TABLE", "c.s.t")
    assert not _is_owner_statement("GRANT SELECT ON TABLE c.s.t TO `u`")


def test_owner_transfer_runs_last(tmp_path: Path):
    root = _owner_package(tmp_path)
    sql = FakeSql()
    engine = PackageImportEngine(str(root), sql, dry_run=False)
    results = engine.run()

    owner_idx = next(i for i, s in enumerate(sql.statements) if "OWNER TO" in s.upper())
    cat_idx = next(i for i, s in enumerate(sql.statements) if "CREATE CATALOG" in s.upper())
    el_idx = next(
        i for i, s in enumerate(sql.statements) if "CREATE EXTERNAL LOCATION" in s.upper()
    )
    # Ownership transfer happens after BOTH the EL and the catalog were created.
    assert owner_idx > cat_idx > el_idx
    assert engine._ownership_transferred == 1
    assert engine._ownership_skipped == 0
    by = {r.object_type: r for r in results}
    assert by["EXTERNAL_LOCATION"].status == "SUCCESS"
    assert by["CATALOG"].status == "SUCCESS"


def test_missing_owner_is_a_warning_not_a_failure(tmp_path: Path):
    """A source owner absent on the target degrades to a warning; the objects
    stay SUCCESS and the run does not raise."""
    root = _owner_package(tmp_path)
    sql = FakeSql(fail_on="OWNER TO")  # simulate: principal doesn't exist on target
    engine = PackageImportEngine(str(root), sql, dry_run=False)
    results = engine.run()  # must not raise

    by = {r.object_type: r for r in results}
    assert by["EXTERNAL_LOCATION"].status == "SUCCESS"
    assert by["CATALOG"].status == "SUCCESS"
    assert engine._ownership_skipped == 1
    assert engine._ownership_transferred == 0
    # No object result was marked FAILURE because of the ownership hand-off.
    assert all(r.status != "FAILURE" for r in results)


def test_regular_grants_still_applied_inline(tmp_path: Path):
    """Only OWNER TO is deferred; ordinary GRANTs still run during the object's
    own step and are never queued."""
    root = _package_with(
        tmp_path,
        "TABLE_c__s__t.sql",
        "CREATE TABLE c.s.t (id INT);\n",
        grants="GRANT SELECT ON TABLE c.s.t TO `analyst`;\n",
    )
    sql = FakeSql()
    engine = PackageImportEngine(str(root), sql, dry_run=False)
    engine.run()
    assert any("GRANT SELECT" in s.upper() for s in sql.statements)
    assert engine._deferred_owner == []


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


def test_unqualified_create_is_rewritten_to_three_level_name(tmp_path: Path):
    """SHOW CREATE emits `schema.view`; replay rewrites it to the fully-qualified
    3-level name and issues NO USE CATALOG/SCHEMA — so it resolves correctly on any
    (stateless) executor, never against a default catalog."""
    root = _package_with(
        tmp_path,
        "VIEW_tgt__s__v.sql",
        "CREATE VIEW s.v AS SELECT 1;\n",
    )
    sql = FakeSql()
    result = PackageImportEngine(str(root), sql, dry_run=False).run()[0]
    assert result.status == "SUCCESS"
    # No USE context statements at all.
    assert not any(s.upper().startswith("USE ") for s in sql.statements)
    # The create carries its own 3-level namespace.
    create = next(
        s for s in sql.statements if s.upper().lstrip().startswith("CREATE")
    )
    assert create.startswith("CREATE OR REPLACE VIEW `tgt`.`s`.`v`")


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
        # A non-owner grant: OWNER TO is now deferred, but ordinary grants still
        # run inline, so a NOT_FOUND here still proves the object never created.
        grants="GRANT READ VOLUME ON VOLUME tgt.s.v TO `someone`;\n",
    )

    class MissingAfterCreate:
        def execute(self, sql: str):
            if sql.upper().startswith("GRANT"):
                raise RuntimeError("[UC_VOLUME_NOT_FOUND] Volume does not exist")

    result = PackageImportEngine(str(root), MissingAfterCreate(), dry_run=False).run()[0]
    assert result.status == "FAILURE"
    assert "object missing after create" in result.message


def test_grant_warning_still_allows_success(tmp_path: Path):
    root = _package_with(
        tmp_path,
        "TABLE_tgt__s__t.sql",
        "CREATE TABLE tgt.s.t (id INT);\n",
        grants="GRANT SELECT ON TABLE tgt.s.t TO `someone`;\n",
    )

    class PermissionDenied:
        def execute(self, sql: str):
            if sql.upper().startswith("GRANT"):
                raise RuntimeError("PERMISSION_DENIED: cannot grant")

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
        run_id="run1",
    ).run(dry_run=False)

    row = next(r for r in result["results"] if r["artifact"] == "ddl")
    assert row["object_type"] == "EXTERNAL_TABLE"
    assert row["source_full_name"] == "ril_sandbox.s.ext"
    # Names are never mapped: target identity == source identity.
    assert row["target_full_name"] == "ril_sandbox.s.ext"
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
