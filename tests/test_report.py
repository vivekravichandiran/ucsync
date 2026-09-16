"""Migration report (Excel) generation test."""
from __future__ import annotations

from uc_sync.report import build_report


def test_volume_data_copy_sheet_present_only_when_results_given(tmp_path):
    """Bug #17: FEAT-4 file-copy results must surface in the report as a 'Volume Data
    Copy' sheet (with a roll-up), and only when the caller supplies them."""
    from openpyxl import load_workbook

    objects = [{"object_type": "VOLUME", "full_name": "c.s.v", "owner": "me",
                "tags": {}, "grants": []}]
    copy_rows = [
        {"volume": "c.s.v", "source_path": "/Volumes/c/s/v/a.csv",
         "target_path": "/Volumes/c/s/v/a.csv", "status": "COPIED", "bytes_copied": 12},
        {"volume": "c.s.v", "source_path": "/Volumes/c/s/v/b.csv",
         "target_path": "/Volumes/c/s/v/b.csv", "status": "SKIPPED_UNCHANGED",
         "bytes_copied": 0, "message": "unchanged since last copy"},
        {"volume": "c.s.v", "source_path": "/Volumes/c/s/v/big.bin",
         "target_path": "/Volumes/c/s/v/big.bin", "status": "SKIPPED_TOO_LARGE",
         "bytes_copied": 0, "message": "> 5 GB"},
    ]
    # Without results → no sheet.
    out0 = tmp_path / "no_copy.xlsx"
    build_report(objects, str(out0), stage="IMPORT",
                 import_results=[{"target_full_name": "c.s.v", "status": "SUCCESS"}],
                 run_id="r1")
    assert "Volume Data Copy" not in load_workbook(out0).sheetnames

    # With results → sheet present, rows + roll-up rendered.
    out = tmp_path / "copy.xlsx"
    build_report(objects, str(out), stage="IMPORT",
                 import_results=[{"target_full_name": "c.s.v", "status": "SUCCESS"}],
                 volume_copy_results=copy_rows, run_id="r1")
    wb = load_workbook(out)
    assert "Volume Data Copy" in wb.sheetnames
    text = "\n".join(
        str(r) for r in wb["Volume Data Copy"].iter_rows(values_only=True)
    )
    assert "Copied" in text and "Skipped (unchanged)" in text and "Skipped (>5 GB)" in text
    assert "bytes copied" in text  # roll-up footer


def test_unchanged_action_reads_as_skipped_not_created(tmp_path):
    """Bug #19: an incremental UNCHANGED skip must roll up as a Skipped variant, never
    'Created' — otherwise a skipped (or previously-failed) object reads as applied."""
    from uc_sync.report import _wsmig_status_key
    assert _wsmig_status_key({"status": "UNCHANGED", "action": "UNCHANGED"}) == "skipped"
    # (regression guard: a plain SUCCESS still reads as created)
    assert _wsmig_status_key({"status": "SUCCESS", "action": "CREATE"}) == "created"


def test_changed_preexisting_object_reads_updated_through_report(tmp_path):
    """Bug #19 integration: a governance/grants change on a PRE-EXISTING object must
    read 'Updated' on its per-type sheet — end to end through build_report (regression
    for _import_index dropping delta_action, which made it read 'Adopted')."""
    from openpyxl import load_workbook
    objects = [
        {"object_type": "TABLE", "full_name": "c.s.upd", "owner": "me",
         "tags": {}, "grants": []},
        {"object_type": "TABLE", "full_name": "c.s.new", "owner": "me",
         "tags": {}, "grants": []},
    ]
    import_results = [
        # pre-existing table whose grants changed → GRANTS_UPDATED + SKIP_EXISTING.
        {"object_type": "TABLE", "target_full_name": "c.s.upd", "full_name": "c.s.upd",
         "status": "SUCCESS", "action": "SKIP_EXISTING", "delta_action": "GRANTS_UPDATED"},
        # a genuinely new table.
        {"object_type": "TABLE", "target_full_name": "c.s.new", "full_name": "c.s.new",
         "status": "SUCCESS", "action": "CREATE_OR_SKIP", "delta_action": "CREATED_NEW"},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), stage="IMPORT",
                 import_results=import_results, run_id="r1")
    rows = {r[0]: r[-1] for r in load_workbook(out)["Tables"].iter_rows(values_only=True)}
    assert rows["c.s.upd"] == "Updated"
    assert rows["c.s.new"] == "Created"


