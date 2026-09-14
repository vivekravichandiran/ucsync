"""Single source of truth for the migration status vocabulary (``last_action``).

One closed set of status keys is stored verbatim in ``uc_sync_state.last_action`` and
rendered verbatim in the report, so state and report never disagree. This replaces the
old split — where the state table spoke SUCCESS / UNCHANGED / ADOPTED / REPORT_ONLY /
… and the report spoke created / updated / adopted / skipped / … — with ONE vocabulary
used by both (the confirmed "constant naming + vocab" decision).

Mirrors the workspace-migration utility's ``LAST_ACTIONS`` (``src/state/state_store.py``)
so an operator reads the same words across both tools.
"""

from __future__ import annotations

from typing import Any, Optional

# --- the closed vocabulary (wsmig LAST_ACTIONS) -----------------------------
CREATED = "created"
CREATED_WITH_WARNING = "created_with_warning"
UPDATED = "updated"
ADOPTED = "adopted"
SKIPPED = "skipped"
# BYO / existing-catalog: the create toggle for this type is OFF, so the utility did
# not create it (a Skipped variant, distinct from "unchanged" and from "adopted" — the
# confirmed B3 decision). UC-specific (wsmig has no BYO concept), so it extends the
# shared set rather than renaming a wsmig key.
SKIPPED_CREATE_DISABLED = "skipped_create_disabled"
NOT_SELECTED = "not_selected"
SKIPPED_NO_OBJECT = "skipped_no_object"
DELETED_IN_SOURCE = "deleted_in_source"
MANUAL = "manual"
FAILED = "failed"

LAST_ACTIONS = (
    CREATED, CREATED_WITH_WARNING, UPDATED, ADOPTED, SKIPPED,
    SKIPPED_CREATE_DISABLED, NOT_SELECTED, SKIPPED_NO_OBJECT, DELETED_IN_SOURCE,
    MANUAL, FAILED,
)

# Outstanding = still-broken across ALL runs (wsmig OUTSTANDING_ACTIONS = {failed}).
# The Outstanding report sheet lists every state row whose last_action is in this set.
OUTSTANDING_ACTIONS = {FAILED}

# The "clean / present" set: an object last recorded in one of these states is on the
# target and fine, so an UNCHANGED source may be skipped on an incremental run. Any
# other state (failed / manual, or the absent/deferred variants) is re-attempted even
# when the source fingerprint is unchanged — bug #18: a green incremental must never
# mask an object that is missing / failed / not-fully-applied on target. Legacy values
# (pre-rename baselines, before backfill) are folded in by ``is_clean_action``.
CLEAN_ACTIONS = {
    CREATED, CREATED_WITH_WARNING, UPDATED, ADOPTED, SKIPPED, SKIPPED_CREATE_DISABLED,
}

# Legacy uc_sync_state.last_sync_status values → the equivalent last_action key, so an
# old baseline (read before the ADD COLUMNS backfill) still reads correctly and the
# delta planner treats a legacy "SUCCESS"/"ADOPTED"/… as present.
_LEGACY_STATUS_TO_ACTION = {
    "SUCCESS": CREATED,
    "UNCHANGED": SKIPPED,
    "ADOPTED": ADOPTED,
    "REPORT_ONLY": MANUAL,
    "SKIPPED": NOT_SELECTED,
    "MANUAL_ACTION_REQUIRED": MANUAL,
    "PENDING": SKIPPED,
    "FAILURE": FAILED,
}

# Display label + cell fill hex per status key (wsmig _STATUS_STYLE). One place so the
# per-type sheets, the Summary roll-up and the Outstanding sheet all render identically.
STATUS_STYLE: dict[str, tuple[str, str]] = {
    CREATED: ("Created", "D1FAE5"),
    CREATED_WITH_WARNING: ("Created (warning)", "FDE68A"),
    UPDATED: ("Updated", "DBEAFE"),
    ADOPTED: ("Adopted (pre-existing)", "CFFAFE"),
    SKIPPED: ("Skipped (unchanged)", "E5E7EB"),
    SKIPPED_CREATE_DISABLED: ("Skipped (create disabled — BYO)", "E5E7EB"),
    MANUAL: ("Manual step", "FEF3C7"),
    NOT_SELECTED: ("Deferred (not selected)", "F1F5F9"),
    SKIPPED_NO_OBJECT: ("Skipped (no target object)", "EDE9FE"),
    DELETED_IN_SOURCE: ("Deleted in source", "FFE4E6"),
    FAILED: ("FAILED", "FEE2E2"),
    "": ("—", "FFFFFF"),
}

