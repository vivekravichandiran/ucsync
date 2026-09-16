"""FEAT-4: copy volume files source→target via the Files API. Recursive copy, an
incremental control table (unchanged files skipped, modified re-copied), and a >5 GB
file reported (not silently dropped)."""

from __future__ import annotations

from uc_sync.volume_copy import (
    FILES_API_MAX_BYTES,
    InMemoryVolumeCopyControl,
    VolumeDataCopier,
    WarehouseVolumeCopyControl,
    copy_summary,
)


class _FakeSqlExecutor:
    """Records executed SQL; SELECT returns the current persisted rows (simulating a
    warehouse-backed Delta control table)."""

    def __init__(self):
        self.statements: list[str] = []
        self._rows: dict[tuple[str, str], tuple[int, str]] = {}

    def execute(self, sql):
        self.statements.append(sql)
        s = sql.strip().upper()
        if s.startswith("SELECT"):
            # (volume, path, source_mtime, status) — the control's load query shape.
            return [[v, p, m, st] for (v, p), (m, st) in self._rows.items()]
        if s.startswith("MERGE"):
            # Parse the VALUES tuples ('vol','path',CAST(n AS BIGINT),'status','msg').
            import re
            for vol, path, mtime, status in re.findall(
                r"\('([^']*)',\s*'([^']*)',\s*CAST\((\d+) AS BIGINT\),\s*'([^']*)'",
                sql,
            ):
                self._rows[(vol, path)] = (int(mtime), status)
        return []


def test_warehouse_control_persists_and_skips_across_instances():
    """FEAT-1/FEAT-4: the control table persists via the warehouse executor; a fresh
    control built on the same executor sees prior rows and skips unchanged files."""
    ex = _FakeSqlExecutor()
    c1 = WarehouseVolumeCopyControl(ex, "ops.ops.uc_sync_volume_files")
    assert c1.needs_copy("c.s.v", "/Volumes/c/s/v/a.txt", 100) is True
    c1.record("c.s.v", "/Volumes/c/s/v/a.txt", 100)
    c1.flush()
    assert any(s.strip().upper().startswith("CREATE TABLE") for s in ex.statements)
    assert any(s.strip().upper().startswith("MERGE") for s in ex.statements)
    # A new control instance (next run) loads the persisted row → skips unchanged.
    c2 = WarehouseVolumeCopyControl(ex, "ops.ops.uc_sync_volume_files")
    assert c2.needs_copy("c.s.v", "/Volumes/c/s/v/a.txt", 100) is False
    assert c2.needs_copy("c.s.v", "/Volumes/c/s/v/a.txt", 200) is True  # modified


class FakeSourceClient:
    """A fake Files API source: a directory tree + downloadable bytes."""

    def __init__(self, tree, contents, mtimes=None):
        # tree: {dir_path: [entry dict, ...]}; contents: {file_path: bytes}
        self._tree = tree
        self._contents = contents
        self._mtimes = mtimes or {}

    def list_directory(self, directory_path):
        return list(self._tree.get(directory_path, []))

    def download_file(self, file_path):
        return self._contents[file_path]


class FakeTargetClient:
    def __init__(self):
        self.uploaded: dict[str, bytes] = {}

    def upload_file(self, file_path, data, *, overwrite=True):
        self.uploaded[file_path] = data


def _entry(path, size, mtime, is_dir=False):
    return {"path": path, "file_size": size, "last_modified": mtime,
            "is_directory": is_dir}


def _copier():
    tree = {
        "/Volumes/c/s/v": [
            _entry("/Volumes/c/s/v/a.txt", 3, 100),
            _entry("/Volumes/c/s/v/sub", 0, 0, is_dir=True),
        ],
        "/Volumes/c/s/v/sub": [
            _entry("/Volumes/c/s/v/sub/b.txt", 5, 200),
        ],
    }
    contents = {
        "/Volumes/c/s/v/a.txt": b"aaa",
        "/Volumes/c/s/v/sub/b.txt": b"bbbbb",
    }
    return FakeSourceClient(tree, contents), FakeTargetClient()


def test_recursive_copy_preserves_tree():
    src, tgt = _copier()
    copier = VolumeDataCopier(src, tgt)
    results = copier.copy_volume("c.s.v", "/Volumes/c/s/v", "/Volumes/t/s/v")
    assert copy_summary(results) == {"COPIED": 2}
    # Directory tree recreated under the target root.
    assert tgt.uploaded["/Volumes/t/s/v/a.txt"] == b"aaa"
    assert tgt.uploaded["/Volumes/t/s/v/sub/b.txt"] == b"bbbbb"


