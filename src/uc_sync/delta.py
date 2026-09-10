"""Incremental (delta) planning for the import stage (task 1).

Given the objects in the current migrated bundle and the baseline recorded in
``uc_sync_state`` from the last successful run, decide **per object** what changed
and therefore what the import must do. The plan is a set of labels + gating
booleans the import engine consults; unchanged objects are skipped entirely (zero
writes), and only changed governance / grants are touched.

Run mode is auto-detected, never a widget: a baseline present for the scope → run
**incremental**; none → run **full** and seed the baseline. ``force_full`` forces a
full re-seed on demand.

Match key = **``full_name``** (per the resolved decision): a UC object id is not
stable across a source rebuild, so the durable baseline key is the name. A rename
therefore reads as drop + add — the new name is ``CREATED_NEW`` and the old is
``SOURCE_ABSENT`` (reported, never dropped).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from uc_sync.fingerprints import (
    ddl_fingerprint,
    governance_fingerprint,
    grant_fingerprint_set,
)

# Delta actions surfaced on the report's Delta sheet + uc_sync_audit.
CREATED_NEW = "CREATED_NEW"
REPLACED = "REPLACED"                 # view / function DDL changed → CREATE OR REPLACE
CHANGED = "CHANGED"                   # table DDL changed → reported, not auto-altered
UNCHANGED = "UNCHANGED"
GOVERNANCE_UPDATED = "GOVERNANCE_UPDATED"
SOURCE_ABSENT = "SOURCE_ABSENT"       # in baseline, gone from source → reported, no drop
GRANT_ADDED = "GRANT_ADDED"
GRANT_REMOVED = "GRANT_REMOVED"       # gone from source → reported, never revoked
REPORT_ONLY = "REPORT_ONLY"           # pipeline-managed / Tier-A asset → reported, never migrated

# Object types whose DDL can be safely re-applied with CREATE OR REPLACE on a change.
_REPLACEABLE_TYPES = {
    "VIEW", "DYNAMIC_VIEW", "METRIC_VIEW", "MATERIALIZED_VIEW", "FUNCTION",
}
# A volume has no data DDL and no CREATE OR REPLACE — once created its location is
# fixed, so it is only ever CREATED_NEW or UNCHANGED.
_VOLUME_TYPES = {"VOLUME", "EXTERNAL_VOLUME"}
_ABAC_TYPE = "ABAC_POLICY"
# Types the engine never creates (see package_import): streaming tables are always
# pipeline-managed; Tier-A AI assets (models, online tables, vector indexes, monitors,
# UC secrets) are inventory-only. Materialized views are report-only UNLESS
# migrate_materialized_views is set (handled with the toggle in _decide). These must
# surface on the Delta sheet as REPORT_ONLY, not CREATED_NEW/CHANGED — the plan action
# alone would mislead (the object is reported, not migrated).
_ALWAYS_REPORT_ONLY_TYPES = {
    "STREAMING_TABLE", "MODEL", "ONLINE_TABLE", "VECTOR_INDEX", "MONITOR", "UC_SECRET",
}


@dataclass
class ObjectDelta:
    full_name: str
    object_type: str
    action: str
    governance_changed: bool = False
    grants_added: dict[str, list[str]] = field(default_factory=dict)
    grants_removed: dict[str, list[str]] = field(default_factory=dict)
    reason: str = ""

    @property
    def fully_unchanged(self) -> bool:
        return (
            self.action == UNCHANGED
            and not self.governance_changed
            and not self.grants_added
            and not self.grants_removed
        )


class DeltaPlan:
    """Per-object delta decisions computed from the current bundle vs. the baseline.

    ``baseline`` is ``{full_name: {"ddl_hash", "governance_hash", "grants"}}`` read
    from ``uc_sync_state`` (``grants`` is the normalised explicit-grant set dict). An
    empty / absent baseline means a **full** run: every object is ``CREATED_NEW`` and
    nothing is gated. ``force_full`` also yields a full run.
    """

    def __init__(
        self,
        current_rows: list[Mapping[str, Any]],
        baseline: Optional[Mapping[str, Mapping[str, Any]]] = None,
        *,
        force_full: bool = False,
        migrate_materialized_views: bool = False,
    ):
        self.baseline = dict(baseline or {})
        self.incremental = bool(self.baseline) and not force_full
        self.migrate_materialized_views = bool(migrate_materialized_views)
        self._by_name: dict[str, ObjectDelta] = {}
        self._current_names: set[str] = set()
        for row in current_rows:
            full_name = str(row.get("full_name") or row.get("source_full_name") or "")
            if not full_name:
                continue
            self._current_names.add(full_name)
            self._by_name[full_name] = self._decide(row, full_name)
        # Objects present in the baseline but gone from the current source.
        self.source_absent: list[ObjectDelta] = []
        if self.incremental:
            for name, prior in self.baseline.items():
                if name in self._current_names:
                    continue
                self.source_absent.append(
                    ObjectDelta(
                        full_name=name,
                        object_type=str(prior.get("object_type") or ""),
                        action=SOURCE_ABSENT,
                        reason="present in baseline, absent from source (not dropped)",
                    )
                )

    def _is_report_only(self, object_type: str) -> bool:
        """True for types the engine never migrates (see package_import): streaming
        tables + Tier-A AI assets always, materialized views unless the toggle is set.
        Mirrors the engine so the Delta sheet's action matches what actually happens."""
        if object_type in _ALWAYS_REPORT_ONLY_TYPES:
            return True
        return (
            object_type == "MATERIALIZED_VIEW" and not self.migrate_materialized_views
        )

    def _decide(self, row: Mapping[str, Any], full_name: str) -> ObjectDelta:
        object_type = str(row.get("object_type") or "")
        if not self.incremental:
            # Full run: seed everything, gate nothing.
            return ObjectDelta(full_name, object_type, CREATED_NEW)
        prior = self.baseline.get(full_name)
        cur_ddl = ddl_fingerprint(row)
        cur_gov = governance_fingerprint(row)
        cur_grants = grant_fingerprint_set(row)
        if prior is None:
            return ObjectDelta(
                full_name, object_type, CREATED_NEW,
                governance_changed=bool(cur_gov and cur_gov != governance_fingerprint({})),
                grants_added=cur_grants,
            )
        prior_grants = dict(prior.get("grants") or {})
        grants_added = {
            p: v for p, v in cur_grants.items() if prior_grants.get(p) != v
        }
        grants_removed = {
            p: v for p, v in prior_grants.items() if p not in cur_grants
        }
        governance_changed = str(prior.get("governance_hash") or "") != cur_gov
        ddl_changed = str(prior.get("ddl_hash") or "") != cur_ddl

        if ddl_changed:
            if object_type in _VOLUME_TYPES:
                # A volume's location is fixed once created; a DDL diff cannot be
                # re-applied by recreate, so treat it as UNCHANGED structurally
                # (comment/owner drift is out of the DDL fingerprint's scope here).
                action = UNCHANGED
            elif object_type in _REPLACEABLE_TYPES:
                action = REPLACED
            else:  # table family → report, never auto-ALTER (schema evolution).
                action = CHANGED
        elif governance_changed:
            action = GOVERNANCE_UPDATED
        elif grants_added or grants_removed:
            # Only grants changed — no DDL/governance diff. Surface via grant rows;
            # the object itself is otherwise unchanged.
            action = UNCHANGED
        else:
            action = UNCHANGED
        return ObjectDelta(
            full_name, object_type, action,
            governance_changed=governance_changed,
            grants_added=grants_added,
            grants_removed=grants_removed,
        )

    # --- queries the import engine uses -------------------------------------

    def get(self, full_name: str) -> Optional[ObjectDelta]:
        return self._by_name.get(full_name)

    def should_skip_object(self, full_name: str) -> bool:
        """True when the object is fully unchanged (skip create + governance + grants
        → zero writes). Only ever True on an incremental run."""
        if not self.incremental:
            return False
        delta = self._by_name.get(full_name)
        return bool(delta and delta.fully_unchanged)

    def governance_changed(self, full_name: str) -> bool:
        """True when the object's governance must be (re)applied (new object, or its
        governance fingerprint changed). On a full run everything is applied."""
        if not self.incremental:
            return True
        delta = self._by_name.get(full_name)
        if delta is None:
            return True
        return delta.action == CREATED_NEW or delta.governance_changed

    def action(self, full_name: str) -> str:
        delta = self._by_name.get(full_name)
        return delta.action if delta else CREATED_NEW

    def delta_rows(self) -> list[dict[str, Any]]:
        """Row-per-change for the Delta sheet + audit — only objects/grants that
        changed (UNCHANGED objects are summarised as a count, not listed)."""
        rows: list[dict[str, Any]] = []
        for delta in self._by_name.values():
            if delta.action != UNCHANGED:
                # Report-only types (streaming tables, Tier-A AI assets, and MVs unless
                # migrate_materialized_views) are never migrated — the raw plan action
                # (CREATED_NEW/CHANGED/REPLACED) would falsely imply they were created.
                # Surface REPORT_ONLY instead so the Delta sheet matches reality. (This
                # is label-only: skip/governance gating is unchanged, so an UNCHANGED
                # report-only object still folds into the summarised count, not here.)
                report_only = self._is_report_only(delta.object_type)
                rows.append({
                    "action": REPORT_ONLY if report_only else delta.action,
                    "object_type": delta.object_type,
                    "object": delta.full_name,
                    "detail": (
                        "pipeline-managed / Tier-A asset — reported, not migrated"
                        if report_only else delta.reason
                    ),
                })
            for principal, privs in delta.grants_added.items():
                rows.append({
                    "action": GRANT_ADDED,
                    "object_type": delta.object_type,
                    "object": delta.full_name,
                    "detail": f"{principal}: {', '.join(privs)}",
                })
            for principal, privs in delta.grants_removed.items():
                rows.append({
                    "action": GRANT_REMOVED,
                    "object_type": delta.object_type,
                    "object": delta.full_name,
                    "detail": (
                        f"{principal}: {', '.join(privs)} "
                        "(reported, not actioned — never revoked)"
                    ),
                })
        for delta in self.source_absent:
            rows.append({
                "action": SOURCE_ABSENT,
                "object_type": delta.object_type,
                "object": delta.full_name,
                "detail": "reported, not actioned — never dropped",
            })
        return rows

    def unchanged_count(self) -> int:
        return sum(
            1 for d in self._by_name.values() if d.fully_unchanged
        )
