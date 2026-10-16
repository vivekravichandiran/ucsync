"""Regression tests for 1.2.0:

* ``_split_statements`` honours backslash escapes inside string literals — a
  ``SHOW CREATE TABLE`` comment such as ``'Today\\'s flag … -> Red; …'`` was split
  at its ``;`` and the CREATE failed with PARSE_SYNTAX_ERROR.
* ``filter_tables`` (table allowlist) now applies at inventory too, so unselected
  tables are never read/exported; import accepts source OR target names.
"""

from __future__ import annotations

from pathlib import Path

from uc_sync.config import from_sources
from uc_sync.filters import allowed, table_selected
from uc_sync.install_jobs import load_job_spec
from uc_sync.inventory import InventoryService
from uc_sync.models import ObjectType, UCObject
from uc_sync.package_import import PackageImportEngine, _split_statements
from uc_sync.rewrite import strip_managed_storage_clauses

# ---------------------------------------------------------------------------
# _split_statements: backslash-escaped quotes
# ---------------------------------------------------------------------------

# Shape of the real failing column comment: an escaped apostrophe, then a ';'.
_COLUMN_COMMENT_DDL = (
    "CREATE TABLE IF NOT EXISTS `c`.`s`.`5g_cell_level` (\n"
    "  cell_flags STRING COLLATE UTF8_BINARY COMMENT '9-char bitmap (y=Red, n=Green)',\n"
    "  cssr_defaulting_cell_flag STRING COLLATE UTF8_BINARY COMMENT "
    "'Today\\'s CSSR flag - ER: denom > 50 AND cssr < 99 -> Red; NK: attempts > 50 -> Red',\n"
    "  cell_name STRING COLLATE UTF8_BINARY COMMENT 'concat(ne_name, \\'_\\', cnum)')\n"
    "USING delta\n"
    "PARTITIONED BY (cell_name)\n"
    "TBLPROPERTIES (\n"
    "  'delta.enableDeletionVectors' = 'true');\n"
)

# Shape of the real failing table comment: wrapped in literal quotes, with a ';'.
_TABLE_COMMENT_DDL = (
    "CREATE TABLE IF NOT EXISTS `c`.`s`.`site_actions_weekly` (\n"
    "  site_id STRING COLLATE UTF8_BINARY COMMENT 'Site identifier')\n"
    "USING delta\n"
    "COMMENT '\\'Weekly site actions. Built from anomalies; excludes pairs already "
    "actioned.\\''\n"
    "TBLPROPERTIES (\n"
    "  'delta.enableDeletionVectors' = 'true');\n"
)


def test_escaped_quote_then_semicolon_in_column_comment_is_one_statement():
    statements = _split_statements(_COLUMN_COMMENT_DDL)
    assert len(statements) == 1
    assert statements[0] == _COLUMN_COMMENT_DDL.strip()


def test_escaped_quote_then_semicolon_in_table_comment_is_one_statement():
    statements = _split_statements(_TABLE_COMMENT_DDL)
    assert len(statements) == 1
    assert statements[0] == _TABLE_COMMENT_DDL.strip()


def test_full_import_pipeline_keeps_comment_ddl_intact():
    """Export header + rewrite + split — the exact path the import replays."""
    header = (
        "-- TABLE c.s.t\n-- source=SHOW_CREATE\n"
        "-- captured via SHOW CREATE TABLE `c`.`s`.`t`\n"
    )
    for ddl in (_COLUMN_COMMENT_DDL, _TABLE_COMMENT_DDL):
        rewritten = strip_managed_storage_clauses(header + ddl, "TABLE")
        statements = _split_statements(rewritten)
        assert len(statements) == 1
        assert "COLLATE" not in statements[0]
        assert statements[0].rstrip().endswith("'true');")


def test_statement_boundaries_still_split_after_escaped_quote():
    sql = "COMMENT ON TABLE t IS 'it\\'s; fine';\nGRANT SELECT ON TABLE t TO `g`;\n"
    assert _split_statements(sql) == [
        "COMMENT ON TABLE t IS 'it\\'s; fine';",
        "GRANT SELECT ON TABLE t TO `g`;",
    ]


def test_doubled_quote_and_double_quoted_escapes_still_work():
    assert len(_split_statements("SELECT 'it''s; ok';")) == 1
    assert len(_split_statements('SELECT "say \\"hi\\"; ok";')) == 1
    # An escaped backslash before the closing quote really closes the literal.
    assert _split_statements("SELECT 'a\\\\'; SELECT 2;") == [
        "SELECT 'a\\\\';",
        "SELECT 2;",
    ]


# ---------------------------------------------------------------------------
# filter_tables: shared matcher + inventory (source-side) scoping
# ---------------------------------------------------------------------------


def _obj(object_type, cat, sch=None, name=None):
    full = ".".join(p for p in (cat, sch, name) if p)
    return UCObject(object_type, name or sch or cat, full, catalog=cat, schema=sch)


