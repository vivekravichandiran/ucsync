"""Clean, operator-facing migration report (Excel) for the governance model.

One workbook per stage: a sheet per object type (storage credentials, external
locations, catalogs, schemas, volumes, functions, tables, views, …) carrying that
type's captured detail plus an import-status column, then governance-detail sheets
(tags, classic masks/row filters, ABAC policies, derived policy→column matches,
grants) so a reviewer can read, per line, exactly which mask/policy/tag/grant is
applied where (design §9). Storage credentials and external locations show an
explicit SKIPPED status when the utility did not create them (create toggle off).
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from uc_sync import vocab

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


def _import_index(
    import_results: Iterable[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Map securable name → its **creation** import result (status/action/message).

    The engine appends create/structure results before governance phases, so the
    FIRST result seen per name is the creation one — which is what the per-object
    sheets report. Indexed by both target and source name so it resolves whether
    the report reads the migrated (target) or source inventory.
    """
    idx: dict[str, dict[str, str]] = {}
    for r in import_results or []:
        entry = {
            "status": str(r.get("status") or ""),
            "action": str(r.get("action") or ""),
            "message": str(r.get("message") or ""),
            # delta_action is required by _wsmig_status_key to tell an "Updated"
            # (a change applied to a pre-existing object) from an "Adopted" — bug #19.
            "delta_action": str(r.get("delta_action") or ""),
        }
        for key in (r.get("target_full_name"), r.get("full_name")):
            key = str(key or "")
            if key and key not in idx:
                idx[key] = entry
    return idx