def test_source_absent_shows_on_own_type_sheet_and_summary(tmp_path):
    """B2 + SOURCE_ABSENT visibility fix: a dropped source object (present in the
    baseline, gone from source) is shown as 'Deleted in source' on its OWN per-type
    sheet AND in the Summary 'Deleted in source — review' section — no Delta sheet."""
    from openpyxl import load_workbook
    objects = [
        {"object_type": "TABLE", "full_name": "c.s.keep", "owner": "me",
         "tags": {}, "grants": []},
    ]
    import_results = [
        {"object_type": "TABLE", "target_full_name": "c.s.keep", "full_name": "c.s.keep",
         "status": "SUCCESS", "action": "CREATE_OR_SKIP"},
    ]
    delta_rows = [
        {"action": "SOURCE_ABSENT", "object_type": "TABLE", "object": "c.s.gone",
         "detail": "reported, not actioned — never dropped"},
        {"action": "UNCHANGED_COUNT", "detail": 4},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), stage="IMPORT", import_results=import_results,
                 delta_rows=delta_rows, run_id="r1")
    wb = load_workbook(out)
    assert "Delta" not in wb.sheetnames

    # The dropped table appears on the Tables sheet with a Deleted-in-source status.
    tables = {r[0]: r[-1] for r in wb["Tables"].iter_rows(values_only=True)}
    assert tables.get("c.s.gone") == "Deleted in source"
    assert tables.get("c.s.keep") == "Created"

    # Summary carries the review section + the unchanged tally (moved off the Delta sheet).
    summary = [str(c) for row in wb["Summary"].iter_rows(values_only=True)
               for c in row if c is not None]
    assert any(s.startswith("Deleted in source — review (1)") for s in summary)
    assert any("unchanged" in s for s in summary)


def test_outstanding_sheet_lists_cumulative_failures_from_state(tmp_path):
    """Part E: the Outstanding sheet lists cumulative still-broken objects from state
    (last_action=failed) across ALL runs — the second failure surface after Summary."""
    from openpyxl import load_workbook
    objects = [{"object_type": "TABLE", "full_name": "c.s.t", "owner": "me",
                "tags": {}, "grants": []}]
    import_results = [{"object_type": "TABLE", "target_full_name": "c.s.t",
                       "full_name": "c.s.t", "status": "SUCCESS", "action": "CREATE_OR_SKIP"}]
    # State says an object from an EARLIER run (out of this run's scope) is still failed.
    outstanding = [
        {"source_full_name": "c.other.broken", "object_type": "EXTERNAL_TABLE",
         "last_action": "failed", "error_code": "EXTERNAL_CREATE_FAILED",
         "error_message": "DELTA property mismatch", "run_id": "r0",
         "last_sync_at": "2026-09-10T00:00:00Z"},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), stage="IMPORT", import_results=import_results,
                 outstanding=outstanding, run_id="r1")
    wb = load_workbook(out)
    assert "Outstanding" in wb.sheetnames
    rows = list(wb["Outstanding"].iter_rows(values_only=True))
    assert rows[0][0] == "object"
    body = [r for r in rows[1:] if r[0] == "c.other.broken"]
    assert len(body) == 1
    assert body[0][2] == "FAILED"  # last_action rendered via shared vocab label
    assert "DELTA property mismatch" in str(body[0][4])
    # No Outstanding sheet when the caller supplies none (inventory/export stages).
    out2 = tmp_path / "inv.xlsx"
    build_report(objects, str(out2), stage="INVENTORY")
    assert "Outstanding" not in load_workbook(out2).sheetnames


def test_governed_tags_sheet_replaces_other_objects(tmp_path):
    """B5: governed-tag definitions get a first-class 'Governed Tags' sheet, not the
    generic 'Other Objects' catch-all."""
    from openpyxl import load_workbook
    objects = [
        {"object_type": "GOVERNED_TAG", "full_name": "pii", "owner": "me",
         "tags": {}, "grants": [],
         "definition": {"allowed_values": ["SSN", "EMAIL"]}},
    ]
    import_results = [{"object_type": "GOVERNED_TAG", "full_name": "pii",
                       "target_full_name": "pii", "status": "SUCCESS",
                       "action": "CREATE_OR_SKIP"}]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), stage="IMPORT", import_results=import_results,
                 run_id="r1")
    wb = load_workbook(out)
    assert "Governed Tags" in wb.sheetnames and "Other Objects" not in wb.sheetnames
    rows = list(wb["Governed Tags"].iter_rows(values_only=True))
    assert rows[0][0] == "tag_key"
    body = rows[1]
    assert body[0] == "pii" and "SSN" in body[1] and "EMAIL" in body[1]


