"""FEAT-2: governed tags are created (with their allowed values) before they are
applied. Inventory captures the governed tags in use + their allowed values from the
tag-policies API; export writes CREATE GOVERNED TAG DDL; import Phase 0 creates them
idempotently (SKIP_EXISTING when the same-account target already has the tag)."""

from __future__ import annotations

import json
from pathlib import Path

from uc_sync.config import from_sources
from uc_sync.governance import (
    governed_tag_create_statement,
    read_governed_tag_policies,
)
from uc_sync.inventory import InventoryService
from uc_sync.models import ObjectType, UCObject
from uc_sync.package_import import PackageImportEngine


def test_governed_tag_create_statement():
    assert governed_tag_create_statement("pii", ["EMAIL", "SSN"]) == (
        "CREATE GOVERNED TAG `pii` VALUES ('EMAIL', 'SSN');"
    )
    assert governed_tag_create_statement("freeform", []) == (
        "CREATE GOVERNED TAG `freeform`;"
    )


class _PoliciesClient:
    def paginate(self, path, items_key=None, **q):
        assert path == "/api/2.1/tag-policies"
        return iter([
            {"tag_key": "ai27_uc_pii", "values": [{"name": "EMAIL"}, {"name": "SSN"}]},
            {"tag_key": "ai27_uc_classification", "values": [{"name": "PUBLIC"}]},
        ])


def test_read_governed_tag_policies():
    policies = read_governed_tag_policies(_PoliciesClient())
    assert policies["ai27_uc_pii"] == ["EMAIL", "SSN"]
    assert policies["ai27_uc_classification"] == ["PUBLIC"]


class _TaggedSource:
    """A source with one column-tagged table (governed key ai27_uc_pii) plus a
    tag-policies API."""

    def paginate(self, path, items_key=None, **q):
        if path.endswith("/catalogs"):
            return iter([{"name": "c", "catalog_type": "MANAGED_CATALOG"}])
        if path.endswith("/schemas"):
            return iter([{"name": "s", "full_name": "c.s"}])
        if path.endswith("/tables"):
            return iter([{"name": "t", "full_name": "c.s.t", "table_type": "MANAGED"}])
        if path == "/api/2.1/tag-policies":
            return iter([
                {"tag_key": "ai27_uc_pii", "values": [{"name": "EMAIL"}]},
                {"tag_key": "unused_tag", "values": [{"name": "X"}]},
            ])
        return iter([])

    def get(self, path, **q):
        return {}


class _TaggingSql:
    """A SQL executor that reports a column tag on c.s.t for the governed key."""

    def execute(self, sql):
        s = sql.lower()
        if "column_tags" in s:
            return [["c", "s", "t", "email", "ai27_uc_pii", "EMAIL"]]
        return []


def test_inventory_emits_governed_tag_only_for_used_keys():
    cfg = from_sources({"execution_mode": "LOCAL", "catalogs": "c"})
    objects = InventoryService(_TaggedSource(), cfg, sql_executor=_TaggingSql()).run()
    governed = [o for o in objects if o.object_type == ObjectType.GOVERNED_TAG]
    keys = {g.full_name for g in governed}
    assert keys == {"ai27_uc_pii"}  # unused_tag is a policy but not assigned → skipped
    assert governed[0].definition["allowed_values"] == ["EMAIL"]


def _write(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


class _RecordingSql:
    def __init__(self, fail_exists=()):
        self.statements: list[str] = []
        self._fail_exists = set(fail_exists)

    def execute(self, sql):
        self.statements.append(sql)
        for key in self._fail_exists:
            if key in sql:
                raise RuntimeError("TAG_POLICY_ALREADY_EXISTS: already exists")
        return None


def _governed_tag_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "migrated"
    from uc_sync.export import _safe_filename
    stem = _safe_filename("GOVERNED_TAG", "ai27_uc_pii")
    _write(root, f"governed_tags/{stem}.sql",
           "CREATE GOVERNED TAG `ai27_uc_pii` VALUES ('EMAIL', 'SSN');\n")
    _write(root, "ddl/CATALOG_c.sql", "CREATE CATALOG `c`;\n")
    _write(root, "inventory/objects.json", "[]")
    return root


def test_import_phase0_creates_governed_tag():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        root = _governed_tag_bundle(Path(d))
        sql = _RecordingSql()
        results = PackageImportEngine(str(root), sql, dry_run=False).run()
    gt = next(r for r in results if r.object_type == "GOVERNED_TAG")
    assert gt.status == "SUCCESS"
    assert gt.target_full_name == "ai27_uc_pii"
    assert any("CREATE GOVERNED TAG `ai27_uc_pii`" in s for s in sql.statements)


def test_import_phase0_skips_existing_governed_tag():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        root = _governed_tag_bundle(Path(d))
        sql = _RecordingSql(fail_exists=("ai27_uc_pii",))
        results = PackageImportEngine(str(root), sql, dry_run=False).run()
    gt = next(r for r in results if r.object_type == "GOVERNED_TAG")
    # An already-existing governed tag on a same-account target is a skip, not a fail.
    assert gt.status == "SUCCESS"
    assert gt.action == "SKIP_EXISTING"
