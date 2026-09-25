"""Within-level import parallelism (backlog item 3-import).

The core guarantee: `parallel_threads=4` produces IDENTICAL final state + results +
import_order as `parallel_threads=1` (the sequential default). Also: the _type_rank
barrier holds (functions before tables) even under concurrency, and fail-closed
governance stays correct when many tables are created in parallel.
"""

from __future__ import annotations

from pathlib import Path

from uc_sync.package_import import PackageImportEngine
from tests.test_failclosed_governance import GovSql, _write


def _big_bundle(root: Path, n_tables: int = 24) -> None:
    """A bundle with many same-rank objects across 2 schemas: functions (referenced by
    an inline mask), tables, and views — enough to exercise the level pool."""
    _write(root, "ddl/CATALOG_c.sql", "CREATE CATALOG `c`;\n")
    for sch in ("s1", "s2"):
        _write(root, f"ddl/SCHEMA_c__{sch}.sql", f"CREATE SCHEMA `c`.`{sch}`;\n")
        _write(root, f"ddl/FUNCTION_c__{sch}__mask.sql",
               f"CREATE FUNCTION `c`.`{sch}`.`mask`(v STRING) RETURNS STRING RETURN '***';\n")
    for i in range(n_tables):
        sch = "s1" if i % 2 == 0 else "s2"
        _write(root, f"ddl/TABLE_c__{sch}__t{i}.sql",
               f"CREATE TABLE `c`.`{sch}`.`t{i}` (id INT, ssn STRING MASK `c`.`{sch}`.`mask`);\n")
    # A couple of views (own rank, created after governance).
    for i in range(4):
        sch = "s1" if i % 2 == 0 else "s2"
        _write(root, f"ddl/VIEW_c__{sch}__v{i}.sql",
               f"CREATE VIEW `c`.`{sch}`.`v{i}` AS SELECT * FROM `c`.`{sch}`.`t{i}`;\n")
    _write(root, "inventory/objects.json", "[]")


def _run(root: Path, threads: int):
    sql = GovSql(allowed_tags={"cls"})
    results = PackageImportEngine(
        str(root), sql, dry_run=False, parallel_threads=threads,
    ).run()
    return sql, results


def _signature(results):
    # Order-independent content signature + the deterministic import_order mapping.
    content = sorted((r.object_type, r.target_full_name, r.status, r.action)
                     for r in results)
    order = {(r.object_type, r.target_full_name): r.import_order for r in results}
    return content, order


def test_parallel4_matches_sequential1(tmp_path):
    root1 = tmp_path / "b1"
    root4 = tmp_path / "b4"
    _big_bundle(root1)
    _big_bundle(root4)
    sql1, res1 = _run(root1, 1)
    sql4, res4 = _run(root4, 4)

    c1, o1 = _signature(res1)
    c4, o4 = _signature(res4)
    # Identical result content AND identical import_order assignment.
    assert c1 == c4
    assert o1 == o4
    # Identical final target state.
    assert sql1.tables == sql4.tables
    # Every table + view present, all SUCCESS.
    assert len([r for r in res4 if r.object_type == "TABLE"]) == 24
    assert all(r.status in ("SUCCESS",) for r in res4 if r.object_type == "TABLE")


def test_type_rank_barrier_functions_before_tables_under_threads(tmp_path):
    root = tmp_path / "b"
    _big_bundle(root)
    sql, _ = _run(root, 4)
    # The barrier: ALL functions complete before ANY table is created, even though the
    # table level runs in parallel (order within a level is arbitrary).
    fn_idx = [i for i, s in enumerate(sql.statements)
              if "CREATE" in s.upper() and "FUNCTION" in s.upper()]
    tbl_idx = [i for i, s in enumerate(sql.statements)
               if s.upper().lstrip().startswith("CREATE TABLE")]
    assert fn_idx and tbl_idx
    assert max(fn_idx) < min(tbl_idx)


def test_no_lost_or_duplicated_results_under_threads(tmp_path):
    root = tmp_path / "b"
    _big_bundle(root, n_tables=30)
    _, results = _run(root, 8)
    targets = [r.target_full_name for r in results if r.object_type == "TABLE"]
    assert len(targets) == 30
    assert len(set(targets)) == 30  # no duplicates
    # import_order values are unique and contiguous-ish (no collisions from the race).
    orders = [r.import_order for r in results]
    assert len(orders) == len(set(orders))


def test_failclosed_correct_under_parallel_creates(tmp_path):
    # Many tables created in parallel; one has a governed tag that fails → only that
    # fresh table is dropped fail-closed; the rest succeed. Identical at threads 1 & 4.
    def build(root):
        _write(root, "ddl/CATALOG_c.sql", "CREATE CATALOG `c`;\n")
        _write(root, "ddl/SCHEMA_c__s.sql", "CREATE SCHEMA `c`.`s`;\n")
        for i in range(10):
            _write(root, f"ddl/TABLE_c__s__t{i}.sql",
                   f"CREATE TABLE `c`.`s`.`t{i}` (id INT);\n")
        # t3's tag uses a disallowed key → governance fails → t3 dropped fail-closed.
        _write(root, "tags/TABLE_c__s__t3.sql",
               "ALTER TABLE `c`.`s`.`t3` SET TAGS ('missing' = 'x');\n")
        _write(root, "inventory/objects.json", "[]")

    def outcome(threads):
        root = tmp_path / f"b{threads}"
        build(root)
        sql = GovSql(allowed_tags=set())
        results = PackageImportEngine(str(root), sql, dry_run=False,
                                     parallel_threads=threads).run()
        t3 = next(r for r in results if r.object_type == "TABLE"
                  and r.target_full_name == "c.s.t3")
        others = [r for r in results if r.object_type == "TABLE"
                  and r.target_full_name != "c.s.t3"]
        return sql.tables, t3.status, {r.status for r in others}

    tables1, t3s1, others1 = outcome(1)
    tables4, t3s4, others4 = outcome(4)
    # t3 dropped fail-closed (not in final tables); its status FAILURE.
    assert "c.s.t3" not in tables1 and "c.s.t3" not in tables4
    assert t3s1 == "FAILURE" == t3s4
    # All other tables succeeded and survive — identical at both thread counts.
    assert others1 == {"SUCCESS"} == others4
    assert tables1 == tables4


def test_parallel_threads_default_is_one():
    # The engine default is sequential (the kill-switch).
    e = PackageImportEngine.__init__
    import inspect
    assert inspect.signature(e).parameters["parallel_threads"].default == 1
