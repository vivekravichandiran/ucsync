"""Sync state table — last successful/failed sync per UC object for incremental runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from uc_sync import vocab
from uc_sync.logging_util import get_log

log = get_log(__name__)

# Incremental-sync fingerprint columns (task 1): the three per-object fingerprints
# the next run diffs against. Appended to older state tables in place by
# SyncStateService.ensure_table (same pattern as the audit table).
STATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {full_name} (
  batch_id STRING,
  run_id STRING,
  object_type STRING,
  source_full_name STRING,
  target_full_name STRING,
  source_object_id STRING,
  source_definition_hash STRING COMMENT 'Whole-object canonical hash (export-level integrity): the entire captured object. Any change to the object changes it.',
  ddl_hash STRING COMMENT 'Structural DDL hash only — columns/types/view text/function body/storage AND inline classic masks & row filters (they ride in CREATE); tags excluded. Drives CHANGED/REPLACED.',
  ddl_status STRING COMMENT 'Per-facet outcome of the structure/DDL apply (backlog item 8): applied/skipped/failed/not_selected/not_attempted. Pairs with ddl_hash — the hash advances only when this is applied/skipped.',
  governance_hash STRING COMMENT 'TAGS ONLY — object-level + per-column governed tags. Drives GOVERNANCE_UPDATED even when the DDL is unchanged.',
  governance_status STRING COMMENT 'Per-facet outcome of the tag/ABAC apply (backlog item 8). Pairs with governance_hash. A failed governance apply keeps this = failed so the next run re-applies (never masked by a clean last_action).',
  grants_json STRING,
  grants_status STRING COMMENT 'Per-facet outcome of the grant apply (backlog item 8). Pairs with grants_json. A grant that raised is failed → grants replay next run even when ddl/governance are clean (external-object-created-later case).',
  source_last_modified_at TIMESTAMP,
  last_action STRING COMMENT 'Unified status vocabulary (uc_sync.vocab), shared verbatim with the report: created/created_with_warning/updated/adopted/skipped/skipped_create_disabled/not_selected/skipped_no_object/deleted_in_source/manual/failed.',
  last_sync_at TIMESTAMP,
  last_synced_by STRING,
  ddl_path STRING,
  grants_path STRING,
  error_code STRING,
  error_message STRING,
  detail STRING,
  utility_version STRING,
  first_seen TIMESTAMP COMMENT 'When this object was FIRST migrated (set once on insert, preserved across updates).',
  connectivity_mode STRING COMMENT 'direct vs airgap for the run that last touched this object.',
  failure_category STRING COMMENT 'Coarse failure taxonomy (GOVERNANCE / STORAGE / SCHEMA_EVOLUTION / DEPENDENCY_UNRESOLVED / API_ERROR / OTHER) — set only when last_action=failed.',
  last_error_raw STRING COMMENT 'Full untruncated error text for the last failure (error_message is truncated for display).',
  updated_at TIMESTAMP
) USING DELTA
"""

STATE_COLUMNS = [
    "batch_id",
    "run_id",
    "object_type",
    "source_full_name",
    "target_full_name",
    "source_object_id",
    "source_definition_hash",
    "ddl_hash",
    "ddl_status",
    "governance_hash",
    "governance_status",
    "grants_json",
    "grants_status",
    "source_last_modified_at",
    "last_action",
    "last_sync_at",
    "last_synced_by",
    "ddl_path",
    "grants_path",
    "error_code",
    "error_message",
    "detail",
    "utility_version",
    "first_seen",
    "connectivity_mode",
    "failure_category",
    "last_error_raw",
    "updated_at",
]

# Columns whose value must be PRESERVED on a MERGE update (set once, never overwritten).
_PRESERVE_ON_UPDATE = {"first_seen"}

# --- Per-facet status vocabulary (backlog item 8) ---------------------------
# The three facets an object is tracked in: structure/DDL, governance (tags/ABAC),
# grants. Each records its own apply outcome so a partial failure (structure OK, tags
# failed) is never masked as fully clean.
FACET_APPLIED = "applied"          # the facet was (re)applied this run
FACET_SKIPPED = "skipped"          # idempotent no-op / unchanged / create-disabled
FACET_FAILED = "failed"            # the apply raised — re-attempt next run
FACET_NOT_SELECTED = "not_selected"    # the facet's toggle was off
FACET_NOT_ATTEMPTED = "not_attempted"  # not reached (e.g. object absent / DDL failed first)

