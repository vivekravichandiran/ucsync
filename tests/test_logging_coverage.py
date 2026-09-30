"""Per-object + per-phase logging coverage (backlog item 9, logging overhaul).

The goal: by reading the job-run cell output alone an operator can see every phase, every
object's outcome, and — at DEBUG — the exact SQL each step ran, so a failure is instantly
pinpointable. These tests capture the ``uc_sync`` logger and assert that coverage.
"""

from __future__ import annotations

import io
import logging
from contextlib import contextmanager
from pathlib import Path

from uc_sync.package_import import PackageImportEngine
from tests.test_failclosed_governance import GovSql, _write


@contextmanager
def _capture_logs(level=logging.DEBUG):
    """Attach a StringIO handler to the app logger and yield the buffer."""
    app = logging.getLogger("uc_sync")
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(level)
    prev_level = app.level
    app.setLevel(level)
    app.addHandler(handler)
    try:
        yield buf
    finally:
        app.removeHandler(handler)
        app.setLevel(prev_level)


def _full_bundle(root: Path) -> None:
    _write(root, "ddl/CATALOG_c.sql", "CREATE CATALOG `c`;\n")
    _write(root, "ddl/SCHEMA_c__s.sql", "CREATE SCHEMA `c`.`s`;\n")
    _write(root, "ddl/FUNCTION_c__s__mask_ssn.sql",
           "CREATE FUNCTION `c`.`s`.`mask_ssn`(v STRING) RETURNS STRING RETURN '***';\n")
    _write(root, "ddl/TABLE_c__s__t.sql",
           "CREATE TABLE `c`.`s`.`t` (ssn STRING MASK `c`.`s`.`mask_ssn`);\n")
    _write(root, "ddl/VIEW_c__s__v.sql",
           "CREATE VIEW `c`.`s`.`v` AS SELECT * FROM `c`.`s`.`t`;\n")
    _write(root, "tags/TABLE_c__s__t.sql",
           "ALTER TABLE `c`.`s`.`t` SET TAGS ('cls' = 'CONF');\n")
    _write(root, "inventory/objects.json", "[]")


def test_phase_headers_and_completion_lines(tmp_path):
    root = tmp_path / "migrated"
    _full_bundle(root)
    with _capture_logs() as buf:
        PackageImportEngine(str(root), GovSql(allowed_tags={"cls"}), dry_run=False).run()
    out = buf.getvalue()
    # Phase headers use plain language — no "Structure" jargon.
    assert "Structure" not in out
    assert "> Creating catalogs, schemas, volumes, functions & tables" in out
    assert "> Applying governed tags to tables & other objects" in out
    assert "> Creating views & materialized views" in out
    # Each phase closes with a tally line.
    assert "Object creation complete:" in out
    assert "View creation complete:" in out


def test_per_object_lines_show_each_object_and_outcome(tmp_path):
    root = tmp_path / "migrated"
    _full_bundle(root)
    with _capture_logs() as buf:
        PackageImportEngine(str(root), GovSql(allowed_tags={"cls"}), dry_run=False).run()
    out = buf.getvalue()
    # Every object type + name -> outcome, so the operator sees exactly what happened.
    assert "TABLE" in out and "c.s.t -> created" in out
    assert "FUNCTION" in out and "c.s.mask_ssn -> created" in out
    assert "VIEW" in out and "c.s.v -> created" in out


def test_sql_logged_at_debug_only(tmp_path):
    root = tmp_path / "migrated"
    _full_bundle(root)
    with _capture_logs(logging.DEBUG) as dbg:
        PackageImportEngine(str(root), GovSql(allowed_tags={"cls"}), dry_run=False).run()
    debug_out = dbg.getvalue()
    # At DEBUG the exact SQL is visible (prefixed + greppable).
    assert "sql[main]:" in debug_out
    assert "CREATE TABLE" in debug_out
    # At INFO the SQL is suppressed but the per-object summary still shows.
    with _capture_logs(logging.INFO) as info:
        PackageImportEngine(str(root), GovSql(allowed_tags={"cls"}), dry_run=False).run()
    info_out = info.getvalue()
    assert "sql[main]:" not in info_out
    assert "c.s.t -> created" in info_out


def test_failure_logged_at_error_with_object_name(tmp_path):
    root = tmp_path / "migrated"
    _write(root, "ddl/CATALOG_c.sql", "CREATE CATALOG `c`;\n")
    _write(root, "ddl/SCHEMA_c__s.sql", "CREATE SCHEMA `c`.`s`;\n")
    # Inline MASK references a function that is never created → CREATE TABLE fails.
    _write(root, "ddl/TABLE_c__s__bad.sql",
           "CREATE TABLE `c`.`s`.`bad` (ssn STRING MASK `c`.`s`.`missing`);\n")
    _write(root, "inventory/objects.json", "[]")
    app = logging.getLogger("uc_sync")
    records: list[logging.LogRecord] = []

    class _Rec(logging.Handler):
        def emit(self, record):
            records.append(record)

    h = _Rec()
    prev = app.level
    app.setLevel(logging.DEBUG)
    app.addHandler(h)
    try:
        PackageImportEngine(str(root), GovSql(), dry_run=False).run()
    finally:
        app.removeHandler(h)
        app.setLevel(prev)
    # The failing object is logged at ERROR and names the object + reason.
    errors = [r.getMessage() for r in records if r.levelno == logging.ERROR]
    assert any("c.s.bad -> ERROR" in m for m in errors), errors


# --- inventory (stage 01) + export (stage 02) get the same treatment ---------

def test_inventory_logs_phases_and_completion():
    from uc_sync.inventory import InventoryService
    from tests.test_inventory_tier_a import PathAwareSource, _cfg

    with _capture_logs() as buf:
        InventoryService(PathAwareSource(), _cfg()).run()
    out = buf.getvalue()
    assert "> Scanning source metastore" in out
    assert "scanning catalog c" in out
    assert "> Attaching grants (ACLs)" in out
    assert "Inventory complete:" in out


def test_export_logs_phases_and_completion(tmp_path):
    from uc_sync.export import ExportService
    from uc_sync.models import ObjectType, UCObject

    objs = [
        UCObject(object_type=ObjectType.LAKEBASE_TABLE, name="lb",
                 full_name="c.s.lb", definition={"in_scope_for_migration": False}),
    ]
    with _capture_logs() as buf:
        ExportService(f"{tmp_path}/v", "r1", workspace_root=f"{tmp_path}/w").run(
            objs, dry_run=False
        )
    out = buf.getvalue()
    assert "> Exporting 1 objects" in out
    assert "Export complete:" in out
