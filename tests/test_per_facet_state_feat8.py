"""Per-facet state tracking (backlog item 8): ddl_status / governance_status /
grants_status, hash-gating, backfill, and the delta facet-retry rule (#2) that closes
#7 and the external-object grants-replay gap.
"""

from __future__ import annotations

from uc_sync import vocab
from uc_sync.delta import DeltaPlan, GOVERNANCE_UPDATED, GRANTS_UPDATED, UNCHANGED
from uc_sync.fingerprints import (
    ddl_fingerprint, governance_fingerprint, grant_fingerprint_set,
)
from uc_sync.sync_state import (
    _facet_statuses, state_row_from_import, STATE_COLUMNS,
    FACET_APPLIED, FACET_SKIPPED, FACET_FAILED, FACET_NOT_ATTEMPTED,
    SyncStateService,
)


# --- facet-status derivation (matrix rows) ----------------------------------

def _facets(result):
    la = vocab.status_key(dict(result))
    from uc_sync.sync_state import _failure_category_for
    cat = _failure_category_for(str(result.get("error_code") or ""), la)
    return _facet_statuses(result, la, cat)


def test_matrix_row1_fresh_created_governed_clean():
    ddl, gov, gr = _facets({"status": "SUCCESS", "action": "CREATE_OR_SKIP",
                            "governance_hash": "gh"})
    assert (ddl, gov) == (FACET_APPLIED, FACET_APPLIED)


def test_matrix_row2_fresh_dropped_on_governance_fail_ddl_failed():
    # A fresh shell dropped fail-closed: ddl rolls back → failed; governance failed.
    ddl, gov, gr = _facets({"status": "FAILURE", "action": "DROP_PROTECTION_FAILED",
                            "error_code": "PROTECTION_FAILED"})
    assert ddl == FACET_FAILED
    assert gov == FACET_FAILED


def test_matrix_row5_preexisting_governance_fail_ddl_stays_applied():
    # Pre-existing table, tag failed, NOT dropped: DDL is fine, governance failed.
    ddl, gov, gr = _facets({"status": "FAILURE", "action": "GOVERNANCE_FAILED",
                            "error_code": "PROTECTION_FAILED"})
    assert ddl == FACET_APPLIED
    assert gov == FACET_FAILED


def test_matrix_row6_create_disabled_clean():
    ddl, gov, gr = _facets({"status": "SUCCESS", "action": "SKIP_CREATE_DISABLED",
                            "governance_hash": "gh"})
    assert ddl == FACET_SKIPPED
    assert gov == FACET_APPLIED


def test_grants_failure_recorded_on_clean_object():
    # ddl/governance clean but a grant raised → grants_status = failed.
    ddl, gov, gr = _facets({"status": "SUCCESS", "action": "SKIP_CREATE_DISABLED",
                            "grants_status": "failed"})
    assert gr == FACET_FAILED
    assert ddl == FACET_SKIPPED


def test_ddl_failure_governance_not_attempted():
    ddl, gov, gr = _facets({"status": "FAILURE", "action": "EXTERNAL_CREATE_FAILED",
                            "error_code": "EXTERNAL_CREATE_FAILED"})
    assert ddl == FACET_FAILED
    assert gov == FACET_NOT_ATTEMPTED


# --- state_row_from_import wires the statuses + STATE_COLUMNS alignment ------

def test_state_row_carries_facet_statuses_and_columns_match():
    row = state_row_from_import(
        batch_id="b", run_id="r", ran_by="me", utility_version="9",
        result={"object_type": "TABLE", "source_full_name": "c.s.t",
                "status": "FAILURE", "action": "GOVERNANCE_FAILED",
                "error_code": "PROTECTION_FAILED", "governance_hash": "gh"},
    )
    assert row["ddl_status"] == FACET_APPLIED
    assert row["governance_status"] == FACET_FAILED
    # Every produced key is a real state column (MERGE alignment).
    assert set(row) <= set(STATE_COLUMNS)
    assert {"ddl_status", "governance_status", "grants_status"} <= set(STATE_COLUMNS)


# --- delta facet-retry (rule #2) --------------------------------------------