# A facet's paired hash advances (to the source fingerprint) ONLY when the facet
# applied or was a clean skip; otherwise the prior hash is preserved (rule #1). Which
# state column each facet's hash-gate reads:
_FACET_HASH_GATE = {
    "ddl_hash": "ddl_status",
    "governance_hash": "governance_status",
    "grants_json": "grants_status",
}
_FACET_HASH_ADVANCE = {FACET_APPLIED, FACET_SKIPPED}
# A facet that must be RE-APPLIED next run (drives delta rule #2's OR-failed clause).
FACET_RETRY_STATUSES = {FACET_FAILED}

# (column, SQL type) for columns that may be missing on an older state table. The
# last_action rename lands here too: an older table has last_sync_status but not
# last_action, so ADD it (ensure_table then backfills it from the legacy column). The
# Part-D parity columns (first_seen / connectivity_mode / failure_category /
# last_error_raw) are appended in place the same way.
_STATE_UPGRADE_COLUMNS = [
    ("ddl_hash", "STRING"),
    ("governance_hash", "STRING"),
    ("grants_json", "STRING"),
    ("detail", "STRING"),
    ("last_action", "STRING"),
    ("first_seen", "TIMESTAMP"),
    ("connectivity_mode", "STRING"),
    ("failure_category", "STRING"),
    ("last_error_raw", "STRING"),
    # Per-facet status columns (backlog item 8) — added in place to older tables the
    # same way; ensure_table backfills them from last_action + hash presence + category.
    ("ddl_status", "STRING"),
    ("governance_status", "STRING"),
    ("grants_status", "STRING"),
]

# error_code → coarse failure_category (Part D). A fallback keeps unknown codes as OTHER.
_FAILURE_CATEGORY = {
    "PROTECTION_FAILED": "GOVERNANCE",
    "GOVERNANCE_FAILED": "GOVERNANCE",
    "GOVERNANCE_PREREQ_MISSING": "DEPENDENCY_UNRESOLVED",
    "ABAC_WAREHOUSE_REQUIRED": "GOVERNANCE",
    "GOVERNED_TAG_FAILED": "GOVERNANCE",
    "SCHEMA_EVOLUTION_FAILED": "SCHEMA_EVOLUTION",
    "EXTERNAL_CREATE_FAILED": "STORAGE",
    "LOCATION_OVERLAP": "STORAGE",
    "EXTERNAL_LOCATION_MISSING": "STORAGE",
}


def _failure_category_for(error_code: str, last_action: str) -> str:
    """Coarse failure bucket for a state row — only meaningful for a failure."""
    if last_action != vocab.FAILED:
        return ""
    code = str(error_code or "").upper()
    return _FAILURE_CATEGORY.get(code, "API_ERROR" if code else "OTHER")


def _facet_statuses(
    result: Mapping[str, Any], last_action: str, failure_category: str
) -> tuple[str, str, str]:
    """Derive ``(ddl_status, governance_status, grants_status)`` for an object's state
    row from its FINAL import result (backlog item 8, rule #4 + the matrix).

    The engine folds a governance failure into the owning object's result
    (``_drop_failed_tables`` / ``_mark_ungoverned_objects``), so the failed facet is
    read from ``action`` / ``failure_category``. A grant failure is carried explicitly
    on the result as ``grants_status`` (the engine sets it when a GRANT raised), so it
    survives even when the object is otherwise clean (the external-object-created-later
    grants-replay gap).
    """
    action = str(result.get("action") or "")
    # An explicit grants_status from the engine wins for the grants facet.
    explicit_grants = str(result.get("grants_status") or "")
    has_gov = bool(str(result.get("governance_hash") or ""))

    if last_action == vocab.FAILED:
        cat = str(failure_category or "")
        if cat == "GOVERNANCE":
            # DDL is fine UNLESS the fail-closed sweep dropped a fresh shell this run
            # created (DROP_PROTECTION_FAILED) — then the drop rolled back the DDL too.
            ddl_status = (
                FACET_FAILED if action == "DROP_PROTECTION_FAILED" else FACET_APPLIED
            )
            governance_status = FACET_FAILED
            grants_status = explicit_grants or FACET_NOT_ATTEMPTED
        elif cat in ("STORAGE", "SCHEMA_EVOLUTION", "DEPENDENCY_UNRESOLVED"):
            # A structural/prereq failure — DDL failed, governance/grants not reached.
            ddl_status = FACET_FAILED
            governance_status = FACET_NOT_ATTEMPTED
            grants_status = explicit_grants or FACET_NOT_ATTEMPTED
        else:
            # Unknown/other failure — be conservative so the object re-verifies.
            ddl_status = FACET_FAILED
            governance_status = FACET_NOT_ATTEMPTED
            grants_status = explicit_grants or FACET_NOT_ATTEMPTED
        return ddl_status, governance_status, grants_status

    # Clean object: the DDL facet mirrors the clean last_action (created/updated/adopted
    # → applied; the skip family → skipped). Governance applied when the object carries a
    # governance fingerprint, else there was nothing to apply (skipped). Grants take the
    # engine's explicit status when present, else skipped.
    ddl_status = FACET_APPLIED if last_action in vocab.SUCCESS_STATUSES else FACET_SKIPPED
    governance_status = FACET_APPLIED if has_gov else FACET_SKIPPED
    grants_status = explicit_grants or FACET_SKIPPED
    return ddl_status, governance_status, grants_status


