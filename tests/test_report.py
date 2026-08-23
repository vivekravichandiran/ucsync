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
    assert "Objects" in load_workbook(out).sheetnames
