"""Clean, operator-facing migration report (Excel) for the governance model.

One workbook per Import run: a spine (one row per securable with its import
status) plus governance-detail sheets so a reviewer can read, per line, exactly
which mask/policy/tag/grant is applied where (design §9). Kept deliberately simple.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Iterable, Optional


def _import_status_by_name(import_results: Iterable[dict[str, Any]]) -> dict[str, str]:
    status: dict[str, str] = {}
    for r in import_results or []:
        name = str(r.get("target_full_name") or r.get("full_name") or "")
        if name:
            # Prefer a non-SUCCESS status if any phase for the object flagged one.
            prev = status.get(name)
            cur = str(r.get("status") or "")
            if prev in (None, "SUCCESS", "SKIP_EXISTING", "SKIP_CREATE_DISABLED"):
                status[name] = cur
    return status


def build_report(
    objects: list[dict[str, Any]],
    out_path: str,
    *,
    import_results: Optional[list[dict[str, Any]]] = None,
    run_id: str = "",
) -> str:
    """Write the migration workbook to ``out_path`` (.xlsx). Returns the path."""

    from openpyxl import Workbook
    from openpyxl.styles import Font

    status_by = _import_status_by_name(import_results or [])
    wb = Workbook()

    def _sheet(title: str, headers: list[str]):
        ws = wb.create_sheet(title)
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        return ws

    # Summary
    ws = wb.active
    ws.title = "Summary"
    ws.append(["UC Governance Migration report"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append(["run_id", run_id])
    ws.append(["objects", len(objects)])
    counts: dict[str, int] = {}
    for o in objects:
        counts[o["object_type"]] = counts.get(o["object_type"], 0) + 1
    ws.append([])
    ws.append(["object_type", "count"])
    for k in sorted(counts):
        ws.append([k, counts[k]])
    if import_results:
        st: dict[str, int] = {}
        for r in import_results:
            st[r.get("status", "")] = st.get(r.get("status", ""), 0) + 1
        ws.append([])
        ws.append(["import_status", "count"])
        for k in sorted(st):
            ws.append([k, st[k]])

    # Spine: one row per securable
    spine = _sheet("Objects", ["object", "type", "import_status", "note"])
    for o in sorted(objects, key=lambda x: (x["object_type"], x["full_name"])):
        if o["object_type"] == "ABAC_POLICY":
            continue
        spine.append([
            o["full_name"], o["object_type"],
            status_by.get(o["full_name"], ""), o.get("owner") or "",
        ])

    # Tags (object + column grain)
    tags = _sheet("Tags", ["object", "level", "column", "key", "value"])
    for o in objects:
        for k, v in (o.get("tags") or {}).items():
            tags.append([o["full_name"], o["object_type"], "", k, v])
        for col, ctags in ((o.get("definition") or {}).get("column_tags") or {}).items():
            for k, v in ctags.items():
                tags.append([o["full_name"], "COLUMN", col, k, v])

    # Column masks & row filters (classic)
    cm = _sheet(
        "Column Masks & Row Filters",
        ["table", "kind", "column", "function", "using/on cols"],
    )
    for o in objects:
        d = o.get("definition") or {}
        for mask in d.get("column_masks") or []:
            cm.append([
                o["full_name"], "CLASSIC MASK", mask.get("column_name"),
                mask.get("function_name"),
                ", ".join(mask.get("using_column_names") or []),
            ])
        rf = d.get("row_filter")
        if isinstance(rf, dict) and rf.get("function_name"):
            cm.append([
                o["full_name"], "CLASSIC ROW FILTER", "",
                rf.get("function_name"),
                ", ".join(rf.get("input_column_names") or []),
            ])

    # ABAC policies
    abac = _sheet(
        "ABAC Policies",
        ["policy", "type", "on_securable", "function", "match_columns",
         "to", "except"],
    )
    for o in objects:
        if o["object_type"] != "ABAC_POLICY":
            continue
        d = o.get("definition") or {}
        abac.append([
            d.get("policy_name"), d.get("policy_type"), d.get("on_securable"),
            d.get("function_name"), "; ".join(d.get("match_columns") or []),
            ", ".join(d.get("to_principals") or []),
            ", ".join(d.get("except_principals") or []),
        ])

    # Grants
    grants = _sheet("Grants", ["object", "principal", "type", "privileges"])
    for o in objects:
        for g in o.get("grants") or []:
            grants.append([
                o["full_name"], g.get("principal"),
                g.get("principal_type"),
                ", ".join(g.get("privileges") or []),
            ])

    # UC Volumes are mounted via FUSE, which only supports sequential writes.
    # openpyxl.save() writes a ZIP archive and needs a seekable target, so it
    # fails when handed a /Volumes/... path directly. Build the workbook in an
    # in-memory buffer, then flush the finished bytes in one sequential write —
    # which the Volume FUSE mount does support (same pattern the export/bundle
    # writers use to land files on the Volume).
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    wb.save(buf)
    Path(out_path).write_bytes(buf.getvalue())
    return out_path


def build_report_from_file(inventory_json: str, out_path: str, **kw) -> str:
    objects = json.loads(Path(inventory_json).read_text(encoding="utf-8"))
    return build_report(objects, out_path, **kw)