def test_table_selected_fq_bare_and_any_candidate():
    assert table_selected([], "c.s.t") is True
    assert table_selected(["c.s.t"], "c.s.t") is True
    assert table_selected(["t"], "c.s.t") is True
    assert table_selected(["c.s.t"], "c.s.other") is False
    # Any candidate name matching selects (target name, then source name).
    assert table_selected(["src.s.t"], "tgt.s.t", "src.s.t") is True
    assert table_selected([" c.s.t ", ""], "c.s.t") is True


def test_from_sources_parses_filter_tables_csv():
    cfg = from_sources({"catalogs": "c", "filter_tables": "c.s.a, b ,"})
    assert cfg.filter_tables == ["c.s.a", "b"]
    assert from_sources({"catalogs": "c", "filter_tables": ""}).filter_tables == []


def test_allowed_narrows_only_table_like_securables():
    cfg = from_sources({"catalogs": "c", "filter_tables": "c.s.keep"})
    assert allowed(_obj(ObjectType.TABLE, "c", "s", "keep"), cfg) is True
    assert allowed(_obj(ObjectType.TABLE, "c", "s", "drop"), cfg) is False
    assert allowed(_obj(ObjectType.VIEW, "c", "s", "v"), cfg) is False
    assert allowed(_obj(ObjectType.STREAMING_TABLE, "c", "s", "st"), cfg) is False
    # Prerequisites always come along.
    assert allowed(_obj(ObjectType.CATALOG, "c"), cfg) is True
    assert allowed(_obj(ObjectType.SCHEMA, "c", "s"), cfg) is True
    assert allowed(_obj(ObjectType.FUNCTION, "c", "s", "mask_fn"), cfg) is True
    assert allowed(_obj(ObjectType.VOLUME, "c", "s", "vol"), cfg) is True


def test_allowed_blank_filter_keeps_every_table():
    cfg = from_sources({"catalogs": "c"})
    assert allowed(_obj(ObjectType.TABLE, "c", "s", "anything"), cfg) is True


class _FakeClient:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, path, key, **params):
        for item in self.pages.get(key, []):
            if key == "schemas" and item["catalog_name"] != params.get("catalog_name"):
                continue
            yield item

    def get(self, path):
        return {}


def test_inventory_run_only_returns_selected_tables():
    client = _FakeClient({
        "catalogs": [{"name": "c"}],
        "schemas": [{"name": "s", "catalog_name": "c"}],
        "tables": [
            {"name": "keep", "full_name": "c.s.keep", "table_type": "MANAGED",
             "columns": []},
            {"name": "drop", "full_name": "c.s.drop", "table_type": "MANAGED",
             "columns": []},
            {"name": "v", "full_name": "c.s.v", "table_type": "VIEW", "columns": []},
        ],
        "volumes": [],
        "functions": [],
    })
    cfg = from_sources({"catalogs": "c", "filter_tables": "c.s.keep"}, {})
    names = {o.full_name for o in InventoryService(client, cfg).run()}
    assert {"c", "c.s", "c.s.keep"} <= names
    assert "c.s.drop" not in names
    assert "c.s.v" not in names


def test_inventory_filter_drops_unselected_report_only_table_kinds():
    """Lakebase / vector-index / pipeline tables follow the allowlist like any table."""
    tables = [
        {"name": "orders", "full_name": "c.s.orders", "table_type": "MANAGED",
         "columns": []},
        {"name": "clicks_stream", "full_name": "c.s.clicks_stream",
         "table_type": "STREAMING_TABLE", "columns": []},
        {"name": "orders_vs_index", "full_name": "c.s.orders_vs_index",
         "table_type": "FOREIGN", "data_source_format": "VECTOR_INDEX_FORMAT",
         "columns": []},
        {"name": "app_db_users", "full_name": "c.s.app_db_users",
         "table_type": "FOREIGN", "data_source_format": "POSTGRESQL_FORMAT",
         "columns": []},
        {"name": "event_log", "full_name": "c.s.event_log", "table_type": "MANAGED",
         "pipeline_id": "p-1", "columns": []},
    ]
    pages = {
        "catalogs": [{"name": "c"}],
        "schemas": [{"name": "s", "catalog_name": "c"}],
        "tables": tables, "volumes": [], "functions": [],
    }

    def run(filter_tables):
        cfg = from_sources({"catalogs": "c", "filter_tables": filter_tables}, {})
        return {
            o.full_name: o.object_type
            for o in InventoryService(_FakeClient(pages), cfg).run()
        }

    # Blank filter: every kind is still inventoried (and classified as before).
    everything = run("")
    assert everything["c.s.orders_vs_index"] == ObjectType.VECTOR_INDEX
    assert everything["c.s.app_db_users"] == ObjectType.LAKEBASE_TABLE
    assert everything["c.s.event_log"] == ObjectType.PIPELINE_TABLE
    assert "c.s.clicks_stream" in everything

    # Only the selected table survives; none of the others reappears as a manual step.
    narrowed = run("c.s.orders")
    assert "c.s.orders" in narrowed
    for dropped in ("c.s.clicks_stream", "c.s.orders_vs_index",
                    "c.s.app_db_users", "c.s.event_log"):
        assert dropped not in narrowed, dropped

    # A report-only kind that IS listed is kept (still report-only, never created).
    assert run("c.s.orders_vs_index")["c.s.orders_vs_index"] == ObjectType.VECTOR_INDEX


