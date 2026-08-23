"""Migration report (Excel) generation test."""
from __future__ import annotations

from uc_sync.report import build_report


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
    assert set(wb.sheetnames) == {
        "Summary", "Objects", "Tags", "Column Masks & Row Filters",
        "ABAC Policies", "Grants",
    }
    # ABAC sheet carries the policy with its EXCEPT.
    abac_rows = list(wb["ABAC Policies"].iter_rows(values_only=True))
    assert any("svc@x.com" in str(r) for r in abac_rows)
    # Tags sheet has the column tag.
    tag_rows = list(wb["Tags"].iter_rows(values_only=True))
    assert any("SSN" in str(r) for r in tag_rows)