def _row():
    return {"object_type": "TABLE", "full_name": "c.s.t", "owner": "me",
            "tags": {"cls": "OK"},
            "grants": [{"principal": "u@x.com", "privileges": ["SELECT"]}],
            "definition": {"columns": [{"name": "id"}]}}


def _clean_baseline(row, **over):
    base = {
        "object_type": row["object_type"],
        "ddl_hash": ddl_fingerprint(row),
        "governance_hash": governance_fingerprint(row),
        "grants": grant_fingerprint_set(row),
        "last_action": vocab.CREATED,
        "ddl_status": FACET_APPLIED,
        "governance_status": FACET_APPLIED,
        "grants_status": FACET_APPLIED,
    }
    base.update(over)
    return {row["full_name"]: base}


def test_fully_clean_object_is_skipped():
    row = _row()
    plan = DeltaPlan([row], _clean_baseline(row))
    assert plan.should_skip_object("c.s.t") is True
    assert plan.action("c.s.t") == UNCHANGED


def test_governance_status_failed_reapplies_even_when_hash_unchanged():
    # Field case (#7): last_action clean, governance_hash unchanged, but the prior
    # governance apply FAILED. The delta must re-apply governance, not no-op.
    row = _row()
    baseline = _clean_baseline(row, governance_status=FACET_FAILED)
    plan = DeltaPlan([row], baseline)
    assert plan.should_skip_object("c.s.t") is False
    assert plan.governance_changed("c.s.t") is True


def test_grants_status_failed_reapplies_grants_replay_gap():
    # External-object-created-later gap: ddl+governance clean+unchanged, but the prior
    # grant apply failed → grants must replay on this run.
    row = _row()
    baseline = _clean_baseline(row, grants_status=FACET_FAILED)
    plan = DeltaPlan([row], baseline)
    assert plan.should_skip_object("c.s.t") is False
    delta = plan.get("c.s.t")
    assert delta.action == GRANTS_UPDATED
    assert delta.grants_added  # the current grant set is re-offered


def test_clean_facets_unchanged_still_skips_regression():
    # All facets applied + hashes unchanged → still a no-op (no false retry).
    row = _row()
    plan = DeltaPlan([row], _clean_baseline(row))
    assert plan.unchanged_count() == 1


# --- MERGE hash-gating + backfill (fake spark) ------------------------------

class _DType:
    def simpleString(self): return "string"


class _Field:
    def __init__(self, name):
        self.name = name
        self.dataType = _DType()


class _FakeSpark:
    def __init__(self, columns):
        self.sql_log = []
        self._cols = columns

    def sql(self, sql):
        self.sql_log.append(sql)
        return None

    def table(self, name):
        cols = self._cols
        class _Schema:
            fields = [_Field(c) for c in cols]
            def __iter__(self): return iter(self.fields)
        class _T:
            schema = _Schema()
        return _T()

    def createDataFrame(self, rows, schema=None):
        outer = self
        class _DF:
            def createOrReplaceTempView(self, name): outer.sql_log.append(f"TEMPVIEW {name}")
        return _DF()


def test_merge_gates_facet_hash_advancement():
    cols = STATE_COLUMNS
    spark = _FakeSpark(cols)
    svc = SyncStateService(spark, "ops.ops.uc_sync_state")
    svc.upsert([{c: "" for c in cols} | {"source_full_name": "c.s.t",
                                         "object_type": "TABLE"}])
    merge = next(s for s in spark.sql_log if "MERGE INTO" in s)
    # Each facet hash is gated on its paired status column.
    assert "target.governance_hash = CASE WHEN source.governance_status IN" in merge
    assert "target.ddl_hash = CASE WHEN source.ddl_status IN" in merge
    assert "target.grants_json = CASE WHEN source.grants_status IN" in merge


def test_facet_backfill_sql_emitted_for_legacy_rows():
    # A legacy table (columns present after ADD) → the facet-status backfill UPDATE fires.
    cols = list(STATE_COLUMNS) + ["last_sync_status"]
    spark = _FakeSpark(cols)
    SyncStateService(spark, "ops.ops.uc_sync_state").ensure_table()
    assert any("ddl_status = CASE" in s for s in spark.sql_log)
    assert any("WHERE ddl_status IS NULL" in s for s in spark.sql_log)