def test_inventory_and_e2e_jobs_pass_filter_tables_to_inventory_task():
    for job in ("e2e_live", "e2e_dry_run", "airgap_source"):
        spec = load_job_spec(job, {
            "notebook_dir": "/nb", "job_name_prefix": "X",
            "filter_tables": "c.s.keep",
        })
        inv = next(t for t in spec["tasks"] if t["task_key"] == "inventory")
        assert inv["notebook_task"]["base_parameters"]["filter_tables"] == "c.s.keep"


# ---------------------------------------------------------------------------
# filter_tables at import: source-catalog names work under a catalog rename
# ---------------------------------------------------------------------------


class _FakeSql:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement):
        self.statements.append(statement)
        return []


def test_import_filter_accepts_source_name_under_catalog_rename(tmp_path: Path):
    root = tmp_path / "migrated"
    (root / "ddl").mkdir(parents=True)
    (root / "inventory").mkdir()
    for fn, ddl in (
        ("CATALOG_src.sql", "CREATE CATALOG IF NOT EXISTS `src`;\n"),
        ("SCHEMA_src__s.sql", "CREATE SCHEMA IF NOT EXISTS `src`.`s`;\n"),
        ("TABLE_src__s__keep.sql", "CREATE TABLE IF NOT EXISTS `src`.`s`.`keep` (id INT);\n"),
        ("TABLE_src__s__drop.sql", "CREATE TABLE IF NOT EXISTS `src`.`s`.`drop` (id INT);\n"),
    ):
        (root / "ddl" / fn).write_text(ddl, encoding="utf-8")
    (root / "inventory" / "objects.json").write_text("[]", encoding="utf-8")

    for selection in (["src.s.keep"], ["tgt.s.keep"], ["keep"]):
        sql = _FakeSql()
        results = PackageImportEngine(
            str(root), sql, dry_run=False, select_tables=selection,
            catalog_mapping={"src": "tgt"},
        ).run()
        by_name = {r.target_full_name: r for r in results}
        assert by_name["tgt.s.keep"].action != "SKIP_FILTERED", selection
        assert by_name["tgt.s.keep"].status == "SUCCESS", selection
        assert by_name["tgt.s.drop"].action == "SKIP_FILTERED", selection


# ---------------------------------------------------------------------------
# filter_tables on an incremental run: out-of-scope objects are not "deleted"
# ---------------------------------------------------------------------------


def test_incremental_filtered_run_reports_only_in_scope_deletions(tmp_path: Path):
    """The baseline holds the prior FULL run; this run's inventory holds only the
    filtered table. Unlisted tables must not read as deleted in source — but a
    listed table that really is gone, and a gone function, still do."""
    root = tmp_path / "migrated"
    (root / "ddl").mkdir(parents=True)
    (root / "inventory").mkdir()
    (root / "ddl" / "TABLE_src__s__keep.sql").write_text(
        "CREATE TABLE IF NOT EXISTS `src`.`s`.`keep` (id INT);\n", encoding="utf-8"
    )
    (root / "inventory" / "objects.json").write_text(
        '[{"object_type": "TABLE", "full_name": "src.s.keep", "name": "keep"}]',
        encoding="utf-8",
    )
    prior = {
        name: {"object_type": otype, "ddl_hash": "x", "governance_hash": "y",
               "grants": {}, "last_action": "created"}
        for name, otype in (
            ("src.s.keep", "TABLE"),
            ("src.s.other_table", "TABLE"),      # not listed → out of scope
            ("src.s.other_view", "VIEW"),        # not listed → out of scope
            ("src.s.listed_gone", "VIEW"),       # listed, really deleted
            ("src.s.gone_fn", "FUNCTION"),       # functions are always in scope
        )
    }
    selection = ["src.s.keep", "tgt.s.listed_gone"]
    engine = PackageImportEngine(
        str(root), _FakeSql(), dry_run=False, select_tables=selection,
        catalog_mapping={"src": "tgt"}, prior_state=prior,
    )
    engine.run()
    absent = {
        r["object"] for r in engine.delta_plan.delta_rows()
        if r["action"] == "SOURCE_ABSENT"
    }
    assert absent == {"src.s.listed_gone", "src.s.gone_fn"}

    # Blank filter: unchanged behaviour — every missing object is reported.
    engine = PackageImportEngine(
        str(root), _FakeSql(), dry_run=False, prior_state=prior,
    )
    engine.run()
    absent = {
        r["object"] for r in engine.delta_plan.delta_rows()
        if r["action"] == "SOURCE_ABSENT"
    }
    assert absent == {
        "src.s.other_table", "src.s.other_view", "src.s.listed_gone", "src.s.gone_fn",
    }