def test_incremental_skips_unchanged_recopies_modified():
    src, tgt = _copier()
    control = InMemoryVolumeCopyControl()
    copier = VolumeDataCopier(src, tgt, control=control)

    first = copier.copy_volume("c.s.v", "/Volumes/c/s/v", "/Volumes/t/s/v")
    assert copy_summary(first) == {"COPIED": 2}

    # Second run, nothing changed → all skipped, zero uploads beyond the first.
    tgt.uploaded.clear()
    second = copier.copy_volume("c.s.v", "/Volumes/c/s/v", "/Volumes/t/s/v")
    assert copy_summary(second) == {"SKIPPED_UNCHANGED": 2}
    assert tgt.uploaded == {}

    # Modify a.txt (new mtime) → only it is re-copied.
    src._tree["/Volumes/c/s/v"][0]["last_modified"] = 999
    third = copier.copy_volume("c.s.v", "/Volumes/c/s/v", "/Volumes/t/s/v")
    assert copy_summary(third) == {"COPIED": 1, "SKIPPED_UNCHANGED": 1}
    assert set(tgt.uploaded) == {"/Volumes/t/s/v/a.txt"}


def test_oversize_file_reported_not_dropped():
    tree = {"/Volumes/c/s/v": [_entry("/Volumes/c/s/v/big.bin",
                                      FILES_API_MAX_BYTES + 1, 100)]}
    src = FakeSourceClient(tree, {})
    tgt = FakeTargetClient()
    results = VolumeDataCopier(src, tgt).copy_volume(
        "c.s.v", "/Volumes/c/s/v", "/Volumes/t/s/v")
    assert copy_summary(results) == {"SKIPPED_TOO_LARGE": 1}
    assert "5 GB" in results[0].message
    assert tgt.uploaded == {}  # never attempted


class _RecordingControl(InMemoryVolumeCopyControl):
    """Captures every record() call so we can assert status/message are persisted (C1)."""

    def __init__(self):
        super().__init__()
        self.records: list[tuple] = []

    def record(self, volume, path, source_mtime, status="COPIED", message=""):
        self.records.append((path, source_mtime, status, message))
        super().record(volume, path, source_mtime, status, message)


def test_control_records_status_for_every_outcome():
    """C1: the control store records COPIED, FAILED and SKIPPED_TOO_LARGE (so the
    uc_sync_volume_files table shows what happened), and a FAILED / too-large outcome
    does NOT advance the incremental watermark (so it is retried next run)."""
    tree = {"/Volumes/c/s/v": [
        _entry("/Volumes/c/s/v/ok.txt", 2, 100),
        _entry("/Volumes/c/s/v/bad.txt", 2, 200),
        _entry("/Volumes/c/s/v/big.bin", FILES_API_MAX_BYTES + 1, 300),
    ]}

    class FlakySource(FakeSourceClient):
        def download_file(self, file_path):
            if file_path.endswith("bad.txt"):
                raise RuntimeError("HTTP 500: transient")
            return b"ok"

    control = _RecordingControl()
    VolumeDataCopier(FlakySource(tree, {}), FakeTargetClient(),
                     control=control).copy_volume(
        "c.s.v", "/Volumes/c/s/v", "/Volumes/t/s/v")

    by_path = {p: (m, st) for (p, m, st, _msg) in control.records}
    assert by_path["/Volumes/c/s/v/ok.txt"] == (100, "COPIED")
    assert by_path["/Volumes/c/s/v/bad.txt"][1] == "FAILED"
    assert by_path["/Volumes/c/s/v/big.bin"][1] == "SKIPPED_TOO_LARGE"
    # Only the successful copy advanced the watermark → the two others are retried.
    assert control.needs_copy("c.s.v", "/Volumes/c/s/v/ok.txt", 100) is False
    assert control.needs_copy("c.s.v", "/Volumes/c/s/v/bad.txt", 200) is True
    assert control.needs_copy("c.s.v", "/Volumes/c/s/v/big.bin", 300) is True


def test_download_failure_recorded_run_continues():
    tree = {"/Volumes/c/s/v": [
        _entry("/Volumes/c/s/v/ok.txt", 2, 100),
        _entry("/Volumes/c/s/v/bad.txt", 2, 100),
    ]}

    class FlakySource(FakeSourceClient):
        def download_file(self, file_path):
            if file_path.endswith("bad.txt"):
                raise RuntimeError("HTTP 500: transient")
            return b"ok"

    src = FlakySource(tree, {})
    tgt = FakeTargetClient()
    results = VolumeDataCopier(src, tgt).copy_volume(
        "c.s.v", "/Volumes/c/s/v", "/Volumes/t/s/v")
    summary = copy_summary(results)
    assert summary == {"COPIED": 1, "FAILED": 1}
    # The good file still copied despite the other's failure.
    assert tgt.uploaded == {"/Volumes/t/s/v/ok.txt": b"ok"}