def test_tags_applied_gap_reads_explicit_no_op(tmp_path):
    """B6: a governed-tag row with no tag op recorded reads an explicit note, not blank."""
    from openpyxl import load_workbook
    objects = [{"object_type": "TABLE", "full_name": "c.s.t", "owner": "me",
                "tags": {"cls": "OK"}, "grants": []}]
    # An object create result, but NO APPLY_TAGS op for it.
    import_results = [{"object_type": "TABLE", "target_full_name": "c.s.t",
                       "full_name": "c.s.t", "status": "SUCCESS", "action": "CREATE_OR_SKIP"}]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), stage="IMPORT", import_results=import_results,
                 run_id="r1")
    tag_rows = list(load_workbook(out)["Tags Applied"].iter_rows(values_only=True))
    assert tag_rows[1][-1] == "— (no tag op this run)"


def test_build_report_has_governance_sheets(tmp_path):
    objects = [
        {"object_type": "CATALOG", "full_name": "c", "owner": "me",
         "tags": {"class": "INTERNAL"}, "grants": [
             {"principal": "account users", "principal_type": "GROUP",
              "privileges": ["USE_CATALOG"]}]},
        {"object_type": "TABLE", "full_name": "c.s.t", "owner": "me",
         "definition": {"column_masks": [
             {"column_name": "ssn", "function_name": "c.sec.m", "using_column_names": []}],
             "row_filter": {"function_name": "c.sec.rf", "input_column_names": ["dept"]},
             "column_tags": {"ssn": {"pii": "SSN"}}},
         "tags": {}, "grants": []},
        {"object_type": "ABAC_POLICY", "full_name": "c.s.t#policy:p",
         "definition": {"policy_name": "p", "policy_type": "COLUMN_MASK",
                        "on_securable": "c.s.t", "function_name": "c.sec.m",
                        "match_columns": ["has_tag_value('pii','SSN') AS c"],
                        "to_principals": ["account users"],
                        "except_principals": ["svc@x.com"]}},
    ]
    out = tmp_path / "report.xlsx"
    results = [{"target_full_name": "c.s.t", "status": "SUCCESS"}]
    build_report(objects, str(out), import_results=results, run_id="r1")
    assert out.exists()
    from openpyxl import load_workbook
    wb = load_workbook(out)
    # Per-type sheets appear only for types present; governance sheets always do. There
    # is no standalone Issues sheet (B1) and no Delta sheet (B2) — failures live on the
    # Summary, changes are folded into the per-type sheets.
    assert set(wb.sheetnames) == {
        "Summary", "Catalogs", "Tables", "Tags Applied",
        "Column Masks & Row Filters",
        "ABAC Policies", "Policy Matched Columns", "Grants",
    }
    assert "Issues" not in wb.sheetnames and "Delta" not in wb.sheetnames
    # Summary is first.
    assert wb.sheetnames[0] == "Summary"
    # ABAC sheet carries the policy with its EXCEPT.
    abac_rows = list(wb["ABAC Policies"].iter_rows(values_only=True))
    assert any("svc@x.com" in str(r) for r in abac_rows)
    # Tags sheet has the column tag.
    tag_rows = list(wb["Tags Applied"].iter_rows(values_only=True))
    assert any("SSN" in str(r) for r in tag_rows)
    # The table's per-type sheet carries its import status.
    table_rows = list(wb["Tables"].iter_rows(values_only=True))
    assert any("c.s.t" in str(r) for r in table_rows)


