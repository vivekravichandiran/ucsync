"""Retry-failed-only import mode (backlog item 4).

Given a bundle + a prior-run baseline where a mix of objects are clean and failed, a
retry-failed-only run replays ONLY the failed set (+ required parents) and leaves every
clean object untouched (no result row → prior state preserved). Off = today's behavior.
"""

from __future__ import annotations

from pathlib import Path

from uc_sync.config import from_sources
from uc_sync.package_import import PackageImportEngine
from uc_sync.sync_state import (
    SyncStateService, STATE_COLUMNS, FACET_APPLIED, FACET_FAILED,
)
from tests.test_failclosed_governance import GovSql, _write


# --- config toggle ----------------------------------------------------------

def test_config_retry_failed_only_toggle():
    assert from_sources({"catalogs": "c"}).retry_failed_only is False
    assert from_sources({"catalogs": "c", "retry_failed_only": "true"}).retry_failed_only is True


# --- engine scope restriction -----------------------------------------------

def _bundle(root: Path):
    _write(root, "ddl/CATALOG_c.sql", "CREATE CATALOG `c`;\n")
    _write(root, "ddl/SCHEMA_c__s.sql", "CREATE SCHEMA `c`.`s`;\n")
    _write(root, "ddl/TABLE_c__s__ok.sql", "CREATE TABLE `c`.`s`.`ok` (id INT);\n")
    _write(root, "ddl/TABLE_c__s__bad.sql", "CREATE TABLE `c`.`s`.`bad` (id INT);\n")
    _write(root, "ddl/FUNCTION_c__s__f.sql",
           "CREATE FUNCTION `c`.`s`.`f`(v INT) RETURNS INT RETURN v;\n")
    _write(root, "inventory/objects.json", "[]")


def test_retry_replays_only_failed_set_plus_parents(tmp_path):
    root = tmp_path / "migrated"
    _bundle(root)
    sql = GovSql()
    # Only c.s.bad is in the failed set → only it (+ catalog/schema/functions) is touched.
    engine = PackageImportEngine(
        str(root), sql, dry_run=False,
        retry_failed_only=True,
        retry_failed_set={"c.s.bad"},
    )
    results = engine.run()
    touched = {r.target_full_name for r in results}
    assert "c.s.bad" in touched
    # The clean table is NOT processed (no result row at all → state preserved).
    assert "c.s.ok" not in touched
    # Required parents flow through so the failed object can be (re)built.
    assert "c" in touched            # catalog ancestor
    assert "c.s" in touched          # schema ancestor
    assert "c.s.f" in touched        # functions always allowed (inline-mask backing)
    # The failed table's CREATE actually ran; the clean one was never created.
    assert "c.s.bad" in sql.tables
    assert "c.s.ok" not in sql.tables


def test_retry_off_processes_everything(tmp_path):
    root = tmp_path / "migrated"
    _bundle(root)
    results = PackageImportEngine(str(root), GovSql(), dry_run=False).run()
    touched = {r.target_full_name for r in results}
    assert {"c.s.ok", "c.s.bad", "c.s.f"} <= touched


def test_retry_scope_ok_predicate(tmp_path):
    root = tmp_path / "migrated"
    _bundle(root)
    e = PackageImportEngine(str(root), GovSql(), dry_run=False,
                            retry_failed_only=True, retry_failed_set={"c.s.bad"})
    assert e._retry_scope_ok("TABLE", "c.s.bad") is True
    assert e._retry_scope_ok("TABLE", "c.s.ok") is False
    assert e._retry_scope_ok("CATALOG", "c") is True       # ancestor
    assert e._retry_scope_ok("SCHEMA", "c.s") is True       # ancestor
    assert e._retry_scope_ok("SCHEMA", "c.other") is False  # not an ancestor of c.s.bad
    assert e._retry_scope_ok("FUNCTION", "c.s.f") is True   # functions always allowed


# --- state query: failed_object_names ---------------------------------------

class _Field:
    def __init__(self, name):
        self.name = name
        class _D:
            def simpleString(self_inner): return "string"
        self.dataType = _D()


class _Row(dict):
    def asDict(self): return dict(self)


class _FakeSpark:
    def __init__(self, rows):
        self._rows = rows
        self.sql_log = []

    def sql(self, sql):
        self.sql_log.append(sql)
        low = sql.lower()
        if low.startswith("select") and "where" in low:
            return _Result(self._rows)
        return _Result([])

    def table(self, name):
        class _S:
            fields = [_Field(c) for c in STATE_COLUMNS]
            def __iter__(self): return iter(self.fields)
        class _T:
            schema = _S()
        return _T()


class _Result:
    def __init__(self, rows): self._rows = rows
    def collect(self): return self._rows


def test_failed_object_names_from_facet_status():
    rows = [
        _Row(source_full_name="c.s.bad", target_full_name="c.s.bad",
             ddl_status=FACET_APPLIED, governance_status=FACET_FAILED,
             grants_status=FACET_APPLIED, last_action="failed"),
        _Row(source_full_name="c.s.grantfail", target_full_name="c.s.grantfail",
             ddl_status=FACET_APPLIED, governance_status=FACET_APPLIED,
             grants_status=FACET_FAILED, last_action="created"),
    ]
    spark = _FakeSpark(rows)
    names = SyncStateService(spark, "ops.ops.uc_sync_state").failed_object_names()
    assert names == {"c.s.bad", "c.s.grantfail"}
    # The WHERE clause covers all three facet statuses + the legacy last_action fallback.
    where = next(s for s in spark.sql_log
                 if s.strip().upper().startswith("SELECT") and "WHERE" in s)
    for facet in ("ddl_status = 'failed'", "governance_status = 'failed'",
                  "grants_status = 'failed'"):
        assert facet in where
