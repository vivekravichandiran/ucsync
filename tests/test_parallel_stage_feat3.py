"""Unit tests for within-stage thread-pool parallelism (backlog item 3).

Covers Export (SHOW CREATE pre-capture) and Inventory (grant fan-out): parallel results
match sequential exactly (order + content), a slow object does not reorder results, and a
per-object failure still lands on the right object under concurrency.
"""

from __future__ import annotations

import time

from uc_sync.export import ExportService
from uc_sync.config import SyncConfig
from uc_sync.inventory import InventoryService
from uc_sync.models import ObjectType, UCObject


class FakeSql:
    """Minimal SHOW CREATE executor; optional per-object delay/failure."""

    def __init__(self, slow=None, fail=None):
        self.slow = slow or {}
        self.fail = set(fail or [])

    def show_create(self, object_type: str, full_name: str) -> str:
        if full_name in self.slow:
            time.sleep(self.slow[full_name])
        if full_name in self.fail:
            raise RuntimeError(f"boom for {full_name}")
        return f"CREATE TABLE {full_name} (id INT) USING DELTA"


def _tables(n):
    return [
        UCObject(ObjectType.TABLE, f"t{i}", f"c.s.t{i}",
                 definition={"columns": [{"name": "id", "type_text": "int"}]})
        for i in range(n)
    ]


def _run(tmp_path, objects, threads, sql=None):
    return ExportService(
        str(tmp_path / f"v{threads}"), "run1",
        sql_executor=sql or FakeSql(),
        workspace_root=str(tmp_path / f"w{threads}"),
        parallel_threads=threads,
    ).run(objects, dry_run=False)


def test_export_parallel_matches_sequential(tmp_path):
    objs = _tables(6)
    seq = _run(tmp_path, objs, 1)
    par = _run(tmp_path, objs, 4)
    seq_rows = [(r["full_name"], r["status"]) for r in seq["results"]]
    par_rows = [(r["full_name"], r["status"]) for r in par["results"]]
    assert seq_rows == par_rows
    assert seq["ddl_files"] == par["ddl_files"] == 6
    assert seq["exported"] == par["exported"]


def test_export_slow_object_does_not_reorder(tmp_path):
    objs = _tables(4)
    # Make the FIRST object the slowest; results must still be in inventory order.
    sql = FakeSql(slow={"c.s.t0": 0.15})
    par = _run(tmp_path, objs, 4, sql=sql)
    names = [r["full_name"] for r in par["results"]]
    assert names == ["c.s.t0", "c.s.t1", "c.s.t2", "c.s.t3"]


def test_export_parallel_failure_lands_on_right_object(tmp_path):
    objs = _tables(4)
    sql = FakeSql(fail={"c.s.t2"})
    par = _run(tmp_path, objs, 4, sql=sql)
    by_name = {r["full_name"]: r for r in par["results"]}
    assert by_name["c.s.t2"]["status"] == "ERROR"
    assert by_name["c.s.t2"]["error_code"] == "DDL_CAPTURE_FAILED"
    for n in ("c.s.t0", "c.s.t1", "c.s.t3"):
        assert by_name[n]["status"] == "SUCCESS"


def test_export_threads_one_is_sequential_default(tmp_path):
    # parallel_threads=1 (and the constructor default) run sequentially.
    svc = ExportService(str(tmp_path / "v"), "r", sql_executor=FakeSql(),
                        workspace_root=str(tmp_path / "w"))
    assert svc.parallel_threads == 1


# --- Inventory grant fan-out ------------------------------------------------

class FakeSource:
    """Returns a distinct grant per object full_name via the permissions endpoint."""

    def get(self, path):
        # path = /api/2.1/unity-catalog/permissions/<securable>/<full_name>
        full = path.rsplit("/", 1)[-1]
        return {"privilege_assignments": [
            {"principal": f"user_{full}", "privileges": ["SELECT"]}
        ]}


def _inv_service(threads):
    cfg = SyncConfig(parallel_threads=threads)
    return InventoryService(FakeSource(), cfg)


def test_inventory_grant_fanout_parallel_matches_sequential():
    objs_seq = _tables(6)
    objs_par = _tables(6)
    _inv_service(1)._attach_grants_all(objs_seq)
    _inv_service(4)._attach_grants_all(objs_par)
    for a, b in zip(objs_seq, objs_par):
        assert a.grants == b.grants
        # each object got ITS OWN grant (no cross-thread mixing)
        assert a.grants[0]["principal"] == f"user_{a.full_name}"


def test_inventory_grant_fanout_all_objects_covered():
    objs = _tables(10)
    _inv_service(4)._attach_grants_all(objs)
    assert all(o.grants and o.grants[0]["principal"] == f"user_{o.full_name}" for o in objs)


def test_config_parallel_threads_default_is_four():
    from uc_sync.config import from_sources
    assert from_sources({"catalogs": "c"}).parallel_threads == 4
    assert from_sources({"catalogs": "c", "parallel_threads": "8"}).parallel_threads == 8