def test_summary_failures_section_and_no_issues_sheet(tmp_path):
    """B1: there is NO standalone Issues sheet — current-run failures live in the
    Summary "Failures (N)" section. The Summary keeps a single per-object tally (ABAC
    policies counted as objects; a governance failure folds into its object) with no
    separate governance section; governance sheets carry an import_status column."""
    objects = [
        {"object_type": "TABLE", "full_name": "c.s.t", "owner": "me",
         "tags": {"cls": "OK"}, "grants": []},
        {"object_type": "TABLE", "full_name": "c.s.bad", "owner": "me",
         "tags": {"missing": "x"}, "grants": []},
        {"object_type": "ABAC_POLICY", "full_name": "c.s.t#policy:p",
         "definition": {"policy_name": "p", "policy_type": "COLUMN_MASK",
                        "on_securable": "c.s.t", "function_name": "c.sec.m",
                        "match_columns": [], "to_principals": [],
                        "except_principals": []}},
    ]
    import_results = [
        # object creates first (so the per-object index resolves to these).
        {"object_type": "TABLE", "target_full_name": "c.s.t", "full_name": "c.s.t",
         "status": "SUCCESS", "action": "CREATE_OR_SKIP"},
        {"object_type": "TABLE", "target_full_name": "c.s.bad", "full_name": "c.s.bad",
         "status": "FAILURE", "action": "DROP_PROTECTION_FAILED",
         "error_code": "PROTECTION_FAILED",
         "message": "table dropped (fail-closed): a governance step failed"},
        # governance ops.
        {"object_type": "TABLE", "target_full_name": "c.s.bad", "full_name": "c.s.bad",
         "status": "FAILURE", "action": "MANUAL", "error_code": "PROTECTION_FAILED",
         "policies_path": "/tags/TABLE_c__s__bad.sql", "message": "unknown tag"},
        {"object_type": "ABAC_POLICY", "target_full_name": "c.s.t#policy:p",
         "full_name": "c.s.t#policy:p", "status": "SUCCESS", "action": "CREATE_POLICY",
         "policies_path": "/abac/x.sql"},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), import_results=import_results, run_id="r1")
    from openpyxl import load_workbook
    wb = load_workbook(out)

    # No standalone Issues sheet, no Delta sheet (B1/B2).
    assert "Issues" not in wb.sheetnames and "Delta" not in wb.sheetnames

    # Summary: a SINGLE per-object outcome roll-up (wsmig vocabulary), no separate
    # governance section, plus a "Failures (N)" section listing the failed object.
    summary = [tuple(r) for r in wb["Summary"].iter_rows(values_only=True)]
    flat = [str(c) for row in summary for c in row if c is not None]
    assert "Outcome roll-up" in flat
    assert "governed-tag operations" not in flat and "governance operations" not in flat
    # The created table + the ABAC policy = 2 Created, plus the dropped table's
    # FAILED = 1. The tag ALTER row is excluded (folded into the table it failed).
    sflat = {summary[i][0]: summary[i][1] for i in range(len(summary))
             if summary[i][0] in ("Created", "FAILED")}
    assert sflat.get("Created") == 2 and sflat.get("FAILED") == 1
    # A "Failures (1)" section lists the fail-closed table.
    assert any(str(c or "").startswith("Failures (1)") for c in flat)
    fail_names = [
        summary[i][0] for i in range(len(summary))
        if str(summary[i][0] or "") == "c.s.bad"
    ]
    assert fail_names  # the failed object is named in the Failures section

    # Tags sheet carries import_status; the bad table's tag reads FAILED.
    tag_rows = list(wb["Tags Applied"].iter_rows(values_only=True))
    assert tag_rows[0][-1] == "import_status"
    bad_tag = next(r for r in tag_rows[1:] if r[0] == "c.s.bad")
    assert "FAILED" in str(bad_tag[-1])

    # ABAC sheet also carries import_status keyed by the policy full name.
    abac_rows = list(wb["ABAC Policies"].iter_rows(values_only=True))
    assert abac_rows[0][-1] == "import_status"