def _last_action_for(result: Mapping[str, Any]) -> str:
    """The durable per-object ``last_action`` for a result — the ONE shared vocabulary
    (``uc_sync.vocab``) the report renders too, so the state table and the workbook
    never disagree for the same outcome (the "constant naming + vocab" unification;
    replaces the old, separate SUCCESS/UNCHANGED/ADOPTED/… state vocabulary)."""
    return vocab.status_key(dict(result))


def ensure_state_schema_sql(full_name: str) -> Optional[str]:
    parts = full_name.split(".")
    if len(parts) != 3:
        return None
    return f"CREATE SCHEMA IF NOT EXISTS {parts[0]}.{parts[1]}"


def ensure_state_table_sql(full_name: str) -> str:
    return STATE_TABLE_DDL.format(full_name=full_name)


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        epoch = float(value)
        if epoch > 1e12:
            epoch = epoch / 1000.0
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


class SyncStateService:
    def __init__(self, spark: Any, full_name: str):
        self.spark = spark
        self.full_name = full_name

    def ensure_table(self) -> None:
        schema_sql = ensure_state_schema_sql(self.full_name)
        if schema_sql:
            self.spark.sql(schema_sql)
        self.spark.sql(ensure_state_table_sql(self.full_name))
        # Upgrade older state tables in place with the incremental-sync fingerprint
        # columns, so a pre-existing baseline keeps working (missing columns read as
        # NULL → treated as a change → re-applied, which is safe/idempotent).
        try:
            existing = {f.name for f in self.spark.table(self.full_name).schema}
        except Exception:  # noqa: BLE001 - if we cannot read schema, skip upgrade
            return
        missing_names = [
            name for name, _sql_type in _STATE_UPGRADE_COLUMNS if name not in existing
        ]
        if missing_names:
            add = ", ".join(
                f"{name} {sql_type}"
                for name, sql_type in _STATE_UPGRADE_COLUMNS
                if name in missing_names
            )
            self.spark.sql(
                f"ALTER TABLE {self.full_name} ADD COLUMNS ({add})"
            )
        # last_action rename backfill: a legacy table carried last_sync_status; map its
        # values into last_action for any row still NULL, so a pre-existing baseline reads
        # in the unified vocabulary and a prior FAILURE is still seen as non-clean (bug
        # #18) rather than a blank that reads as clean. The UPDATE is idempotent
        # (WHERE last_action IS NULL) and SELF-HEALING: it runs on every legacy table —
        # not only the run that adds the column — so rows stranded NULL by an earlier
        # partial/buggy upgrade are recovered on the next run. The legacy column is left
        # in place (harmless) — never read once last_action is set.
        if "last_sync_status" in existing:
            cases = " ".join(
                f"WHEN '{legacy}' THEN '{action}'"
                for legacy, action in vocab._LEGACY_STATUS_TO_ACTION.items()
            )
            try:
                self.spark.sql(
                    f"UPDATE {self.full_name} SET last_action = "
                    f"CASE last_sync_status {cases} ELSE lower(last_sync_status) END "
                    "WHERE last_action IS NULL"
                )
            except Exception as exc:  # noqa: BLE001 - best-effort; NULL reads clean
                log.debug("last_action backfill skipped (best-effort): %r", exc)

        # Per-facet status backfill (backlog item 8): legacy rows (written before the
        # status columns existed) have NULL statuses. Derive them from the single
        # last_action + hash presence + failure_category so the first post-upgrade run
        # doesn't re-touch every clean object, and a prior failure stays non-clean.
        # A clean row → each facet applied/skipped (by hash presence); a failed row →
        # route the failed facet from failure_category, others not_attempted. Idempotent
        # + self-healing (WHERE ddl_status IS NULL). NULL status reads as re-verify.
        if "ddl_status" in existing or "ddl_status" in {
            n for n, _ in _STATE_UPGRADE_COLUMNS
        }:
            gov = "GOVERNANCE"
            ddl_cats = "('STORAGE','SCHEMA_EVOLUTION','DEPENDENCY_UNRESOLVED')"
            try:
                self.spark.sql(
                    f"UPDATE {self.full_name} SET "
                    # ddl_status
                    "ddl_status = CASE "
                    f"  WHEN last_action = 'failed' AND failure_category = '{gov}' "
                    "       THEN 'applied' "
                    f"  WHEN last_action = 'failed' THEN 'failed' "
                    "  WHEN last_action IN ('created','created_with_warning','updated',"
                    "       'adopted') THEN 'applied' "
                    "  ELSE 'skipped' END, "
                    # governance_status
                    "governance_status = CASE "
                    f"  WHEN last_action = 'failed' AND failure_category = '{gov}' "
                    "       THEN 'failed' "
                    f"  WHEN last_action = 'failed' AND failure_category IN {ddl_cats} "
                    "       THEN 'not_attempted' "
                    f"  WHEN last_action = 'failed' THEN 'not_attempted' "
                    "  WHEN governance_hash IS NOT NULL AND governance_hash <> '' "
                    "       THEN 'applied' ELSE 'skipped' END, "
                    # grants_status
                    "grants_status = CASE "
                    f"  WHEN last_action = 'failed' THEN 'not_attempted' "
                    "  WHEN grants_json IS NOT NULL AND grants_json NOT IN ('','{{}}') "
                    "       THEN 'applied' ELSE 'skipped' END "
                    "WHERE ddl_status IS NULL"
                )
            except Exception as exc:  # noqa: BLE001 - best-effort; NULL reads as re-verify
                log.debug("facet-status backfill skipped (best-effort): %r", exc)

    def load_baseline(self) -> dict[str, dict[str, Any]]:
        """Read the current per-object baseline for an incremental run.

        Returns ``{source_full_name: {object_type, ddl_hash, governance_hash,
        grants, last_action}}`` — one row per object (the MERGE keeps the latest).
        ``grants`` is the parsed explicit-grant set (``{principal: [privileges]}``);
        ``last_action`` is the prior run's outcome (the unified vocabulary) so the delta
        planner can re-attempt anything not cleanly applied (bug #18). A missing table /
        unreadable state yields an empty baseline → the run is **full**.
        """
        try:
            self.ensure_table()
            rows = self.spark.sql(
                "SELECT source_full_name, object_type, ddl_hash, governance_hash, "
                "grants_json, last_action, ddl_status, governance_status, grants_status "
                f"FROM {self.full_name}"
            ).collect()
        except Exception:  # noqa: BLE001 - no baseline yet → full run
            return {}
        baseline: dict[str, dict[str, Any]] = {}
        for row in rows:
            data = row.asDict() if hasattr(row, "asDict") else dict(row)
            name = str(data.get("source_full_name") or "")
            if not name:
                continue
            grants_json = data.get("grants_json")
            try:
                grants = json.loads(grants_json) if grants_json else {}
            except (TypeError, ValueError):
                grants = {}
            baseline[name] = {
                "object_type": str(data.get("object_type") or ""),
                "ddl_hash": str(data.get("ddl_hash") or ""),
                "governance_hash": str(data.get("governance_hash") or ""),
                "grants": grants if isinstance(grants, dict) else {},
                "last_action": str(data.get("last_action") or ""),
                # Per-facet statuses (backlog item 8) — drive rule #2's OR-failed clause
                # in the delta planner. Missing (legacy pre-backfill) reads as blank.
                "ddl_status": str(data.get("ddl_status") or ""),
                "governance_status": str(data.get("governance_status") or ""),
                "grants_status": str(data.get("grants_status") or ""),
            }
        return baseline

    def failed_object_names(self) -> set[str]:
        """Names of every object whose prior run left a facet FAILED (backlog item 4).

        Returns the union of ``source_full_name`` + ``target_full_name`` for each object
        with any per-facet ``*_status = 'failed'`` (item 8), OR — for a legacy row written
        before the status columns existed (all three NULL) — whose ``last_action`` is in
        ``vocab.OUTSTANDING_ACTIONS`` (a failure). This is the exact set the
        retry-failed-only import replays. Empty on any read error (→ nothing to retry).
        """
        outstanding = ", ".join(f"'{a}'" for a in sorted(vocab.OUTSTANDING_ACTIONS))
        try:
            self.ensure_table()
            rows = self.spark.sql(
                "SELECT source_full_name, target_full_name, ddl_status, "
                "governance_status, grants_status, last_action "
                f"FROM {self.full_name} WHERE "
                "ddl_status = 'failed' OR governance_status = 'failed' "
                "OR grants_status = 'failed' OR ("
                "  ddl_status IS NULL AND governance_status IS NULL "
                f"  AND grants_status IS NULL AND last_action IN ({outstanding}))"
            ).collect()
        except Exception:  # noqa: BLE001 - no state / unreadable → nothing to retry
            return set()
        names: set[str] = set()
        for row in rows:
            data = row.asDict() if hasattr(row, "asDict") else dict(row)
            for key in ("source_full_name", "target_full_name"):
                val = str(data.get(key) or "")
                if val:
                    names.add(val)
        return names

    def outstanding_rows(self) -> list[dict[str, Any]]:
        """Cumulative still-broken objects across ALL runs — every state row whose
        ``last_action`` is in ``vocab.OUTSTANDING_ACTIONS`` (failures). Feeds the report's
        Outstanding sheet (Part E); read AFTER this run's state upsert so it reflects the
        authoritative current picture. Empty on any read error (best-effort)."""
        actions = sorted(vocab.OUTSTANDING_ACTIONS)
        in_list = ", ".join(f"'{a}'" for a in actions)
        try:
            self.ensure_table()
            rows = self.spark.sql(
                "SELECT source_full_name, object_type, last_action, error_code, "
                "error_message, run_id, last_sync_at "
                f"FROM {self.full_name} WHERE last_action IN ({in_list})"
            ).collect()
        except Exception:  # noqa: BLE001 - no state / unreadable → nothing outstanding
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            data = row.asDict() if hasattr(row, "asDict") else dict(row)
            out.append({
                "source_full_name": str(data.get("source_full_name") or ""),
                "object_type": str(data.get("object_type") or ""),
                "last_action": str(data.get("last_action") or ""),
                "error_code": str(data.get("error_code") or ""),
                "error_message": str(data.get("error_message") or ""),
                "run_id": str(data.get("run_id") or ""),
                "last_sync_at": str(data.get("last_sync_at") or ""),
            })
        return out

    def upsert(self, rows: Iterable[Mapping[str, Any]]) -> int:
        records = [dict(row) for row in rows]
        if not records:
            return 0
        self.ensure_table()
        schema = self.spark.table(self.full_name).schema
        aligned = []
        for record in records:
            values = []
            for field in schema.fields:
                value = record.get(field.name)
                type_name = field.dataType.simpleString().lower()
                if "timestamp" in type_name:
                    value = _as_datetime(value)
                elif type_name in {"string", "varchar", "char"} and value is not None:
                    value = str(value)
                values.append(value)
            aligned.append(tuple(values))
        temp_view = f"_uc_sync_state_batch_{abs(hash(self.full_name)) % 10_000_000}"
        (
            self.spark.createDataFrame(aligned, schema=schema)
            .createOrReplaceTempView(temp_view)
        )
        # MERGE keeps one row per source object for incremental planning. first_seen is
        # set ONCE and preserved thereafter — but a row that predates the column (legacy
        # upgrade) has first_seen NULL, so use coalesce(existing, now) to fill it on the
        # next update while never overwriting an existing value. INSERT writes it fresh.
        existing_cols = {f.name for f in schema.fields}
        advance = ", ".join(f"'{s}'" for s in sorted(_FACET_HASH_ADVANCE))
        set_parts = []
        for f in schema.fields:
            if f.name in _PRESERVE_ON_UPDATE:
                set_parts.append(
                    f"target.{f.name} = coalesce(target.{f.name}, source.{f.name})"
                )
            elif f.name in _FACET_HASH_GATE and _FACET_HASH_GATE[f.name] in existing_cols:
                # Per-facet hash gating (backlog item 8, rule #4): advance a facet's hash
                # to the source fingerprint ONLY when that facet applied / cleanly
                # skipped; a failed / not-attempted facet keeps its prior hash so the
                # next run still sees it as needing re-apply.
                status_col = _FACET_HASH_GATE[f.name]
                set_parts.append(
                    f"target.{f.name} = CASE WHEN source.{status_col} IN ({advance}) "
                    f"THEN source.{f.name} ELSE target.{f.name} END"
                )
            else:
                set_parts.append(f"target.{f.name} = source.{f.name}")
        set_clause = ", ".join(set_parts)
        self.spark.sql(
            f"""
            MERGE INTO {self.full_name} AS target
            USING {temp_view} AS source
            ON target.source_full_name = source.source_full_name
               AND target.object_type = source.object_type
            WHEN MATCHED THEN UPDATE SET {set_clause}
            WHEN NOT MATCHED THEN INSERT *
            """
        )
        return len(records)


