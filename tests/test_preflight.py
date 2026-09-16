"""Task 9: graded environment preflight (GO / GO-WITH-WARNINGS / NO-GO)."""

from __future__ import annotations

import pytest

from uc_sync.preflight import (
    Check,
    PreflightError,
    PreflightResult,
    BLOCKING,
    DEGRADING,
    GO,
    GO_WITH_WARNINGS,
    NO_GO,
    check_libraries,
    enforce_preflight,
    run_preflight,
)


def test_verdict_grades_by_severity():
    # All pass → GO.
    r = PreflightResult([Check("a", BLOCKING, True), Check("b", DEGRADING, True)])
    assert r.verdict == GO
    # A degrading failure → GO-WITH-WARNINGS.
    r = PreflightResult([Check("a", BLOCKING, True), Check("b", DEGRADING, False)])
    assert r.verdict == GO_WITH_WARNINGS
    # A blocking failure → NO-GO (dominates).
    r = PreflightResult([Check("a", BLOCKING, False), Check("b", DEGRADING, False)])
    assert r.verdict == NO_GO


def test_check_libraries_one_per_required_lib_and_openpyxl_present():
    # One BLOCKING check per required library; openpyxl (the report dep, used
    # throughout the report tests) must be present and pass in any env that can run
    # this suite.
    checks = check_libraries()
    assert len(checks) == 3
    assert all(c.severity == BLOCKING for c in checks)
    openpyxl_check = next(c for c in checks if "openpyxl" in c.name)
    assert openpyxl_check.passed, openpyxl_check.message


def test_missing_library_is_blocking_nogo(monkeypatch):
    import uc_sync.preflight as pf
    # Simulate openpyxl absent (the observed silent-no-report failure mode).
    monkeypatch.setattr(pf, "_installed_version", lambda pkg: None)
    checks = pf.check_libraries()
    assert all(not c.passed and c.severity == BLOCKING for c in checks)
    result = pf.run_preflight(check_libs=True)
    assert result.verdict == NO_GO


def test_unreachable_warehouse_is_blocking():
    r = run_preflight(check_libs=False, warehouse_reachable=False)
    assert r.verdict == NO_GO
    assert "warehouse" in r.blocking_failures[0].name.lower()


def test_ops_state_absent_is_degrading_only():
    r = run_preflight(check_libs=False, ops_state_present=False)
    assert r.verdict == GO_WITH_WARNINGS


def test_enforce_raises_on_nogo_and_downgrades_when_off(capsys):
    nogo = PreflightResult([Check("lib x", BLOCKING, False, "missing")])
    with pytest.raises(PreflightError):
        enforce_preflight(nogo, enforce=True)
    # With enforcement off, a NO-GO is a loud warning, not an exception.
    enforce_preflight(nogo, enforce=False)
    out = capsys.readouterr().out
    assert "WARNING" in out and "NO-GO" in out


def test_none_probe_is_skipped_not_failed():
    # An un-probed condition (None) must not fail the preflight.
    r = run_preflight(check_libs=False, warehouse_reachable=None,
                      catalogs_readable=None, ops_state_present=None)
    assert r.verdict == GO
    assert r.checks == []