def test_governance_rows_read_rolled_back_when_object_dropped_failclosed(tmp_path):
    """Task 6: a table dropped in the fail-closed sweep (DROP_PROTECTION_FAILED)
    had its tag / mask / grant ops applied at exec time, but they are gone now — so
    those governance rows read 'ROLLED BACK (object dropped)', not the exec-time
    SUCCESS the op result still carries. A distinct table whose CREATE failed
    atomically (bad inline mask) still reads FAILED (never applied, not rolled
    back)."""
    objects = [
        {"object_type": "TABLE", "full_name": "c.s.dropped", "owner": "me",
         "tags": {"cls": "SECRET"}, "grants": [
             {"principal": "u@x.com", "principal_type": "USER",
              "privileges": ["SELECT"]}],
         "definition": {"column_masks": [
             {"column_name": "ssn", "function_name": "c.sec.m",
              "using_column_names": []}]}},
    ]
    import_results = [
        # The table was created then dropped fail-closed (a governed tag failed).
        {"object_type": "TABLE", "target_full_name": "c.s.dropped",
         "full_name": "c.s.dropped", "status": "FAILURE",
         "action": "DROP_PROTECTION_FAILED", "error_code": "PROTECTION_FAILED",
         "message": "table dropped (fail-closed)"},
        # The APPLY_TAGS op recorded SUCCESS at exec time (before the drop).
        {"object_type": "TABLE", "target_full_name": "c.s.dropped",
         "full_name": "c.s.dropped", "status": "SUCCESS", "action": "APPLY_TAGS",
         "policies_path": "/tags/TABLE_c__s__dropped.sql"},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), import_results=import_results, run_id="r1")
    from openpyxl import load_workbook
    wb = load_workbook(out)

    tag_rows = list(wb["Tags Applied"].iter_rows(values_only=True))
    tag = next(r for r in tag_rows[1:] if r[0] == "c.s.dropped")
    assert tag[-1] == "Rolled back (table dropped fail-closed)"

    mask_rows = list(wb["Column Masks & Row Filters"].iter_rows(values_only=True))
    mask = next(r for r in mask_rows[1:] if r[0] == "c.s.dropped")
    assert mask[-1] == "Rolled back (table dropped fail-closed)"

    grant_rows = list(wb["Grants"].iter_rows(values_only=True))
    grant = next(r for r in grant_rows[1:] if r[0] == "c.s.dropped")
    assert grant[-1] == "Rolled back (table dropped fail-closed)"


def test_abac_counts_as_object_so_export_and_import_totals_match(tmp_path):
    """Parity: an ABAC policy is an object on BOTH sides, so the export-object total
    equals the import per-object total. Tags stay in the supplementary block (they
    are attributes on objects already counted, never part of the object total)."""
    from openpyxl import load_workbook

    objects = [
        {"object_type": "CATALOG", "full_name": "c", "owner": "me",
         "tags": {"cls": "INTERNAL"}, "grants": []},
        {"object_type": "TABLE", "full_name": "c.s.t", "owner": "me",
         "tags": {}, "grants": []},
        {"object_type": "ABAC_POLICY", "full_name": "c.s.t#policy:p",
         "definition": {"policy_name": "p", "policy_type": "COLUMN_MASK",
                        "on_securable": "c.s.t", "function_name": "c.sec.m",
                        "match_columns": [], "to_principals": [],
                        "except_principals": []}},
    ]  # 3 objects total (incl. the ABAC policy)
    export_results = [
        {"full_name": "c", "status": "SUCCESS"},
        {"full_name": "c.s.t", "status": "SUCCESS"},
        {"full_name": "c.s.t#policy:p", "status": "SUCCESS"},
    ]
    import_results = [
        {"object_type": "CATALOG", "target_full_name": "c", "full_name": "c",
         "status": "SUCCESS", "action": "CREATE_OR_SKIP"},
        {"object_type": "TABLE", "target_full_name": "c.s.t", "full_name": "c.s.t",
         "status": "SUCCESS", "action": "CREATE_OR_SKIP"},
        # the ABAC policy — an OBJECT create (CREATE POLICY), counts per-object.
        {"object_type": "ABAC_POLICY", "target_full_name": "c.s.t#policy:p",
         "full_name": "c.s.t#policy:p", "status": "SUCCESS", "action": "CREATE_POLICY",
         "policies_path": "/abac/x.sql"},
        # a governed-tag op on the catalog — supplementary, NOT a per-object row.
        {"object_type": "CATALOG", "target_full_name": "c", "full_name": "c",
         "status": "SUCCESS", "action": "APPLY_TAGS",
         "policies_path": "/tags/CATALOG_c.sql"},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), stage="IMPORT", run_id="r1",
                 export_results=export_results, import_results=import_results)
    summary = [tuple(r) for r in load_workbook(out)["Summary"].iter_rows(values_only=True)]

    def _count(label, key):
        # find the block header row, then read the key within that block
        seen = False
        for row in summary:
            if row and row[0] == label:
                seen = True
                continue
            if seen and row and row[0] == key:
                return row[1]
        return None

    # export objects == import per-object == 3 (all incl. the ABAC policy). The
    # governed-tag ALTER on the catalog is folded into the catalog's object status
    # (Created here), NOT a separate tally.
    assert _count("export_status", "SUCCESS") == 3
    assert _count("Outcome roll-up", "Created") == 3
    flat = [str(c) for row in summary for c in row if c is not None]
    assert "governed-tag operations" not in flat and "governance operations" not in flat