def _tag_op_index(
    import_results: Iterable[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Map securable name → its ``APPLY_TAGS`` governance-op result.

    The Tags sheet's status must reflect whether the tag was actually applied, NOT
    the securable's create outcome: in existing-catalog mode a catalog/schema create
    is ``SKIP_CREATE_DISABLED`` while its tags are still applied, and keying off the
    create result wrongly rendered "not created by utility" (plan P2-C). Tag ops are
    the governance results that carry a ``policies_path`` and are not ABAC
    (``ABAC_POLICY``) — there is one tag file per securable.
    """
    idx: dict[str, dict[str, str]] = {}
    for r in import_results or []:
        if not r.get("policies_path"):
            continue
        if str(r.get("object_type")) == "ABAC_POLICY":
            continue
        # A classic mask / row filter op (Phase 1c) also carries a policies_path but is
        # NOT a governed-tag op — exclude it so it never renders as a table's tag status.
        if str(r.get("action")) == "APPLY_POLICY":
            continue
        entry = {
            "status": str(r.get("status") or ""),
            "action": str(r.get("action") or ""),
            "message": str(r.get("message") or ""),
        }
        for key in (r.get("target_full_name"), r.get("full_name")):
            key = str(key or "")
            if key and key not in idx:
                idx[key] = entry
    return idx


def _is_tag_op_result(r: dict[str, Any]) -> bool:
    """A governed-tag ``SET TAGS`` op — the only import result that is NOT an object.

    A tag is an *attribute* on a securable that is already counted as an object
    (its catalog / schema / table), so tallying it as an object too would double-
    count — it belongs in the supplementary governed-tag block instead.

    An **ABAC policy**, by contrast, is a distinct securable the utility creates
    (its own ``CREATE POLICY``), so it counts as an OBJECT on import exactly as it
    does on export — that keeps the export-object and import-object totals at
    parity. Tag ops set ``policies_path`` and are not ``ABAC_POLICY``; ABAC results
    set ``policies_path`` too but carry object_type ``ABAC_POLICY`` (excluded here).
    """
    return bool(r.get("policies_path")) and str(r.get("object_type")) != "ABAC_POLICY"


def _export_index(
    export_results: Iterable[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Map securable name → its export (stage 02) result.

    Keyed by both target and source name so it resolves against the migrated
    inventory the export/import reports read (names are never remapped, so the
    two coincide, but resolving either is robust).
    """
    idx: dict[str, dict[str, str]] = {}
    for r in export_results or []:
        entry = {
            "status": str(r.get("status") or ""),
            "error_code": str(r.get("error_code") or ""),
            "error_message": str(r.get("error_message") or ""),
        }
        for key in (r.get("target_full_name"), r.get("full_name")):
            key = str(key or "")
            if key and key not in idx:
                idx[key] = entry
    return idx


def _render_export_status(entry: Optional[dict[str, str]]) -> str:
    """Human-readable export outcome for an object row (stage 02)."""
    if not entry:
        return ""  # no export in this stage (inventory report)
    status = entry["status"]
    msg = entry.get("error_message") or ""
    tail = f": {msg[:200]}" if msg else ""
    if status == "ERROR":
        return f"FAILED{tail}"
    if status == "SUCCESS_WITH_WARNINGS":
        return f"EXPORTED (with warnings{tail})"
    if status == "DRY_RUN":
        return "DRY RUN (validated, not exported)"
    if status == "SUCCESS":
        return "EXPORTED"
    return status or ""


def _render_import_status(entry: Optional[dict[str, str]]) -> str:
    """Human-readable import outcome for an object row, using the ONE wsmig status
    vocabulary (bug #19) so the per-type sheets match the Summary roll-up exactly —
    Created / Created (warning) / Updated / Adopted (pre-existing) / Skipped (unchanged)
    / Deferred (not selected) / Skipped (no target object) / Manual step / FAILED — and
    a reader never sees two different labels ("SUCCESS (UNCHANGED)" vs "ALREADY EXISTS
    (skipped)") for the same kind of outcome. Failures and manual steps keep their
    message tail; a dry run reads as a validation.
    """
    if not entry:
        return ""  # no import in this stage (inventory/export reports)
    status, action, msg = entry["status"], entry["action"], entry["message"]
    tail = f": {msg[:200]}" if msg else ""
    # A CHANGED table skipped because its only diff is a column drop / type change
    # (A3/A4) reads a Skipped variant WITH the reason, not a blanket "Updated".
    if str(entry.get("delta_action")) == "CHANGED_SKIPPED":
        return f"Skipped — {msg}" if msg else _STATUS_STYLE["skipped"][0]
    key = _wsmig_status_key(entry)
    if key == "failed":
        return f"FAILED{tail}"
    if key == "manual":
        return f"MANUAL ACTION REQUIRED{tail}"
    # Report-only objects read as "Skipped (no target object)" regardless of their
    # PENDING status — checked before the dry-run branch below so they aren't mistaken
    # for a dry run.
    if key == "skipped_no_object":
        return _STATUS_STYLE["skipped_no_object"][0]
    if status == "PENDING" or action == "DRY_RUN":
        return "DRY RUN (validated, not applied)"
    return _STATUS_STYLE.get(key, ("", ""))[0] or (status or "")


# --- Per-object-type sheet column specs -------------------------------------
# Each column is (header, extractor). A trailing "import_status" column is added
# by the renderer for every type.

def _cell(o: dict[str, Any], *keys: str) -> Any:
    """First non-empty value among top-level then ``definition`` for each key."""
    d = o.get("definition") or {}
    for k in keys:
        v = o.get(k)
        if v not in (None, "", [], {}):
            return v
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return ""


def _truncate(v: Any, n: int = 300) -> str:
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "…"


def _col_count(o: dict[str, Any]) -> Any:
    return len((o.get("definition") or {}).get("columns") or []) or ""


def _has_mask(o: dict[str, Any]) -> str:
    return "yes" if (o.get("definition") or {}).get("column_masks") else ""


def _has_row_filter(o: dict[str, Any]) -> str:
    rf = (o.get("definition") or {}).get("row_filter")
    return "yes" if isinstance(rf, dict) and rf.get("function_name") else ""


_VOLUME_COLS = [
    ("volume", lambda o: o["full_name"]),
    ("volume_type", lambda o: _cell(o, "volume_type")),
    ("storage_location", lambda o: _cell(o, "storage_location")),
    ("comment", lambda o: _cell(o, "comment")),
    ("owner", lambda o: o.get("owner") or ""),
]
_TABLE_COLS = [
    ("table", lambda o: o["full_name"]),
    ("table_type", lambda o: _cell(o, "table_type")),
    ("format", lambda o: _cell(o, "data_source_format")),
    ("storage_location", lambda o: _cell(o, "storage_location")),
    ("columns", _col_count),
    ("column_mask", _has_mask),
    ("row_filter", _has_row_filter),
    ("comment", lambda o: _cell(o, "comment")),
    ("owner", lambda o: o.get("owner") or ""),
]
# Identity functions that make a view "dynamic": its SELECT/WHERE masks columns
# or filters rows based on the querying principal. UC has no classic ALTER-applied
# masks/row filters on views (those are table-only) — a view expresses the same
# protection inside its own definition, so surface which markers are present.
_IDENTITY_MARKERS = (
    "current_user(",
    "session_user(",
    "is_member(",
    "is_account_group_member(",
)


def _view_identity(o: dict[str, Any]) -> str:
    d = o.get("definition") or {}
    text = " ".join(
        str(d.get(k) or "") for k in ("view_definition", "view_original_text")
    ).lower()
    return ", ".join(m.rstrip("(") for m in _IDENTITY_MARKERS if m in text)


def _view_functions(o: dict[str, Any]) -> str:
    """Functions the view applies, from UC's authoritative ``view_dependencies``.

    A view can protect a column by calling a UDF inline
    (``SELECT sec.mask_email(email) AS email``). UC records that as a function
    dependency, so we surface it without parsing SQL — this is how a
    function-masked view (as opposed to an identity-based dynamic view) shows up
    in the report.
    """
    d = o.get("definition") or {}
    vd = d.get("view_dependencies") or {}
    deps = vd.get("dependencies") if isinstance(vd, dict) else vd
    names: list[str] = []
    for item in deps or []:
        if not isinstance(item, dict):
            continue
        fn = item.get("function")
        if isinstance(fn, dict):
            name = fn.get("function_full_name") or fn.get("name")
            if name:
                names.append(str(name))
    return ", ".join(sorted(set(names)))


_VIEW_COLS = [
    ("view", lambda o: o["full_name"]),
    ("columns", _col_count),
    ("identity_aware", _view_identity),
    ("functions_applied", _view_functions),
    ("definition", lambda o: _truncate(_cell(o, "view_definition", "view_original_text"))),
    ("comment", lambda o: _cell(o, "comment")),
    ("owner", lambda o: o.get("owner") or ""),
]

# Ordered: creation-dependency order (creds/locations first, governance-bearing
# securables after). Titles are the sheet names.
_TYPE_SHEETS: list[tuple[str, str, list]] = [
    ("STORAGE_CREDENTIAL", "Storage Credentials", [
        ("credential", lambda o: o["full_name"]),
        ("credential_type", lambda o: _cell(o, "credential_type")),
        ("purpose", lambda o: _cell(o, "credential_purpose")),
        ("access_connector_id", lambda o: _cell(o, "access_connector_id")),
        ("managed_identity_id", lambda o: _cell(o, "user_assigned_managed_identity_id")),
        ("read_only", lambda o: _cell(o, "read_only")),
        ("comment", lambda o: _cell(o, "comment")),
        ("owner", lambda o: o.get("owner") or ""),
    ]),
    ("EXTERNAL_LOCATION", "External Locations", [
        ("external_location", lambda o: o["full_name"]),
        ("url", lambda o: _cell(o, "url", "storage_location")),
        ("credential_name", lambda o: _cell(o, "credential_name", "storage_credential_name")),
        ("read_only", lambda o: _cell(o, "read_only")),
        ("comment", lambda o: _cell(o, "comment")),
        ("owner", lambda o: o.get("owner") or ""),
    ]),
    ("CATALOG", "Catalogs", [
        ("catalog", lambda o: o["full_name"]),
        ("catalog_type", lambda o: _cell(o, "catalog_type")),
        ("isolation_mode", lambda o: _cell(o, "isolation_mode")),
        ("storage_root", lambda o: _cell(o, "storage_root")),
        ("comment", lambda o: _cell(o, "comment")),
        ("owner", lambda o: o.get("owner") or ""),
    ]),
    ("SCHEMA", "Schemas", [
        ("schema", lambda o: o["full_name"]),
        ("comment", lambda o: _cell(o, "comment")),
        ("owner", lambda o: o.get("owner") or ""),
    ]),
    ("VOLUME", "Volumes", _VOLUME_COLS),
    ("EXTERNAL_VOLUME", "External Volumes", _VOLUME_COLS),
    ("FUNCTION", "Functions", [
        ("function", lambda o: o["full_name"]),
        ("returns", lambda o: _cell(o, "data_type", "full_data_type")),
        ("deterministic", lambda o: _cell(o, "is_deterministic")),
        ("routine_body", lambda o: _cell(o, "routine_body")),
        ("comment", lambda o: _cell(o, "comment")),
        ("owner", lambda o: o.get("owner") or ""),
    ]),
    ("TABLE", "Tables", _TABLE_COLS),
    ("EXTERNAL_TABLE", "External Tables", _TABLE_COLS),
    ("STREAMING_TABLE", "Streaming Tables", _TABLE_COLS),
    ("VIEW", "Views", _VIEW_COLS),
    ("DYNAMIC_VIEW", "Dynamic Views", _VIEW_COLS),
    ("MATERIALIZED_VIEW", "Materialized Views", _VIEW_COLS),
    ("METRIC_VIEW", "Metric Views", _VIEW_COLS),
]

# Tier-A AI-asset types the utility inventories but never migrates (report-only,
# task 4); each gets a lean inventory-only sheet flagged in_scope_for_migration=false.
# Tier-B / Tier-C metastore-scoped objects (connections, service credentials, foreign
# catalogs, shares, recipients, providers, clean rooms) are OUT of both migration AND
# inventory — the catalog-scoped run principal cannot even list them — so they get NO
# sheet (an empty one would imply a coverage we don't provide). See Out-of-scope §C.
_INVENTORY_ONLY = [
    ("MODEL", "Models"),
    ("ONLINE_TABLE", "Online Tables"),
    ("VECTOR_INDEX", "Vector Search Indexes"),
    ("MONITOR", "Monitors"),
    ("UC_SECRET", "UC Secrets"),
    # FOREIGN objects that only look like tables — reported, never migrated (bug #6).
    ("LAKEBASE_TABLE", "Lakebase Tables"),
    # Pipeline-managed tables (event logs / outputs) — reported, never migrated (#8).
    ("PIPELINE_TABLE", "Pipeline Tables"),
    # Monitor-owned metric tables (profile/drift) — regenerated when the monitor is
    # recreated, so reported, never migrated as empty copies.
    ("MONITOR_METRIC_TABLE", "Monitor Metric Tables"),
]


# --- FEAT-5: workspace-migration (wsmig) report vocabulary --------------------
# The status vocabulary is now a single source of truth in uc_sync.vocab, shared by
# BOTH this report and uc_sync_state.last_action (the "constant naming + vocab"
# unification) — so a reader never sees one word in the workbook and a different one
# in the state table for the same outcome. Aliased here for readability.
_STATUS_STYLE = vocab.STATUS_STYLE
_SUMMARY_ORDER = vocab.SUMMARY_ORDER
_SUCCESS_STATUSES = vocab.SUCCESS_STATUSES

# wsmig palette / fonts.
_WSMIG_HEADER_BG = "1E3A5F"   # deep navy
_WSMIG_SECTION_BG = "334155"  # slate
_WSMIG_DB_RED = "FF3621"      # Databricks brand red
_WSMIG_ALT_ROW = "F1F5F9"     # very light gray
_WSMIG_FONT = "Calibri"


_REPORT_ONLY_TYPES_REPORT = {
    "STREAMING_TABLE", "MODEL", "ONLINE_TABLE", "VECTOR_INDEX", "MONITOR",
    "UC_SECRET", "LAKEBASE_TABLE", "PIPELINE_TABLE", "MONITOR_METRIC_TABLE",
}


def _is_report_only_object(o: dict[str, Any]) -> bool:
    """A report-only inventory object (never migrated): a Tier-A / FOREIGN / pipeline
    type, or anything flagged in_scope_for_migration=false."""
    if str(o.get("object_type") or "") in _REPORT_ONLY_TYPES_REPORT:
        return True
    return (o.get("definition") or {}).get("in_scope_for_migration") is False


def _wsmig_status_key(entry: Optional[dict[str, Any]]) -> str:
    """Map an internal import-result entry to a status key. Thin alias over the shared
    ``uc_sync.vocab.status_key`` so the report and ``uc_sync_state`` agree exactly."""
    return vocab.status_key(entry)


def build_report(
    objects: list[dict[str, Any]],
    out_path: str,
    *,
    stage: Optional[str] = None,
    export_results: Optional[list[dict[str, Any]]] = None,
    import_results: Optional[list[dict[str, Any]]] = None,
    delta_rows: Optional[list[dict[str, Any]]] = None,
    volume_copy_results: Optional[list[dict[str, Any]]] = None,
    outstanding: Optional[list[dict[str, Any]]] = None,
    run_id: str = "",
    workspace_url: str = "",
) -> str:
    """Write the migration workbook to ``out_path`` (.xlsx). Returns the path.

    Each stage's report becomes the base for the next, so the per-object-type
    sheets carry stage-appropriate status columns:

    * ``INVENTORY`` — no status columns (nothing has happened yet).
    * ``EXPORT``    — an ``export_status`` column.
    * ``IMPORT``    — both ``export_status`` (carried forward from stage 02) and
      ``import_status`` columns.

    ``stage`` is inferred from which results are supplied when not passed
    explicitly (import → IMPORT, export → EXPORT, neither → INVENTORY).
    """

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    def _fill(hex_color: str) -> "PatternFill":
        return PatternFill("solid", fgColor=hex_color)

    if stage is None:
        stage = (
            "IMPORT" if import_results is not None
            else "EXPORT" if export_results is not None
            else "INVENTORY"
        )
    stage = str(stage).upper()

    export_idx = _export_index(export_results or [])
    idx = _import_index(import_results or [])
    tag_idx = _tag_op_index(import_results or [])

    def _status_headers() -> list[str]:
        headers: list[str] = []
        if stage in ("EXPORT", "IMPORT"):
            headers.append("export_status")
        if stage == "IMPORT":
            headers.append("import_status")
        return headers

    def _status_cells(o: dict[str, Any]) -> list[str]:
        name = o["full_name"]
        tname = str(o.get("target_full_name") or "")
        # Report-only objects have no DDL captured and are never imported — the export
        # column must not claim "EXPORTED" (it didn't), and import is a manual step.
        report_only = (
            o.get("object_type") in _REPORT_ONLY_TYPES_REPORT
            or (o.get("definition") or {}).get("in_scope_for_migration") is False
        )
        cells: list[str] = []
        if stage in ("EXPORT", "IMPORT"):
            cells.append(
                "Skipped (report-only)" if report_only
                else _render_export_status(export_idx.get(name) or export_idx.get(tname))
            )
        if stage == "IMPORT":
            cells.append(
                _STATUS_STYLE["manual"][0] if report_only
                else _render_import_status(idx.get(name) or idx.get(tname))
            )
        return cells

    # Fold the incremental delta rows into the object sheets (the Delta sheet is gone —
    # B2): SOURCE_ABSENT objects become a "Deleted in source" row on their own per-type
    # sheet (fixing the visibility gap where they showed ONLY on the Delta sheet), a
    # removed grant is a note on the Grants sheet, and the UNCHANGED tally moves to the
    # Summary. GRANT_ADDED / changed-object rows already read "Updated" per-type.
    source_absent_by_type: dict[str, list[dict[str, Any]]] = {}
    grant_removed_rows: list[dict[str, Any]] = []
    unchanged_count: Optional[int] = None
    for r in (delta_rows or []):
        act = str(r.get("action") or "")
        if act == "SOURCE_ABSENT":
            source_absent_by_type.setdefault(
                str(r.get("object_type") or ""), []
            ).append(r)
        elif act == "GRANT_REMOVED":
            grant_removed_rows.append(r)
        elif act == "UNCHANGED_COUNT":
            try:
                unchanged_count = int(r.get("detail") or 0)
            except (TypeError, ValueError):
                unchanged_count = None
    source_absent_total = sum(len(v) for v in source_absent_by_type.values())

    def _absent_status_cells() -> list[str]:
        """Status column(s) for a Deleted-in-source row (import stage only)."""
        cells: list[str] = []
        if stage in ("EXPORT", "IMPORT"):
            cells.append("")
        if stage == "IMPORT":
            cells.append(_STATUS_STYLE["deleted_in_source"][0])
        return cells

    wb = Workbook()

    def _sheet(title: str, headers: list[str]):
        ws = wb.create_sheet(title)
        ws.append(headers)
        # wsmig palette: navy header band, white bold Calibri.
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF", name=_WSMIG_FONT)
            cell.fill = _fill(_WSMIG_HEADER_BG)
        # Freeze the header row so it stays visible while scrolling (wsmig parity).
        ws.freeze_panes = "A2"
        return ws

    # Summary — title bar (workspace URL + generated timestamp) in the wsmig style.
    ws = wb.active
    ws.title = "Summary"
    ws.append(["UC Governance Migration report"])
    ws["A1"].font = Font(bold=True, size=14, color="FFFFFF", name=_WSMIG_FONT)
    ws["A1"].fill = _fill(_WSMIG_HEADER_BG)
    from datetime import datetime, timezone
    ws.append(["workspace", str(workspace_url or "")])
    ws.append(["generated", datetime.now(timezone.utc).isoformat(timespec="seconds")])
    ws.append(["run_id", run_id])
    ws.append(["objects", len(objects)])
    counts: dict[str, int] = {}
    for o in objects:
        counts[o["object_type"]] = counts.get(o["object_type"], 0) + 1
    ws.append([])
    ws.append(["object_type", "count"])
    for k in sorted(counts):
        ws.append([k, counts[k]])
    if stage in ("EXPORT", "IMPORT") and export_results:
        st = {}
        for r in export_results:
            st[r.get("status", "")] = st.get(r.get("status", ""), 0) + 1
        ws.append([])
        ws.append(["export_status", "count"])
        for k in sorted(st):
            ws.append([k, st[k]])
    if stage == "IMPORT" and import_results:
        # ONE import tally, at PARITY with the export-object total. A governance
        # failure IS an object failure — the engine folds every tag / mask / row-
        # filter / ABAC outcome into the status of the object it protects (a table
        # whose tag failed is dropped → FAILURE; a catalog/schema/volume/view whose
        # tag failed is marked FAILURE; a bad inline mask fails CREATE TABLE). ABAC
        # policies are themselves objects (their own CREATE POLICY), counted here on
        # both sides. So there is no separate governance section: every securable
        # appears exactly once, and no object reads success if its governance failed.
        # The per-tag detail still lives on the Tags sheet; governed-tag ALTER result
        # rows are excluded here so they are not double-counted against their object.
        object_results = [r for r in import_results if not _is_tag_op_result(r)]
        rollup: dict[str, int] = {}
        imported_names: set[str] = set()
        for r in object_results:
            key = _wsmig_status_key(r)
            rollup[key] = rollup.get(key, 0) + 1
            imported_names.add(str(r.get("target_full_name") or r.get("full_name") or ""))
            imported_names.add(str(r.get("source_full_name") or ""))
        # Report-only inventory objects (never imported: Tier-A AI assets, Lakebase /
        # pipeline tables) are inventory-only → counted as a MANUAL step (B4 / confirmed
        # decision), OUTSIDE success. Collected so the Manual-steps section lists them.
        report_only_manual: list[dict[str, Any]] = []
        for o in objects:
            if o["full_name"] in imported_names or str(
                o.get("target_full_name") or ""
            ) in imported_names:
                continue
            if _is_report_only_object(o):
                rollup["manual"] = rollup.get("manual", 0) + 1
                report_only_manual.append(o)
        # Objects present in the baseline but gone from source (SOURCE_ABSENT) →
        # deleted_in_source, surfaced both here and on the object's own per-type sheet.
        if source_absent_total:
            rollup["deleted_in_source"] = (
                rollup.get("deleted_in_source", 0) + source_absent_total
            )
        ws.append([])
        hdr_row = ws.max_row + 1
        ws.append(["Outcome roll-up", "count"])
        for c in ws[hdr_row]:
            c.font = Font(bold=True, color="FFFFFF", name=_WSMIG_FONT)
            c.fill = _fill(_WSMIG_SECTION_BG)
        total = 0
        for key in _SUMMARY_ORDER:  # FAILURES FIRST (see _SUMMARY_ORDER)
            if key not in rollup:
                continue
            label, colour = _STATUS_STYLE[key]
            ws.append([label, rollup[key]])
            ws.cell(row=ws.max_row, column=1).fill = _fill(colour)
            total += rollup[key]
        trow = ws.max_row + 1
        ws.append(["TOTAL", total])
        for c in ws[trow]:
            c.font = Font(bold=True, name=_WSMIG_FONT)
        applied = sum(v for k, v in rollup.items() if k in _SUCCESS_STATUSES)
        skipped = sum(v for k, v in rollup.items() if k in vocab.SKIP_STATUSES)
        ws.append(["applied (created/updated/adopted)", applied])
        ws.append(["skipped (unchanged / create-disabled / deferred)", skipped])
        # The UNCHANGED tally (used to live on the now-removed Delta sheet) as a stat.
        if unchanged_count is not None:
            ws.append(["unchanged (incremental: skipped, zero writes)", unchanged_count])

        # Failures (N) — CURRENT-run failures, FAILURES FIRST (wsmig Summary parity).
        # This + the Outstanding sheet (cumulative from state) are the ONLY failure
        # surfaces; the standalone Issues sheet is gone (B1).
        failures = [r for r in object_results if _wsmig_status_key(r) == "failed"]
        if failures:
            ws.append([])
            frow = ws.max_row + 1
            ws.append([f"Failures ({len(failures)})", ""])
            for c in ws[frow]:
                c.font = Font(bold=True, color="FFFFFF", name=_WSMIG_FONT)
                c.fill = _fill(_WSMIG_DB_RED)
            for r in failures:
                ws.append([
                    str(r.get("target_full_name") or r.get("full_name") or ""),
                    _truncate(r.get("error_message") or r.get("message"), 300),
                ])

        # Manual-steps section (wsmig parity): objects the operator must migrate by hand
        # — a governance step needing a manual prerequisite, plus report-only assets.
        manual = [r for r in object_results if _wsmig_status_key(r) == "manual"]
        manual_total = len(manual) + len(report_only_manual)
        if manual_total:
            ws.append([])
            mrow = ws.max_row + 1
            ws.append([f"Manual steps required ({manual_total})", ""])
            for c in ws[mrow]:
                c.font = Font(bold=True, color="FFFFFF", name=_WSMIG_FONT)
                c.fill = _fill(_WSMIG_DB_RED)
            for r in manual:
                ws.append([
                    str(r.get("target_full_name") or r.get("full_name") or ""),
                    str(r.get("message") or "")[:200],
                ])
            for o in report_only_manual:
                ws.append([
                    o["full_name"],
                    f"inventory-only ({o.get('object_type')}) — migrate manually",
                ])

        # Deleted-in-source — review (N): objects gone from source, reported never
        # dropped (wsmig "Deleted in source — review" parity).
        if source_absent_total:
            ws.append([])
            drow = ws.max_row + 1
            ws.append([f"Deleted in source — review ({source_absent_total})", ""])
            for c in ws[drow]:
                c.font = Font(bold=True, color="FFFFFF", name=_WSMIG_FONT)
                c.fill = _fill(_WSMIG_SECTION_BG)
            for rows in source_absent_by_type.values():
                for r in rows:
                    ws.append([
                        str(r.get("object") or ""),
                        str(r.get("detail") or "reported, not dropped"),
                    ])

    # Outstanding sheet (Part E): CUMULATIVE still-broken objects read from uc_sync_state
    # (last_action = failed) across ALL runs — independent of this run's scope. The
    # Summary "Failures (N)" section is THIS run's failures; this is the authoritative
    # "everything still broken" view (wsmig OUTSTANDING_ACTIONS parity). Rendered right
    # after the Summary as the second failure surface (the Issues sheet is gone — B1).
    if stage == "IMPORT" and outstanding is not None:
        out_sheet = _sheet(
            "Outstanding",
            ["object", "object_type", "last_action", "error_code", "error_message",
             "last_run_id", "last_sync_at"],
        )
        for r in sorted(
            outstanding, key=lambda x: str(x.get("source_full_name") or x.get("object") or "")
        ):
            out_sheet.append([
                str(r.get("source_full_name") or r.get("object") or ""),
                str(r.get("object_type") or ""),
                _STATUS_STYLE.get(str(r.get("last_action") or ""),
                                  (str(r.get("last_action") or ""), ""))[0],
                str(r.get("error_code") or ""),
                _truncate(r.get("error_message") or r.get("detail"), 400),
                str(r.get("run_id") or ""),
                str(r.get("last_sync_at") or ""),
            ])

    # One sheet per object type, each carrying that type's captured detail plus
    # an import-status column. Only types actually present get a sheet.
    by_type: dict[str, list[dict[str, Any]]] = {}
    for o in objects:
        by_type.setdefault(o["object_type"], []).append(o)

    status_headers = _status_headers()

    for obj_type, title, cols in _TYPE_SHEETS:
        rows = by_type.get(obj_type) or []
        absent = source_absent_by_type.get(obj_type) or []
        if not rows and not absent:
            continue
        ws_t = _sheet(title, [h for h, _ in cols] + status_headers)
        for o in sorted(rows, key=lambda x: x["full_name"]):
            ws_t.append([fn(o) for _, fn in cols] + _status_cells(o))
        # SOURCE_ABSENT objects (gone from source) are shown on their OWN type sheet
        # with a "Deleted in source" status — the Delta sheet used to be their only
        # home (the visibility gap). Only the name column is known; the rest is blank.
        for r in sorted(absent, key=lambda x: str(x.get("object") or "")):
            pad = [""] * (len(cols) - 1)
            ws_t.append([str(r.get("object") or "")] + pad + _absent_status_cells())

    for obj_type, title in _INVENTORY_ONLY:
        rows = by_type.get(obj_type)
        if not rows:
            continue
        ws_t = _sheet(
            title,
            ["object", "in_scope_for_migration", "comment", "owner", "note"]
            + status_headers,
        )
        note = (
            "monitor-managed metric table — regenerated when the monitor is recreated; "
            "not migrated"
            if obj_type == "MONITOR_METRIC_TABLE"
            else "inventory-only — report-only Tier-A AI asset (migrate manually)"
        )
        for o in sorted(rows, key=lambda x: x["full_name"]):
            # Report-only inventory rows are a MANUAL step (B4), never blank/SUCCESS.
            inv_status = list(_status_cells(o))
            if stage == "IMPORT" and inv_status and not inv_status[-1]:
                inv_status[-1] = _STATUS_STYLE["manual"][0]
            ws_t.append([
                o["full_name"], "false", _cell(o, "comment"), o.get("owner") or "",
                note,
            ] + inv_status)

    # Governed Tags (B5): the governed-tag DEFINITIONS the utility creates before any
    # SET TAGS (allowed-value lists), one row per tag key — with the create/adopt
    # outcome. Formerly folded into a generic "Other Objects" catch-all.
    governed_tags = [o for o in objects if o["object_type"] == "GOVERNED_TAG"]
    if governed_tags:
        ws_t = _sheet(
            "Governed Tags",
            ["tag_key", "allowed_values", "comment", "owner"] + status_headers,
        )
        for o in sorted(governed_tags, key=lambda x: x["full_name"]):
            allowed = (o.get("definition") or {}).get("allowed_values") or []
            ws_t.append([
                o["full_name"],
                ", ".join(str(v) for v in allowed),
                _cell(o, "comment"), o.get("owner") or "",
            ] + _status_cells(o))

    # Catch-all safety net for any present type not explicitly modeled above (never
    # drop an object silently). Governed tags + ABAC policies have their own sheets, so
    # in practice this appears only if a genuinely new/unmodeled type shows up.
    _known = (
        {t for t, _, _ in _TYPE_SHEETS}
        | {t for t, _ in _INVENTORY_ONLY}
        | {"ABAC_POLICY", "GOVERNED_TAG"}
    )
    other = [o for o in objects if o["object_type"] not in _known]
    if other:
        ws_t = _sheet("Other Objects", ["object", "type", "comment", "owner"] + status_headers)
        for o in sorted(other, key=lambda x: (x["object_type"], x["full_name"])):
            ws_t.append([
                o["full_name"], o["object_type"], _cell(o, "comment"),
                o.get("owner") or "",
            ] + _status_cells(o))

    # Governance detail sheets carry a trailing import_status column (IMPORT stage
    # only) so a reviewer sees, per tag / mask / policy / grant, the import outcome
    # of the securable it protects — a table dropped fail-closed reads FAILED here.
    def _gov_status_header() -> list[str]:
        return ["import_status"] if stage == "IMPORT" else []

    # A table dropped in the fail-closed sweep (action DROP_PROTECTION_FAILED) had
    # its tag / mask / grant ops applied at exec time, but they no longer exist on
    # target. Those governance rows must therefore read ROLLED BACK — not the
    # exec-time SUCCESS the op result still carries (task 6). A genuine create
    # FAILURE (e.g. a bad inline mask fails CREATE TABLE atomically — a different
    # action) is NOT a rollback: the governance never applied, so it reads FAILED.
    _ROLLED_BACK = "Rolled back (table dropped fail-closed)"

    def _object_rolled_back(*names: str) -> bool:
        entry = next((idx.get(n) for n in names if n and idx.get(n)), None)
        return bool(
            entry
            and entry.get("status") == "FAILURE"
            and entry.get("action") == "DROP_PROTECTION_FAILED"
        )

    def _gov_status(*names: str) -> list[str]:
        if stage != "IMPORT":
            return []
        if _object_rolled_back(*names):
            return [_ROLLED_BACK]
        entry = next((idx.get(n) for n in names if n and idx.get(n)), None)
        return [_render_import_status(entry)]

    def _tag_gov_status(*names: str) -> list[str]:
        """Tags sheet status ← the actual APPLY_TAGS op (never the create-skip).

        If the tag op itself succeeded but the owning table was then dropped
        fail-closed (some *other* governance step failed), the tag no longer exists
        → ROLLED BACK. A tag op that itself FAILED keeps its FAILED render (it is the
        actual cause, not a casualty of another failure)."""
        if stage != "IMPORT":
            return []
        entry = next((tag_idx.get(n) for n in names if n and tag_idx.get(n)), None)
        op_failed = bool(entry and str(entry.get("status")) == "FAILURE")
        if _object_rolled_back(*names) and not op_failed:
            return [_ROLLED_BACK]
        if not entry:
            return ["— (no tag op this run)"]
        # A tag is a governance OP, not an object lifecycle event — render APPLIED /
        # FAILED / DRY RUN (like grants), never the object create/update vocab.
        status, action = entry.get("status"), entry.get("action")
        if status == "PENDING" or action == "DRY_RUN":
            return ["DRY RUN (validated, not applied)"]
        if status == "FAILURE":
            msg = str(entry.get("message") or "")
            return [f"FAILED{': ' + msg[:200] if msg else ''}"]
        return ["APPLIED"]

    def _grant_gov_status(*names: str) -> list[str]:
        """Grants sheet status ← APPLIED whenever the securable exists (grants run
        inline even when its create was skipped/disabled); FAILED only if the
        securable's own create failed (plan P2-C). Never the create-skip label. In a
        dry run nothing is applied, so it reads the same DRY RUN message as every
        other sheet."""
        if stage != "IMPORT":
            return []
        if _object_rolled_back(*names):
            return [_ROLLED_BACK]
        entry = next((idx.get(n) for n in names if n and idx.get(n)), None)
        if not entry:
            return [""]
        status, action = entry.get("status"), entry.get("action")
        if status == "PENDING" or action == "DRY_RUN":
            return ["DRY RUN (validated, not applied)"]
        if status == "FAILURE":
            return ["FAILED (object not created)"]
        return ["APPLIED"]

    # Tags Applied (object + column grain) — the governed tags this run SET on each
    # securable, with the actual APPLY_TAGS outcome per row (B6 rename from "Tags").
    tags = _sheet(
        "Tags Applied",
        ["object", "level", "column", "key", "value"] + _gov_status_header(),
    )
    for o in objects:
        st = _tag_gov_status(o["full_name"], str(o.get("target_full_name") or ""))
        for k, v in (o.get("tags") or {}).items():
            tags.append([o["full_name"], o["object_type"], "", k, v] + st)
        for col, ctags in ((o.get("definition") or {}).get("column_tags") or {}).items():
            for k, v in ctags.items():
                tags.append([o["full_name"], "COLUMN", col, k, v] + st)

    # Column masks & row filters (classic, ALTER-applied). These are table-only in
    # UC — the securable is a table, external table, materialized view, or
    # streaming table (never a plain/dynamic view), so the first column is the
    # general "object", not "table". Views express equivalent protection in their
    # own definition (see the identity_aware column on the view sheets).
    cm = _sheet(
        "Column Masks & Row Filters",
        ["object", "kind", "column", "function", "using/on cols"] + _gov_status_header(),
    )
    for o in objects:
        d = o.get("definition") or {}
        st = _gov_status(o["full_name"], str(o.get("target_full_name") or ""))
        for mask in d.get("column_masks") or []:
            cm.append([
                o["full_name"], "CLASSIC MASK", mask.get("column_name"),
                mask.get("function_name"),
                ", ".join(mask.get("using_column_names") or []),
            ] + st)
        rf = d.get("row_filter")
        if isinstance(rf, dict) and rf.get("function_name"):
            cm.append([
                o["full_name"], "CLASSIC ROW FILTER", "",
                rf.get("function_name"),
                ", ".join(rf.get("input_column_names") or []),
            ] + st)

    # ABAC policies
    abac = _sheet(
        "ABAC Policies",
        ["policy", "type", "on_securable", "function", "match_columns",
         "to", "except"] + _gov_status_header(),
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
        ] + _gov_status(o["full_name"], str(o.get("target_full_name") or "")))

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
         "matched_table", "matched_column", "tag_key", "tag_value"]
        + _gov_status_header(),
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
        pol_st = _gov_status(o["full_name"], str(o.get("target_full_name") or ""))
        hits = 0
        for table_fn, ctags in col_tag_index.items():
            if not _table_in_policy_scope(table_fn, on_type, on_securable):
                continue
            for col, tags in ctags.items():
                hit = _column_condition_match(tags, conditions)
                if hit:
                    key, value = hit
                    matched.append(base + [table_fn, col, key, value] + pol_st)
                    hits += 1
        if hits == 0:
            note = (
                "(no captured tagged columns match)" if conditions
                else "(match rule not tag-based / unparsed)"
            )
            matched.append(base + ["", "", "", note] + pol_st)

    # Grants (explicit privilege assignments captured at each securable level).
    # These are the grants the utility replays; UC re-establishes inheritance on
    # the target automatically once the parent-level (catalog/schema) grants are
    # replayed, so inherited-only effective privileges are intentionally not
    # listed here (they are not migrated as per-object grants).
    grants = _sheet(
        "Grants",
        ["object", "level", "principal", "type", "privileges"] + _gov_status_header(),
    )
    for o in objects:
        st = _grant_gov_status(o["full_name"], str(o.get("target_full_name") or ""))
        for g in o.get("grants") or []:
            grants.append([
                o["full_name"], o["object_type"], g.get("principal"),
                g.get("principal_type"),
                ", ".join(g.get("privileges") or []),
            ] + st)
    # A grant removed on source is reported, NEVER revoked (confirmed decision) — shown
    # here (its former home was the now-removed Delta sheet) so the change stays visible
    # on the object's own governance sheet. Import stage only (incremental delta).
    if stage == "IMPORT" and grant_removed_rows:
        removed_status = ["Removed in source (not revoked)"] if _gov_status_header() else []
        for r in grant_removed_rows:
            grants.append([
                str(r.get("object") or ""), str(r.get("object_type") or ""),
                str(r.get("detail") or ""), "", "",
            ] + removed_status)

    # Volume Data Copy (FEAT-4, bug #17): the per-file source→target file copy the
    # import performs when copy_volume_data is on. This is separate from the volume
    # OBJECT migration above — it reports the file movement (copied / skipped-unchanged
    # / skipped-too-large / failed) so the copy is visible in the report, not only in
    # the run's exit payload. Only rendered when the caller supplies results.
    if volume_copy_results is not None:
        vc = _sheet(
            "Volume Data Copy",
            ["volume", "status", "source_path", "target_path", "bytes_copied", "message"],
        )
        _VC_STATUS_STYLE = {
            "COPIED": ("Copied", "D1FAE5"),
            "SKIPPED_UNCHANGED": ("Skipped (unchanged)", "E5E7EB"),
            "SKIPPED_TOO_LARGE": ("Skipped (>5 GB)", "FDE68A"),
            "FAILED": ("FAILED", "FEE2E2"),
        }
        vc_rollup: dict[str, int] = {}
        vc_bytes = 0
        # FAILED first, then copied, then the skip variants — mirrors the spine order.
        _VC_ORDER = ("FAILED", "COPIED", "SKIPPED_UNCHANGED", "SKIPPED_TOO_LARGE")
        for r in sorted(
            volume_copy_results,
            key=lambda x: _VC_ORDER.index(str(x.get("status")))
            if str(x.get("status")) in _VC_ORDER else len(_VC_ORDER),
        ):
            st = str(r.get("status") or "")
            vc_rollup[st] = vc_rollup.get(st, 0) + 1
            vc_bytes += int(r.get("bytes_copied") or 0)
            label, colour = _VC_STATUS_STYLE.get(st, (st or "—", "FFFFFF"))
            vc.append([
                str(r.get("volume") or ""), label,
                str(r.get("source_path") or ""), str(r.get("target_path") or ""),
                int(r.get("bytes_copied") or 0), str(r.get("message") or "")[:200],
            ])
            vc.cell(row=vc.max_row, column=2).fill = _fill(colour)
        vc.append([])
        hrow = vc.max_row + 1
        vc.append(["Copy roll-up", "count"])
        for c in vc[hrow]:
            c.font = Font(bold=True, color="FFFFFF", name=_WSMIG_FONT)
            c.fill = _fill(_WSMIG_SECTION_BG)
        for key in _VC_ORDER:
            if key in vc_rollup:
                label, colour = _VC_STATUS_STYLE[key]
                vc.append([label, vc_rollup[key]])
                vc.cell(row=vc.max_row, column=1).fill = _fill(colour)
        vc.append(["TOTAL files", sum(vc_rollup.values())])
        vc.append(["bytes copied", vc_bytes])

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
