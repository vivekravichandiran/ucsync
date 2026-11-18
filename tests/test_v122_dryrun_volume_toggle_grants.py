"""v1.2.2 field fixes.

1. A dry-run import never writes ``uc_sync_state`` (it used to seed a clean baseline,
   so the next LIVE run skipped every object as "Skipped (unchanged)").
2. Volume data copy never runs on a dry run (guarded in 03_Import; see the notebook
   source check below).
3. Volume copy URL-encodes directory paths (a space in a folder name aborted the
   whole copy) and isolates failures per directory / per volume.
4. An object a prior run recorded ``skipped_create_disabled`` is re-attempted once its
   create toggle is ON (it used to be skipped forever as unchanged).
5. The Grants sheet reports a per-grant outcome (APPLIED / NO-OP / SKIPPED / NOT
   APPLIED / FAILED) instead of a blanket APPLIED.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from uc_sync.delta import DeltaPlan
from uc_sync.fingerprints import (
    ddl_fingerprint,
    governance_fingerprint,
    grant_fingerprint_set,
)
from uc_sync.package_import import PackageImportEngine
from uc_sync.sync_state import state_rows_for_import
from uc_sync.volume_copy import (
    FAILED,
    COPIED,
    VolumeDataCopier,
    copy_all_volumes,
)

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
class RecordingSql:
    """Fake executor: tracks created catalogs/schemas/tables so DESCRIBE probes work;
    optionally fails ALTER … OWNER TO statements."""

    def __init__(self, existing: tuple[str, ...] = (), fail_owner: bool = False):
        self.statements: list[str] = []
        self.objects: set[str] = set(existing)
        self.fail_owner = fail_owner

    def execute(self, sql: str):
        self.statements.append(sql)
        u = sql.strip().upper()
        m = re.match(
            r"CREATE\s+(?:CATALOG|SCHEMA|TABLE)\s+(?:IF\s+NOT\s+EXISTS\s+)?([`\w.]+)",
            sql.strip(), re.I,
        )
        if m:
            self.objects.add(m.group(1).replace("`", ""))
        if u.startswith("DESCRIBE"):
            name = sql.split()[-1].replace("`", "").rstrip(";")
            if name not in self.objects:
                raise RuntimeError(f"NOT_FOUND {name}")
        if self.fail_owner and " OWNER TO " in u:
            raise RuntimeError("PRINCIPAL_DOES_NOT_EXIST: could not find principal")
        return None


def _write(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _inventory():
    return [
        {"object_type": "CATALOG", "full_name": "c", "target_full_name": "c",
         "tags": {}, "grants": [], "definition": {}},
        {"object_type": "SCHEMA", "full_name": "c.s", "target_full_name": "c.s",
         "tags": {}, "grants": [], "definition": {}},
        {"object_type": "TABLE", "full_name": "c.s.t", "target_full_name": "c.s.t",
         "tags": {},
         "grants": [{"principal": "u@x.com", "privileges": ["SELECT"]}],
         "definition": {"columns": [{"name": "id"}]}},
    ]


def _bundle(tmp_path: Path, inventory: list[dict], *, owner: bool = False) -> Path:
    root = tmp_path / "migrated"
    _write(root, "ddl/CATALOG_c.sql", "CREATE CATALOG `c`;\n")
    _write(root, "ddl/SCHEMA_c__s.sql", "CREATE SCHEMA `c`.`s`;\n")
    _write(root, "ddl/TABLE_c__s__t.sql", "CREATE TABLE `c`.`s`.`t` (id INT);\n")
    grants = "GRANT SELECT ON TABLE `c`.`s`.`t` TO `u@x.com`;\n"
    if owner:
        grants += "ALTER TABLE `c`.`s`.`t` OWNER TO `gone@x.com`;\n"
    _write(root, "grants/TABLE_c__s__t.sql", grants)
    _write(root, "inventory/objects.json", json.dumps(inventory))
    return root


def _baseline(inv: list[dict], last_action: dict[str, str]) -> dict:
    return {
        row["full_name"]: {
            "object_type": row["object_type"],
            "ddl_hash": ddl_fingerprint(row),
            "governance_hash": governance_fingerprint(row),
            "grants": grant_fingerprint_set(row),
            "last_action": last_action.get(row["object_type"], "created"),
        }
        for row in inv
    }


# --------------------------------------------------------------------------- #
# 1. Dry run never writes uc_sync_state
# --------------------------------------------------------------------------- #
def _result(object_type, name, **kw):
    base = {"object_type": object_type, "source_full_name": name,
            "target_full_name": name, "full_name": name, "status": "SUCCESS",
            "action": "CREATE_OR_SKIP"}
    base.update(kw)
    return base


def test_dry_run_writes_no_state_rows():
    results = [_result("TABLE", "c.s.t", status="PENDING", action="DRY_RUN",
                       ddl_hash="h1")]
    rows = state_rows_for_import(
        results, dry_run=True, batch_id="b", run_id="r", ran_by="me",
        utility_version="1.2.2",
    )
    assert rows == []


def test_live_run_state_rows_exclude_tag_ops_and_columns_keep_abac():
    results = [
        _result("TABLE", "c.s.t", ddl_hash="h1"),
        _result("TABLE", "c.s.t", action="APPLY_TAGS", policies_path="/tags/x.sql"),
        _result("COLUMN", "c.s.t.newcol", action="COLUMN_ADDED"),
        _result("ABAC_POLICY", "c.pol", policies_path="/abac/p.sql"),
    ]
    rows = state_rows_for_import(
        results, dry_run=False, batch_id="b", run_id="r", ran_by="me",
        utility_version="1.2.2",
    )
    assert [(r["object_type"], r["source_full_name"]) for r in rows] == [
        ("TABLE", "c.s.t"), ("ABAC_POLICY", "c.pol"),
    ]


def test_dry_run_then_live_run_creates_everything(tmp_path: Path):
    """The field sequence: dry run, then live. With the dry run no longer seeding
    state, the live run has no baseline → full run → the table is CREATED."""
    inv = _inventory()
    root = _bundle(tmp_path, inv)
    dry = PackageImportEngine(str(root), RecordingSql(), dry_run=True).run()
    baseline_rows = state_rows_for_import(
        [r.to_dict() for r in dry], dry_run=True, batch_id="b", run_id="r1",
        ran_by="me", utility_version="1.2.2",
    )
    assert baseline_rows == []
    sql = RecordingSql()
    live = PackageImportEngine(
        str(root), sql, dry_run=False, prior_state={}  # nothing persisted
    ).run()
    tbl = next(r for r in live if r.object_type == "TABLE")
    assert tbl.status == "SUCCESS" and tbl.action == "CREATE_OR_SKIP"
    assert any(s.upper().startswith("CREATE TABLE") for s in sql.statements)


def test_notebook_gates_state_upsert_and_volume_copy_on_dry_run():
    """03_Import is a Databricks notebook (not importable), so pin the two dry-run
    guards at source level: the state rows go through state_rows_for_import with
    dry_run, the upsert sits in the non-dry-run branch, and the volume copy has a
    dry-run branch BEFORE the copy branch."""
    src = (REPO / "notebooks" / "03_Import.py").read_text(encoding="utf-8")
    assert "state_rows_for_import(" in src and "dry_run=cfg.dry_run" in src
    gate = src.index("if cfg.dry_run:\n                state_write_status")
    upsert = src.index("_state_svc.upsert(_state_rows)")
    assert gate < upsert
    dry_copy = src.index("if cfg.copy_volume_data and cfg.dry_run:")
    live_copy = src.index("elif cfg.copy_volume_data:")
    assert dry_copy < live_copy
    assert '"state": state_write_status' in src


# --------------------------------------------------------------------------- #
# 3. Volume copy: URL encoding + failure isolation + catalog mapping
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_GET(self):  # noqa: N802
        _Handler.seen.append(self.path)
        body = json.dumps({"contents": [
            {"path": "/Volumes/c/s/v/Tax advises folder/a b.txt",
             "is_directory": False, "file_size": 1, "last_modified": 1},
        ]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence test output
        pass


def test_list_directory_encodes_spaces_and_special_chars_over_real_http():
    """Real urllib round-trip (the pre-fix code raised "URL can't contain control
    characters" before any request was sent)."""
    from uc_sync.auth import WorkspaceAuth
    from uc_sync.workspace_client import WorkspaceClient

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        _Handler.seen.clear()
        host = f"http://127.0.0.1:{server.server_address[1]}"
        client = WorkspaceClient(WorkspaceAuth(host=host, token="t"), max_retries=1)
        directory = "/Volumes/c/s/v/Tax advises folder #1 ?x 100% ünï"
        items = list(client.list_directory(directory))
    finally:
        server.shutdown()
    assert items and items[0]["path"].endswith("a b.txt")
    path = _Handler.seen[0]
    assert path.startswith(
        "/api/2.0/fs/directories/Volumes/c/s/v/Tax%20advises%20folder%20%231%20%3Fx"
        "%20100%25%20%C3%BCn%C3%AF"
    )
    assert " " not in path


class _FakeSource:
    def __init__(self, tree, contents, broken=()):
        self.tree, self.contents, self.broken = tree, contents, set(broken)

    def list_directory(self, directory):
        if directory in self.broken:
            raise RuntimeError("URL can't contain control characters")
        return list(self.tree.get(directory, []))

    def download_file(self, path):
        return self.contents[path]


class _FakeTarget:
    def __init__(self):
        self.uploaded: dict[str, bytes] = {}

    def upload_file(self, path, data, *, overwrite=True):
        self.uploaded[path] = data


def _e(path, is_dir=False, size=1, mtime=1):
    return {"path": path, "is_directory": is_dir, "file_size": size,
            "last_modified": mtime}


def test_unlistable_directory_is_one_failed_row_siblings_still_copy():
    tree = {
        "/Volumes/c/s/v": [_e("/Volumes/c/s/v/ok.txt"),
                           _e("/Volumes/c/s/v/bad dir", is_dir=True),
                           _e("/Volumes/c/s/v/good", is_dir=True)],
        "/Volumes/c/s/v/good": [_e("/Volumes/c/s/v/good/g.txt")],
    }
    contents = {"/Volumes/c/s/v/ok.txt": b"o", "/Volumes/c/s/v/good/g.txt": b"g"}
    tgt = _FakeTarget()
    copier = VolumeDataCopier(_FakeSource(tree, contents, broken={"/Volumes/c/s/v/bad dir"}), tgt)
    results = copier.copy_volume("c.s.v", "/Volumes/c/s/v", "/Volumes/t/s/v")
    by_status = {r.source_path: r.status for r in results}
    assert by_status["/Volumes/c/s/v/bad dir"] == FAILED
    assert by_status["/Volumes/c/s/v/ok.txt"] == COPIED
    assert by_status["/Volumes/c/s/v/good/g.txt"] == COPIED
    assert set(tgt.uploaded) == {"/Volumes/t/s/v/ok.txt", "/Volumes/t/s/v/good/g.txt"}
    failed = next(r for r in results if r.status == FAILED)
    assert failed.target_path == "/Volumes/t/s/v/bad dir"
    assert "could not list directory" in failed.message


def test_copy_all_volumes_maps_catalog_and_isolates_a_failing_volume():
    inv = [
        {"object_type": "VOLUME", "full_name": "src.s.v1"},
        {"object_type": "EXTERNAL_VOLUME", "full_name": "src.s.v2"},
        {"object_type": "TABLE", "full_name": "src.s.t"},  # ignored
        {"object_type": "VOLUME", "full_name": "other.s.v3"},  # unmapped catalog
    ]
    tree = {
        "/Volumes/src/s/v1": [_e("/Volumes/src/s/v1/a.txt")],
        "/Volumes/other/s/v3": [_e("/Volumes/other/s/v3/c.txt")],
    }
    contents = {"/Volumes/src/s/v1/a.txt": b"a", "/Volumes/other/s/v3/c.txt": b"c"}
    tgt = _FakeTarget()

    class _Copier(VolumeDataCopier):
        def copy_volume(self, volume, source_root, target_root):
            if volume == "src.s.v2":
                raise RuntimeError("boom")
            return super().copy_volume(volume, source_root, target_root)

    results = copy_all_volumes(
        _Copier(_FakeSource(tree, contents), tgt), inv, {"src": "dst"}
    )
    assert set(tgt.uploaded) == {"/Volumes/dst/s/v1/a.txt", "/Volumes/other/s/v3/c.txt"}
    v2 = next(r for r in results if r.volume == "src.s.v2")
    assert v2.status == FAILED and "boom" in v2.message
    assert v2.source_path == "/Volumes/src/s/v2"
    assert v2.target_path == "/Volumes/dst/s/v2"


# --------------------------------------------------------------------------- #
# 4. create_* toggle flipped ON re-attempts a prior skipped_create_disabled object
# --------------------------------------------------------------------------- #
def test_delta_reattempts_create_disabled_when_toggle_now_on():
    rows = [{"object_type": "SCHEMA", "full_name": "c.s", "tags": {}, "grants": [],
             "definition": {}}]
    baseline = _baseline(rows, {"SCHEMA": "skipped_create_disabled"})
    on = DeltaPlan(rows, baseline, create_enabled=lambda ot: True)
    assert on.action("c.s") == "CREATED_NEW"
    assert on.should_skip_object("c.s") is False
    off = DeltaPlan(rows, baseline, create_enabled=lambda ot: False)
    assert off.action("c.s") == "UNCHANGED" and off.should_skip_object("c.s")
    legacy = DeltaPlan(rows, baseline)  # no callable → previous behaviour
    assert legacy.should_skip_object("c.s")


def test_engine_creates_schema_after_create_schemas_flipped_on(tmp_path: Path):
    """Field sequence: run 1 create_schemas=false (schema recorded
    skipped_create_disabled, its tables failed); run 2 create_schemas=true must
    actually issue CREATE SCHEMA, not skip it as unchanged."""
    inv = _inventory()
    root = _bundle(tmp_path, inv)
    baseline = _baseline(inv, {"SCHEMA": "skipped_create_disabled",
                               "TABLE": "failed"})
    sql = RecordingSql(existing=("c",))
    results = PackageImportEngine(
        str(root), sql, dry_run=False, prior_state=baseline,
        toggles={"create_schemas": True},
    ).run()
    schema = next(r for r in results if r.object_type == "SCHEMA")
    assert schema.delta_action == "CREATED_NEW" and schema.status == "SUCCESS"
    assert any(s.upper().startswith("CREATE SCHEMA") for s in sql.statements)

    # Toggle still OFF → still skipped, zero create.
    sql_off = RecordingSql(existing=("c", "c.s"))
    results_off = PackageImportEngine(
        str(root), sql_off, dry_run=False, prior_state=baseline,
        toggles={"create_schemas": False},
    ).run()
    schema_off = next(r for r in results_off if r.object_type == "SCHEMA")
    assert schema_off.action == "SKIP_CREATE_DISABLED"
    assert not any(s.upper().startswith("CREATE SCHEMA") for s in sql_off.statements)


def test_existing_catalog_mode_forces_toggle_off_before_delta_plan(tmp_path: Path):
    """Existing-catalog auto-detection forces create_catalogs OFF. It must run BEFORE
    the delta plan, so a BYO catalog recorded skipped_create_disabled is NOT
    re-attempted every run just because the create_catalogs widget defaults ON."""
    inv = _inventory()
    root = _bundle(tmp_path, inv)
    baseline = _baseline(inv, {"CATALOG": "skipped_create_disabled"})
    sql = RecordingSql(existing=("c", "c.s", "c.s.t"))
    results = PackageImportEngine(
        str(root), sql, dry_run=False, prior_state=baseline,
        catalog_mapping={"c": "c"}, workspace_client=object(),
    ).run()
    cat = next(r for r in results if r.object_type == "CATALOG")
    assert cat.delta_action == "UNCHANGED"
    assert cat.action == "SKIP_CREATE_DISABLED"
    assert not any(s.upper().startswith("CREATE CATALOG") for s in sql.statements)


# --------------------------------------------------------------------------- #
# 5. Grants sheet: per-grant outcome
# --------------------------------------------------------------------------- #
def test_engine_records_apply_grants_off_and_owner_kept(tmp_path: Path):
    inv = _inventory()
    root = _bundle(tmp_path, inv, owner=True)
    off = PackageImportEngine(
        str(root), RecordingSql(), dry_run=False, toggles={"apply_grants": False},
    ).run()
    tbl = next(r for r in off if r.object_type == "TABLE")
    assert tbl.grants_status == "skipped" and tbl.grants_note == "apply_grants off"

    on = PackageImportEngine(
        str(root), RecordingSql(fail_owner=True), dry_run=False,
    ).run()
    tbl = next(r for r in on if r.object_type == "TABLE")
    assert tbl.grants_status == "applied"
    assert tbl.owner_status == "kept" and tbl.owner_note


def _grant(principal, privs, ptype="USER"):
    return {"principal": principal, "principal_type": ptype, "privileges": privs}


def _obj(name, grants, otype="TABLE"):
    return {"object_type": otype, "full_name": name, "target_full_name": name,
            "owner": "me", "tags": {}, "grants": grants}


def test_grants_sheet_per_grant_statuses(tmp_path: Path):
    from openpyxl import load_workbook

    from uc_sync.report import build_report

    objects = [
        _obj("c.s.unchanged", [_grant("a@x.com", ["SELECT"])]),
        _obj("c.s.regrant", [_grant("new@x.com", ["SELECT"]),
                             _grant("old@x.com", ["SELECT"])]),
        _obj("c.s.off", [_grant("a@x.com", ["SELECT"])]),
        _obj("c.s.owned", [_grant("gone@x.com", ["OWNER"]),
                           _grant("spn-app-id", ["ALL_PRIVILEGES"], "SERVICE_PRINCIPAL")]),
        _obj("c.s.moved", [_grant("ok@x.com", ["OWNER"])]),
        _obj("c.s.manual", [_grant("a@x.com", ["SELECT"])], "EXTERNAL_TABLE"),
        _obj("c.s.grantfail", [_grant("ghost@x.com", ["SELECT"])]),
        _obj("c.s.fresh", [_grant("a@x.com", ["SELECT"])]),
    ]
    res = [
        _result("TABLE", "c.s.unchanged", status="UNCHANGED", action="UNCHANGED",
                delta_action="UNCHANGED"),
        _result("TABLE", "c.s.regrant", action="SKIP_EXISTING",
                delta_action="GRANTS_UPDATED", grants_status="applied"),
        _result("TABLE", "c.s.off", grants_status="skipped",
                grants_note="apply_grants off", delta_action="CREATED_NEW"),
        _result("TABLE", "c.s.owned", grants_status="applied", owner_status="kept",
                owner_note="PRINCIPAL_DOES_NOT_EXIST", delta_action="CREATED_NEW"),
        _result("TABLE", "c.s.moved", grants_status="applied",
                owner_status="transferred", delta_action="CREATED_NEW"),
        _result("EXTERNAL_TABLE", "c.s.manual", status="MANUAL_ACTION_REQUIRED",
                action="MANUAL"),
        _result("TABLE", "c.s.grantfail", grants_status="failed",
                message="created; grant warning: PRINCIPAL_DOES_NOT_EXIST ghost",
                delta_action="CREATED_NEW"),
        _result("TABLE", "c.s.fresh", grants_status="applied",
                delta_action="CREATED_NEW"),
    ]
    delta_rows = [{"action": "GRANT_ADDED", "object_type": "TABLE",
                   "object": "c.s.regrant", "principal": "new@x.com",
                   "detail": "new@x.com: SELECT"}]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), stage="IMPORT", import_results=res,
                 delta_rows=delta_rows, run_id="r", run_as_spn="SPN-APP-ID")
    rows = list(load_workbook(out)["Grants"].iter_rows(values_only=True))[1:]
    st = {(r[0], r[2]): r[-1] for r in rows}

    assert st[("c.s.unchanged", "a@x.com")] == "SKIPPED (unchanged)"
    assert st[("c.s.regrant", "new@x.com")] == "APPLIED"
    assert st[("c.s.regrant", "old@x.com")] == "NO-OP (unchanged since last run)"
    assert st[("c.s.off", "a@x.com")] == "SKIPPED (apply_grants off)"
    assert st[("c.s.owned", "gone@x.com")].startswith(
        "NOT APPLIED (owner kept as run principal: PRINCIPAL_DOES_NOT_EXIST")
    assert st[("c.s.owned", "spn-app-id")] == "SKIPPED (run-as principal — never re-granted)"
    assert st[("c.s.moved", "ok@x.com")] == "APPLIED (ownership transferred)"
    assert st[("c.s.manual", "a@x.com")] == "SKIPPED (object not created — manual step)"
    assert st[("c.s.grantfail", "ghost@x.com")].startswith(
        "FAILED (a GRANT on this object raised: PRINCIPAL_DOES_NOT_EXIST")
    assert st[("c.s.fresh", "a@x.com")] == "APPLIED"
