"""Tests for the two report/state fixes:
(b) a skipped structural change (drop/type) riding alongside an applied change is
    surfaced as a caveat on the "Updated" label — never silent.
(a) an object a newer version classifies report-only but an older version migrated
    self-heals its stale uc_sync_state row + flags the orphan on target.
"""
from uc_sync.report import _render_import_status
from uc_sync.package_import import PackageImportEngine


# ---- change (b): caveat on the label ----

def test_updated_appends_caveat():
    entry = {
        "status": "SUCCESS", "action": "", "message": "",
        "delta_action": "CHANGED",
        "caveat": "column type changed on source (id: int → bigint); "
                  "not altered on target (out of scope)",
    }
    s = _render_import_status(entry)
    assert s.startswith("Updated")
    assert "type changed" in s


def test_pure_skip_still_reads_skipped():
    entry = {
        "status": "SUCCESS", "action": "",
        "message": "column deleted on source (note); not dropped on target (non-destructive)",
        "delta_action": "CHANGED_SKIPPED", "caveat": "",
    }
    s = _render_import_status(entry)
    assert s.startswith("Skipped —")
    assert "column deleted" in s


def test_caveat_never_hides_a_failure():
    entry = {"status": "FAILURE", "action": "", "message": "boom",
             "delta_action": "CHANGED", "caveat": "should not show"}
    s = _render_import_status(entry)
    assert "should not show" not in s
    assert s.startswith("FAILED")


# ---- change (a): state self-heal for reclassified report-only objects ----

def _engine():
    e = PackageImportEngine.__new__(PackageImportEngine)
    e._created_objects = {}
    e.prior_state = {}
    return e


def test_selfheal_emits_manual_result_for_migrated_then_reclassified():
    e = _engine()
    e.prior_state = {"c.s.m_profile_metrics": {"last_action": "created"}}
    inv = {"c.s.m_profile_metrics": {
        "object_type": "MONITOR_METRIC_TABLE",
        "source_full_name": "c.s.m_profile_metrics",
        "target_full_name": "c.s.m_profile_metrics",
    }}
    out = e._selfheal_reclassified_report_only(inv, 0)
    assert len(out) == 1
    assert out[0].action == "REPORT_ONLY"
    assert "orphan" in out[0].message.lower()


def test_selfheal_skips_report_only_never_materialized():
    e = _engine()  # no prior state → was never migrated
    inv = {"c.s.idx": {
        "object_type": "VECTOR_INDEX",
        "source_full_name": "c.s.idx", "target_full_name": "c.s.idx",
    }}
    assert e._selfheal_reclassified_report_only(inv, 0) == []


def test_selfheal_skips_normal_tables():
    e = _engine()
    e.prior_state = {"c.s.t": {"last_action": "created"}}
    inv = {"c.s.t": {
        "object_type": "TABLE",
        "source_full_name": "c.s.t", "target_full_name": "c.s.t",
    }}
    assert e._selfheal_reclassified_report_only(inv, 0) == []