# Summary roll-up order — FAILURES FIRST, then created/updated/adopted, then the
# skip/defer variants, manual, and deleted-in-source last.
SUMMARY_ORDER = (
    FAILED, CREATED, CREATED_WITH_WARNING, UPDATED, ADOPTED,
    SKIPPED, SKIPPED_CREATE_DISABLED, SKIPPED_NO_OBJECT, NOT_SELECTED, MANUAL,
    DELETED_IN_SOURCE,
)
# Which roll-up buckets count as "actually applied" vs. skipped (honest totals, #12).
SUCCESS_STATUSES = {CREATED, CREATED_WITH_WARNING, UPDATED, ADOPTED}
# The skip family (counted outside success in the Summary "skipped" total).
SKIP_STATUSES = {SKIPPED, SKIPPED_CREATE_DISABLED, SKIPPED_NO_OBJECT, NOT_SELECTED}


def label(key: str) -> str:
    """The display label for a status key (falls back to the raw key)."""
    return STATUS_STYLE.get(key, (key or "—", ""))[0]


def is_clean_action(action: Optional[str]) -> bool:
    """True when a prior ``last_action`` (or a legacy ``last_sync_status``) means the
    object is present and cleanly applied, so an unchanged source may be skipped.

    A blank / unknown value reads as clean, so the FIRST incremental after an upgrade
    (a legacy baseline whose column has not been backfilled yet) stays idempotent.
    """
    if not action:
        return True
    raw = str(action).strip()
    if raw in _LEGACY_STATUS_TO_ACTION:  # legacy last_sync_status value
        return _LEGACY_STATUS_TO_ACTION[raw] in CLEAN_ACTIONS
    return raw.lower() in CLEAN_ACTIONS


def status_key(entry: Optional[dict[str, Any]]) -> str:
    """Map an internal import-result entry (``status`` / ``action`` / ``delta_action``)
    to a ``last_action`` key. Used by BOTH the report renderer and the state upsert, so
    a single object is labelled the same in the workbook and in ``uc_sync_state``.

    A report-only / never-migrated asset TYPE (streaming tables, MVs w/o the toggle,
    Tier-A AI assets) reads as ``manual`` — an inventory-only "manual step" the operator
    migrates by hand (the confirmed decision). ``skipped_no_object`` is reserved for the
    ``not entry`` case (a grant/ACL whose target object is absent). A source object gone
    from source reads ``deleted_in_source``.
    """
    if not entry:
        return SKIPPED_NO_OBJECT
    status = str(entry.get("status") or "")
    action = str(entry.get("action") or "")
    delta_action = str(entry.get("delta_action") or "")
    if delta_action == "SOURCE_ABSENT":
        return DELETED_IN_SOURCE
    # Report-only asset TYPES: inventory-only, surfaced as a manual step (B4 / confirmed
    # decision) — off skipped_no_object, which is only for a missing target object.
    if action in ("REPORT_ONLY", "SKIP_REPORT_ONLY") or delta_action == "REPORT_ONLY":
        return MANUAL
    if status == "FAILURE":
        return FAILED
    if status == "MANUAL_ACTION_REQUIRED" or action == "MANUAL_ACTION_REQUIRED":
        return MANUAL
    if status == "SUCCESS_WITH_WARNINGS":
        return CREATED_WITH_WARNING
    # A CHANGED table whose only diff is a column DROP or TYPE CHANGE (both out of
    # scope — non-destructive / unsupported) produced no actionable change: a Skipped
    # variant with a comment (A3/A4), never a misleading "Updated".
    if delta_action == "CHANGED_SKIPPED":
        return SKIPPED
    # A change applied to a PRE-EXISTING object (DDL / governance / grants / column
    # delta) reads as "Updated" — never "Adopted"/"Skipped" (bug #19). Checked before
    # the skip/adopt branches so a changed-but-pre-existing object isn't mislabeled.
    if delta_action in (
        "CHANGED", "REPLACED", "GOVERNANCE_UPDATED", "GRANTS_UPDATED", "COLUMN_ADDED"
    ) or action == "COLUMN_ADDED":
        return UPDATED
    # BYO / existing-catalog: the create toggle was OFF — a distinct Skipped variant
    # (B3), never conflated with "unchanged" or "adopted".
    if action == "SKIP_CREATE_DISABLED":
        return SKIPPED_CREATE_DISABLED
    # Incremental skip: unchanged since a prior present run — a Skipped variant, never
    # "Created" (a skipped object must not read as created/applied).
    if action == "UNCHANGED" or status == "UNCHANGED" or delta_action == "UNCHANGED":
        # A pre-existing object with no delta is Adopted; a delta-gated one is Skipped.
        if action == "SKIP_EXISTING":
            return ADOPTED
        return SKIPPED
    # Pre-existing, adopted as-is (no change applied this run).
    if action == "SKIP_EXISTING":
        return ADOPTED
    if action == "SKIP_FILTERED":
        return NOT_SELECTED
    if status in ("SUCCESS", "PENDING"):
        return CREATED
    return SKIPPED