def state_row_from_import(
    *,
    batch_id: str,
    run_id: str,
    result: Mapping[str, Any],
    ran_by: str,
    utility_version: str,
    connectivity_mode: str = "",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    last_action = _last_action_for(result)
    failure_category = _failure_category_for(
        str(result.get("error_code") or ""), last_action
    )
    ddl_status, governance_status, grants_status = _facet_statuses(
        result, last_action, failure_category
    )
    # Split the two message channels (bug #19): error_message carries ONLY a real
    # failure; detail carries informational context (skip reason, applied note, DDL).
    err_text = str(result.get("error_message") or "")
    info_text = str(result.get("message") or "")
    if last_action == vocab.FAILED:
        error_message = (err_text or info_text)[:4000]
        detail = ""
        last_error_raw = (err_text or info_text)  # full, untruncated (Part D)
    else:
        error_message = ""
        detail = (info_text or err_text)[:4000]
        last_error_raw = ""
    return {
        "batch_id": batch_id,
        "run_id": run_id,
        "object_type": str(result.get("object_type") or ""),
        "source_full_name": str(
            result.get("source_full_name") or result.get("full_name") or ""
        ),
        "target_full_name": str(result.get("target_full_name") or ""),
        "source_object_id": str(result.get("source_object_id") or ""),
        "source_definition_hash": str(
            result.get("source_definition_hash")
            or result.get("definition_hash")
            or ""
        ),
        "ddl_hash": str(result.get("ddl_hash") or ""),
        "ddl_status": ddl_status,
        "governance_hash": str(result.get("governance_hash") or ""),
        "governance_status": governance_status,
        "grants_json": (
            result.get("grants_json")
            if isinstance(result.get("grants_json"), str)
            else json.dumps(result.get("grants_json") or {}, sort_keys=True)
        ),
        "grants_status": grants_status,
        "source_last_modified_at": _as_datetime(
            result.get("source_last_modified_at") or result.get("last_modified_at")
        ),
        "last_action": last_action,
        "last_sync_at": now,
        "last_synced_by": ran_by,
        "ddl_path": str(result.get("ddl_path") or ""),
        "grants_path": str(result.get("grants_path") or ""),
        "error_code": str(result.get("error_code") or ""),
        "error_message": error_message,
        "detail": detail,
        "utility_version": utility_version,
        # Part D — state-table parity columns.
        "first_seen": now,  # preserved on update by the MERGE (set once, on insert)
        "connectivity_mode": str(connectivity_mode or ""),
        "failure_category": _failure_category_for(
            str(result.get("error_code") or ""), last_action
        ),
        "last_error_raw": last_error_raw,
        "updated_at": now,
    }