def test_policy_matched_columns_derives_from_tags_within_scope(tmp_path):
    """The derived sheet resolves each policy's tag rule against captured column
    tags, scoped to the securable the policy is attached ON."""
    objects = [
        # Schema-scoped column-mask policy matching pii=SSN.
        {"object_type": "ABAC_POLICY", "full_name": "c.hr#policy:mask_ssn",
         "definition": {"policy_name": "mask_ssn", "policy_type": "COLUMN_MASK",
                        "on_securable_type": "SCHEMA", "on_securable": "c.hr",
                        "function_name": "c.sec.mask", "to_principals": [],
                        "except_principals": [],
                        "match_columns": ["has_tag_value('pii', 'SSN') AS x"]}},
        # In-scope table with a matching column tag and a non-matching one.
        {"object_type": "TABLE", "full_name": "c.hr.employees", "owner": "me",
         "tags": {}, "grants": [],
         "definition": {"column_tags": {"ssn": {"pii": "SSN"},
                                        "email": {"pii": "EMAIL"}}}},
        # Out-of-scope table (different schema) — must NOT match.
        {"object_type": "TABLE", "full_name": "c.finance.ledger", "owner": "me",
         "tags": {}, "grants": [],
         "definition": {"column_tags": {"acct": {"pii": "SSN"}}}},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), run_id="r1")
    from openpyxl import load_workbook
    ws = load_workbook(out)["Policy Matched Columns"]
    rows = list(ws.iter_rows(values_only=True))[1:]  # drop header
    # Exactly one match: c.hr.employees.ssn (pii=SSN), in-scope.
    assert len(rows) == 1
    r = rows[0]
    assert r[0] == "mask_ssn" and r[5] == "c.hr.employees"
    assert r[6] == "ssn" and r[7] == "pii" and r[8] == "SSN"


def test_storage_sheets_status_skipped_vs_created(tmp_path):
    """Storage creds / external locations get their own sheets, and the status
    says SKIPPED when the utility did not create them (create toggle off) vs the
    real import outcome when it did."""
    objects = [
        {"object_type": "STORAGE_CREDENTIAL", "full_name": "cred_a", "owner": "me",
         "credential_type": "AZURE_MANAGED_IDENTITY",
         "access_connector_id": "/subscriptions/x/ac/conn",
         "definition": {"read_only": False}, "grants": []},
        {"object_type": "EXTERNAL_LOCATION", "full_name": "loc_a", "owner": "me",
         "definition": {"url": "abfss://c@acct/p", "credential_name": "cred_a",
                        "read_only": False}, "grants": []},
    ]
    # cred was created by the utility; the external location's create was toggled off.
    results = [
        {"object_type": "STORAGE_CREDENTIAL", "full_name": "cred_a",
         "target_full_name": "cred_a", "status": "SUCCESS", "action": "CREATE"},
        {"object_type": "EXTERNAL_LOCATION", "full_name": "loc_a",
         "target_full_name": "loc_a", "status": "SUCCESS",
         "action": "SKIP_CREATE_DISABLED"},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), import_results=results, run_id="r1")
    from openpyxl import load_workbook
    wb = load_workbook(out)

    cred = list(wb["Storage Credentials"].iter_rows(values_only=True))
    assert cred[0][-1] == "import_status"
    assert cred[1][0] == "cred_a"
    assert "/subscriptions/x/ac/conn" in cred[1]        # access connector captured
    assert cred[1][-1] == "Created"

    loc = list(wb["External Locations"].iter_rows(values_only=True))
    assert loc[1][0] == "loc_a"
    assert "abfss://c@acct/p" in loc[1]                  # url captured
    # create toggle off (BYO) → its own Skipped variant (B3), distinct from adopted/unchanged.
    assert "create disabled" in loc[1][-1]


