"""The unified status vocabulary (uc_sync.vocab) — one set of keys shared by the
report and uc_sync_state.last_action, plus the incremental "clean action" gate."""

from __future__ import annotations

from uc_sync import vocab
from uc_sync.sync_state import state_row_from_import


def test_state_last_action_matches_report_status_key_one_to_one():
    """The invariant behind the unification: the last_action written to uc_sync_state
    is EXACTLY the status key the report renders for the same result — so an object is
    never labelled one word in the workbook and a different word in the state table."""
    results = [
        {"object_type": "TABLE", "source_full_name": "s.s.t", "status": "SUCCESS",
         "action": "CREATE_OR_SKIP"},
        {"object_type": "TABLE", "source_full_name": "s.s.u", "status": "SUCCESS",
         "action": "SKIP_EXISTING", "delta_action": "GRANTS_UPDATED"},
        {"object_type": "TABLE", "source_full_name": "s.s.a", "status": "SUCCESS",
         "action": "SKIP_EXISTING"},
        {"object_type": "TABLE", "source_full_name": "s.s.f", "status": "FAILURE",
         "error_code": "X", "error_message": "boom"},
        {"object_type": "EXTERNAL_TABLE", "source_full_name": "s.s.m",
         "status": "MANUAL_ACTION_REQUIRED", "action": "MANUAL"},
    ]
    expected = ["created", "updated", "adopted", "failed", "manual"]
    for result, want in zip(results, expected):
        key = vocab.status_key(result)
        assert key == want
        row = state_row_from_import(
            batch_id="b", run_id="r", result=result, ran_by="me", utility_version="9",
        )
        # State stores the SAME vocabulary key the report renders from.
        assert row["last_action"] == key
        assert key in vocab.STATUS_STYLE  # renderable label exists


def test_is_clean_action_new_and_legacy_and_blank():
    # New vocab: created/updated/adopted/skipped/created_with_warning are clean.
    for a in ("created", "updated", "adopted", "skipped", "created_with_warning"):
        assert vocab.is_clean_action(a) is True
    # Not clean → re-attempted next incremental (bug #18).
    for a in ("failed", "manual", "skipped_no_object", "not_selected", "deleted_in_source"):
        assert vocab.is_clean_action(a) is False
    # Legacy last_sync_status values still classify correctly (pre-backfill baseline).
    assert vocab.is_clean_action("SUCCESS") is True
    assert vocab.is_clean_action("ADOPTED") is True
    assert vocab.is_clean_action("UNCHANGED") is True
    assert vocab.is_clean_action("FAILURE") is False
    assert vocab.is_clean_action("MANUAL_ACTION_REQUIRED") is False
    # Blank / unknown reads as clean so the first post-upgrade incremental is idempotent.
    assert vocab.is_clean_action("") is True
    assert vocab.is_clean_action(None) is True


def test_outstanding_actions_is_failures_only():
    """Outstanding (cumulative-broken) mirrors wsmig: failures only."""
    assert vocab.OUTSTANDING_ACTIONS == {"failed"}
