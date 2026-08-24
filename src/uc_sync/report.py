"""Clean, operator-facing migration report (Excel) for the governance model.

One workbook per Import run: a spine (one row per securable with its import
status) plus governance-detail sheets so a reviewer can read, per line, exactly
which mask/policy/tag/grant is applied where (design §9). Kept deliberately simple.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

# ABAC MATCH COLUMNS predicates. ``has_tag_value('k','v')`` matches a column
# tagged k=v; ``has_tag('k')`` matches any column carrying key k. The ``has_tag(``
# pattern deliberately requires a ``(`` right after, so it never matches inside
# ``has_tag_value(``.
_HAS_TAG_VALUE = re.compile(r"has_tag_value\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)", re.I)
_HAS_TAG = re.compile(r"has_tag\(\s*'([^']*)'\s*\)", re.I)


def _parse_match_conditions(match_columns: Iterable[Any]) -> list[tuple[str, str, Optional[str]]]:
    """Parse MATCH COLUMNS expressions into ``(kind, tag_key, tag_value)`` tuples.

    ``kind`` is ``"value"`` (key + exact value) or ``"key"`` (key, any value).
    """
    conds: list[tuple[str, str, Optional[str]]] = []
    for expr in match_columns or []:
        s = str(expr)
        for key, val in _HAS_TAG_VALUE.findall(s):
            conds.append(("value", key, val))
        for key in _HAS_TAG.findall(s):
            conds.append(("key", key, None))
    return conds


def _column_condition_match(
    col_tags: dict[str, Any],
    conditions: list[tuple[str, str, Optional[str]]],
) -> Optional[tuple[str, Any]]:
    """Return the ``(tag_key, actual_value)`` of the first satisfied condition."""
    for kind, key, val in conditions:
        if key in col_tags:
            actual = col_tags[key]
            if kind == "key" or str(actual) == str(val):
                return key, actual
    return None


def _table_in_policy_scope(table_full_name: str, on_type: str, on_securable: str) -> bool:
    """Is ``table_full_name`` under the securable the policy is attached ON?"""
    if not on_securable:
        return False
    parts = table_full_name.split(".")
    on_type = on_type.upper()
    if on_type == "CATALOG":
        return parts[0] == on_securable
    if on_type == "SCHEMA":
        return ".".join(parts[:2]) == on_securable
    if on_type == "TABLE":
        return table_full_name == on_securable
    return False


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

    # Policy → matched columns (DERIVED): resolve each ABAC policy's tag rule
    # against the captured column tags, within the securable it is attached ON.
    # UC does not store a policy→column mapping — the effective set is emergent
    # from (policy ON securable) + (MATCH COLUMNS tag rule) + (column tag
    # assignments) — so this sheet reconstructs it as an auditor aid. Matching is
    # on directly-assigned COLUMN tags (has_tag/has_tag_value evaluate column
    # tags); it does not model tag inheritance from parent securables.
    matched = _sheet(
        "Policy Matched Columns",
        ["policy", "type", "on_securable", "function", "match_rule",
         "matched_table", "matched_column", "tag_key", "tag_value"],
    )
    col_tag_index: dict[str, dict[str, Any]] = {}
    for o in objects:
        ctags = (o.get("definition") or {}).get("column_tags") or {}
        if ctags:
            col_tag_index[o["full_name"]] = ctags
    for o in objects:
        if o["object_type"] != "ABAC_POLICY":
            continue
        d = o.get("definition") or {}
        on_type = str(d.get("on_securable_type") or "")
        on_securable = str(d.get("on_securable") or "")
        conditions = _parse_match_conditions(d.get("match_columns") or [])
        rule = "; ".join(d.get("match_columns") or [])
        base = [d.get("policy_name"), d.get("policy_type"), on_securable,
                d.get("function_name"), rule]
        hits = 0
        for table_fn, ctags in col_tag_index.items():
            if not _table_in_policy_scope(table_fn, on_type, on_securable):
                continue
            for col, tags in ctags.items():
                hit = _column_condition_match(tags, conditions)
                if hit:
                    key, value = hit
                    matched.append(base + [table_fn, col, key, value])
                    hits += 1
        if hits == 0:
            note = (
                "(no captured tagged columns match)" if conditions
                else "(match rule not tag-based / unparsed)"
            )
            matched.append(base + ["", "", "", note])

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