def test_tags_and_grants_status_reflect_governance_not_create_skip(tmp_path):
    """Plan P2-C: on a SKIP_CREATE_DISABLED catalog (existing-catalog mode) the
    Tags status is the APPLY_TAGS op result and the Grants status is APPLIED —
    never 'not created by utility' (which is the catalog's create-skip label). A
    failed tag on a table reads FAILED."""
    objects = [
        {"object_type": "CATALOG", "full_name": "c", "owner": "me",
         "tags": {"cls": "INTERNAL"},
         "grants": [{"principal": "account users", "principal_type": "GROUP",
                     "privileges": ["USE_CATALOG"]}]},
        {"object_type": "TABLE", "full_name": "c.s.bad", "owner": "me",
         "tags": {"missing": "x"}, "grants": []},
    ]
    import_results = [
        # Catalog create was skipped (existing-catalog mode), grants/tags still ran.
        {"object_type": "CATALOG", "target_full_name": "c", "full_name": "c",
         "status": "SUCCESS", "action": "SKIP_CREATE_DISABLED"},
        {"object_type": "TABLE", "target_full_name": "c.s.bad",
         "full_name": "c.s.bad", "status": "SUCCESS", "action": "CREATE_OR_SKIP"},
        # Governance ops: catalog tag applied; the table's tag failed.
        {"object_type": "CATALOG", "target_full_name": "c", "full_name": "c",
         "status": "SUCCESS", "action": "APPLY_TAGS",
         "policies_path": "/tags/CATALOG_c.sql"},
        {"object_type": "TABLE", "target_full_name": "c.s.bad",
         "full_name": "c.s.bad", "status": "FAILURE", "action": "MANUAL",
         "error_code": "PROTECTION_FAILED", "policies_path": "/tags/TABLE_c__s__bad.sql",
         "message": "unknown tag"},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), import_results=import_results, run_id="r1")
    from openpyxl import load_workbook
    wb = load_workbook(out)

    # Tags: the catalog tag reads applied (APPLY_TAGS), NOT "not created by utility".
    tag_rows = list(wb["Tags Applied"].iter_rows(values_only=True))
    cat_tag = next(r for r in tag_rows[1:] if r[0] == "c")
    assert "not created by utility" not in str(cat_tag[-1])
    assert "APPLIED" in str(cat_tag[-1])
    bad_tag = next(r for r in tag_rows[1:] if r[0] == "c.s.bad")
    assert "FAILED" in str(bad_tag[-1])

    # Grants: the catalog grant reads APPLIED (create skipped, grant ran), never
    # "not created by utility".
    grant_rows = list(wb["Grants"].iter_rows(values_only=True))
    cat_grant = next(r for r in grant_rows[1:] if r[0] == "c")
    assert cat_grant[-1] == "APPLIED"


def test_stage_status_columns(tmp_path):
    """Each stage's report is the base for the next: inventory has no status
    column, export adds export_status, import carries export_status forward and
    adds import_status."""
    from openpyxl import load_workbook

    objects = [{"object_type": "TABLE", "full_name": "c.s.t", "owner": "me",
                "tags": {}, "grants": [], "definition": {}}]

    # INVENTORY — no status columns at all.
    inv = tmp_path / "inventory.xlsx"
    build_report(objects, str(inv), stage="INVENTORY")
    hdr = list(load_workbook(inv)["Tables"].iter_rows(values_only=True))[0]
    assert "export_status" not in hdr and "import_status" not in hdr

    # EXPORT — export_status only, populated from the export results.
    exp = tmp_path / "export.xlsx"
    build_report(objects, str(exp), stage="EXPORT",
                 export_results=[{"full_name": "c.s.t", "status": "SUCCESS"}])
    rows = list(load_workbook(exp)["Tables"].iter_rows(values_only=True))
    assert rows[0][-1] == "export_status" and "import_status" not in rows[0]
    assert rows[1][-1] == "EXPORTED"

    # IMPORT — both columns, export carried forward + this stage's import status.
    imp = tmp_path / "import.xlsx"
    build_report(
        objects, str(imp), stage="IMPORT",
        export_results=[{"full_name": "c.s.t", "status": "SUCCESS"}],
        import_results=[{"target_full_name": "c.s.t", "status": "SUCCESS",
                         "action": "CREATE"}],
    )
    rows = list(load_workbook(imp)["Tables"].iter_rows(values_only=True))
    assert rows[0][-2] == "export_status" and rows[0][-1] == "import_status"
    assert rows[1][-2] == "EXPORTED" and rows[1][-1] == "Created"


def test_grants_sheet_has_level_column(tmp_path):
    """The Grants sheet records the securable level of each explicit grant so
    catalog/schema/table/view/function/volume grants are distinguishable."""
    objects = [
        {"object_type": "CATALOG", "full_name": "c", "owner": "me", "tags": {},
         "grants": [{"principal": "u@x.com", "principal_type": "USER",
                     "privileges": ["USE_CATALOG"]}]},
        {"object_type": "TABLE", "full_name": "c.s.t", "owner": "me", "tags": {},
         "grants": [{"principal": "u@x.com", "principal_type": "USER",
                     "privileges": ["SELECT"]}]},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), stage="INVENTORY")
    from openpyxl import load_workbook
    rows = list(load_workbook(out)["Grants"].iter_rows(values_only=True))
    assert rows[0] == ("object", "level", "principal", "type", "privileges")
    levels = {r[0]: r[1] for r in rows[1:]}
    assert levels["c"] == "CATALOG" and levels["c.s.t"] == "TABLE"


def test_masks_sheet_object_header_and_dynamic_view_identity(tmp_path):
    """Classic masks/row filters are table-only, so their sheet's first column is
    'object' (can be an MV/streaming table, never a view). Dynamic views instead
    surface their in-definition protection via an identity_aware column."""
    objects = [
        {"object_type": "TABLE", "full_name": "c.s.t", "owner": "me", "tags": {},
         "grants": [], "definition": {
             "column_masks": [{"column_name": "ssn", "function_name": "c.sec.m",
                               "using_column_names": []}]}},
        {"object_type": "DYNAMIC_VIEW", "full_name": "c.s.dv", "owner": "me",
         "tags": {}, "grants": [], "definition": {
             "view_definition": "SELECT id, CASE WHEN is_account_group_member('hr') "
                                "THEN ssn ELSE '***' END AS ssn FROM c.s.t "
                                "WHERE dept = current_user()"}},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), stage="INVENTORY")
    from openpyxl import load_workbook
    wb = load_workbook(out)

    cm = list(wb["Column Masks & Row Filters"].iter_rows(values_only=True))
    assert cm[0][0] == "object"

    dv = list(wb["Dynamic Views"].iter_rows(values_only=True))
    idx = dv[0].index("identity_aware")
    markers = dv[1][idx]
    assert "is_account_group_member" in markers and "current_user" in markers


def test_view_functions_applied_from_dependencies(tmp_path):
    """A view that masks a column by calling a UDF inline is detected via UC's
    view_dependencies (no SQL parsing) and surfaced in functions_applied."""
    objects = [
        {"object_type": "VIEW", "full_name": "c.s.masked", "owner": "me",
         "tags": {}, "grants": [], "definition": {
             "view_definition": "SELECT id, c.sec.mask_email(email) AS email FROM c.s.t",
             "view_dependencies": {"dependencies": [
                 {"function": {"function_full_name": "c.sec.mask_email"}},
                 {"table": {"table_full_name": "c.s.t"}},
             ]}}},
    ]
    out = tmp_path / "r.xlsx"
    build_report(objects, str(out), stage="INVENTORY")
    from openpyxl import load_workbook
    rows = list(load_workbook(out)["Views"].iter_rows(values_only=True))
    idx = rows[0].index("functions_applied")
    assert rows[1][idx] == "c.sec.mask_email"


def test_build_report_saves_to_buffer_not_path(tmp_path, monkeypatch):
    """UC Volumes FUSE mounts reject the seeks openpyxl needs to write a ZIP to a
    path directly, so the workbook must be built in an in-memory buffer and then
    flushed to the Volume with a single sequential write. Pin that contract:
    Workbook.save must be handed a file-like buffer, never a str/Path target."""
    import io
    from pathlib import Path
    from openpyxl import Workbook

    save_targets = []
    real_save = Workbook.save

    def _spy_save(self, target):  # noqa: ANN001
        save_targets.append(target)
        return real_save(self, target)

    monkeypatch.setattr(Workbook, "save", _spy_save, raising=True)

    out = tmp_path / "reports" / "import.xlsx"
    objects = [{"object_type": "CATALOG", "full_name": "c", "owner": "me",
                "tags": {}, "grants": []}]
    build_report(objects, str(out), run_id="r1")

    assert save_targets, "Workbook.save was never called"
    for target in save_targets:
        assert not isinstance(target, (str, Path)), (
            "workbook saved to a filesystem path (fails on Volume FUSE); "
            "it must be saved to an in-memory buffer then written sequentially"
        )
        assert isinstance(target, io.BytesIO)

    assert out.exists()
    from openpyxl import load_workbook
    assert "Catalogs" in load_workbook(out).sheetnames
