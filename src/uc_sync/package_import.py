"""Import Unity Catalog objects from a migrated export package."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from uc_sync.dependency import _TYPE_RANK
from uc_sync.location_mapping import ObjectLocations
from uc_sync.models import ObjectType
from uc_sync.report import _table_in_policy_scope

# View-like securables are created AFTER governance + the drop sweep, so a view
# built on a table that governance failed (and dropped) simply fails to create —
# no dependency/cascade tracking needed.
_VIEW_LIKE_TYPES = {
    "VIEW", "DYNAMIC_VIEW", "METRIC_VIEW", "MATERIALIZED_VIEW", "STREAMING_TABLE",
}
# Tables this run creates are empty shells, so dropping one on a governance
# failure loses no data (fail-closed). Pre-existing tables are SKIP_EXISTING and
# never recorded, hence never dropped.
_DROPPABLE_TABLE_TYPES = {"TABLE", "EXTERNAL_TABLE"}


@dataclass
class PackageImportResult:
    object_type: str
    source_full_name: str
    target_full_name: str
    full_name: str
    action: str
    status: str
    message: str = ""
    error_code: str = ""
    ddl_path: str = ""
    grants_path: str = ""
    policies_path: str = ""
    dependency_level: int = 0
    import_order: int = 0
    source_definition_hash: str = ""
    source_object_id: str = ""
    source_last_modified_at: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Longest ObjectType values first so EXTERNAL_TABLE wins over TABLE, etc.
_OBJECT_TYPE_PREFIXES = tuple(
    sorted((member.value for member in ObjectType), key=len, reverse=True)
)


def _parse_sql_filename(name: str) -> tuple[str, str]:
    stem = name[:-4] if name.lower().endswith(".sql") else name
    upper = stem.upper()
    for object_type in _OBJECT_TYPE_PREFIXES:
        prefix = f"{object_type}_"
        if upper.startswith(prefix):
            encoded = stem[len(prefix) :]
            return object_type, encoded.replace("__", ".")
    return "UNKNOWN", stem.replace("__", ".")


_OWNER_TO_RE = re.compile(r"\bOWNER\s+TO\b", re.IGNORECASE)
_OWNER_TARGET_RE = re.compile(
    r"\bALTER\s+(EXTERNAL\s+LOCATION|STORAGE\s+CREDENTIAL|MATERIALIZED\s+VIEW|"
    r"STREAMING\s+TABLE|CATALOG|SCHEMA|TABLE|VIEW|VOLUME|FUNCTION|CONNECTION|SHARE)"
    r"\s+(.+?)\s+OWNER\s+TO\b",
    re.IGNORECASE | re.DOTALL,
)


def _is_owner_statement(statement: str) -> bool:
    """True for an ``ALTER … OWNER TO …`` ownership transfer."""

    return bool(_OWNER_TO_RE.search(statement or ""))


def _owner_statement_target(statement: str) -> tuple[str, str]:
    """(object_type, unquoted full name) parsed from an ``OWNER TO`` statement."""

    match = _OWNER_TARGET_RE.search(statement or "")
    if not match:
        return "", ""
    object_type = re.sub(r"\s+", "_", match.group(1).strip().upper())
    name = match.group(2).strip().replace("`", "")
    return object_type, name


def _type_rank(object_type: str) -> int:
    try:
        return int(_TYPE_RANK.get(ObjectType(object_type), 999))
    except ValueError:
        return 999


def _split_statements(sql_text: str) -> list[str]:
    """Split a SQL file into executable statements, preserving $$ blocks."""

    statements: list[str] = []
    buffer: list[str] = []
    in_dollar = False
    for line in str(sql_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        if "$$" in line:
            # Toggle for each $$ occurrence on the line.
            in_dollar = (line.count("$$") % 2 == 1) ^ in_dollar
        buffer.append(line)
        if not in_dollar and stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
    trailing = "\n".join(buffer).strip()
    if trailing:
        statements.append(trailing if trailing.endswith(";") else f"{trailing};")
    return statements


_ALREADY_EXISTS_MARKERS = (
    "TABLE_OR_VIEW_ALREADY_EXISTS",
    "ALREADY_EXISTS",
    "SCHEMA_ALREADY_EXISTS",
    "CATALOG_ALREADY_EXISTS",
    "RESOURCE_ALREADY_EXISTS",
)

# Storage path collisions mean the object was NOT created — never a skip.
_LOCATION_CONFLICT_MARKERS = (
    "LOCATION_OVERLAP",
    "LOCATION_ALREADY_IN_USE",
)

_NOT_FOUND_MARKERS = (
    "NOT_FOUND",
    "DOES NOT EXIST",
    "CANNOT BE FOUND",
    "NOSUCH",
)

# A column mask / row filter is already bound — re-runs treat this as a skip.
_POLICY_EXISTS_MARKERS = (
    "ALREADY HAS",
    "ALREADY ASSIGNED",
    "MASK_ALREADY",
    "ROW_FILTER_ALREADY",
    "ALREADY EXISTS",
    "ALREADY_EXISTS",
)

# The compute cannot apply masks / row filters at all. Single-user (assigned)
# access-mode clusters reject them; serverless or Standard (shared) access mode
# is required. Surface this as MANUAL_ACTION_REQUIRED, never a bare failure.
_POLICY_UNSUPPORTED_MARKERS = (
    "ROW_COLUMN_ACCESS_POLICIES_NOT_SUPPORTED_ON_ASSIGNED_CLUSTERS",
    "NOT SUPPORTED ON ASSIGNED CLUSTERS",
)


def _is_policy_exists_error(message: str) -> bool:
    upper = str(message or "").upper()
    return any(marker in upper for marker in _POLICY_EXISTS_MARKERS)


def _is_policy_unsupported_error(message: str) -> bool:
    upper = str(message or "").upper()
    return any(marker in upper for marker in _POLICY_UNSUPPORTED_MARKERS)


# Guidance emitted when the compute cannot apply policies.
_POLICY_COMPUTE_HINT = (
    "Column masks / row filters are not supported on single-user (assigned) "
    "access-mode clusters. Re-run the import on serverless or a Standard "
    "(shared) access-mode cluster to apply them."
)


_MANUAL_OBJECT_TYPES = {
    "STORAGE_CREDENTIAL",
    "SERVICE_CREDENTIAL",
    "CONNECTION",
    "SHARE",
    "RECIPIENT",
    "PROVIDER",
}

# DESCRIBE variants used to prove an object really exists after a skip.
_DESCRIBE_COMMANDS = {
    "CATALOG": "DESCRIBE CATALOG",
    "SCHEMA": "DESCRIBE SCHEMA",
    "VOLUME": "DESCRIBE VOLUME",
    "EXTERNAL_VOLUME": "DESCRIBE VOLUME",
    "FUNCTION": "DESCRIBE FUNCTION",
    "EXTERNAL_LOCATION": "DESCRIBE EXTERNAL LOCATION",
}


def quote_full_name(full_name: str) -> str:
    return ".".join(f"`{part}`" for part in str(full_name).split(".") if part)


def _normalize_create_statement(statement: str) -> str:
    """Inject IF NOT EXISTS / OR REPLACE for idempotent CREATE_OR_SKIP imports."""

    text = statement.strip()
    if not text:
        return text
    upper = text.upper()
    if not upper.startswith("CREATE"):
        return text
    if " IF NOT EXISTS " in upper or upper.startswith("CREATE OR REPLACE"):
        return text

    # Views / metric views / functions: prefer OR REPLACE for re-runs.
    for kind in (
        "VIEW",
        "TEMPORARY VIEW",
        "MATERIALIZED VIEW",
        "FUNCTION",
    ):
        token = f"CREATE {kind} "
        if upper.startswith(token):
            return "CREATE OR REPLACE " + text[len("CREATE ") :]

    # Catalogs / schemas / tables / volumes / locations / credentials.
    match = re.match(
        r"(?is)^(CREATE\s+(?:EXTERNAL\s+)?(?:CATALOG|SCHEMA|TABLE|VOLUME|"
        r"EXTERNAL\s+LOCATION|STORAGE\s+CREDENTIAL|SERVICE\s+CREDENTIAL|"
        r"CONNECTION)\s+)(.+)$",
        text,
    )
    if match:
        return f"{match.group(1)}IF NOT EXISTS {match.group(2)}"
    return text


def _is_already_exists_error(message: str) -> bool:
    upper = str(message or "").upper()
    if any(marker in upper for marker in _LOCATION_CONFLICT_MARKERS):
        return False
    return any(marker in upper for marker in _ALREADY_EXISTS_MARKERS)


def _is_location_conflict_error(message: str) -> bool:
    upper = str(message or "").upper()
    return any(marker in upper for marker in _LOCATION_CONFLICT_MARKERS)


def _is_not_found_error(message: str) -> bool:
    upper = str(message or "").upper()
    return any(marker in upper for marker in _NOT_FOUND_MARKERS)


# Governance prerequisites owned outside this utility: the governed-tag
# definition must exist at account level, its value must be allowed, and any
# referenced mask/filter function must exist on the target.
_GOVERNANCE_PREREQ_MARKERS = (
    "UC_INVALID_POLICY_CONDITION",
    "UNKNOWN TAG POLICY KEY",
    "INVALID_PARAMETER_VALUE",
    "TAG_POLICY",
    "ROUTINE_NOT_FOUND",
    "FUNCTION_NOT_FOUND",
    "CANNOT BE RESOLVED",
)


def _is_governance_prereq_error(message: str) -> bool:
    upper = str(message or "").upper()
    return any(marker in upper for marker in _GOVERNANCE_PREREQ_MARKERS)


def rewrite_catalog_references(sql: str, mapping: dict[str, str]) -> str:
    """Rewrite catalog references source->target in a SQL statement.

    Handles the three ways a catalog name appears: backticked (```src```),
    as a qualifier (``src.schema.table``), and standalone (``CREATE CATALOG src``,
    ``ON CATALOG src``, ``USE CATALOG src``). Catalog names are long/unique, so a
    word-boundary replace is safe for the bundle's DDL/GRANT/ALTER/POLICY text
    (which carries no table data — this is a governance/structure migration).
    """
    for src, tgt in (mapping or {}).items():
        if not src or not tgt or src == tgt:
            continue
        sql = sql.replace(f"`{src}`", f"`{tgt}`")
        sql = re.sub(rf"(?<![\w`.]){re.escape(src)}(?=\.)", tgt, sql)      # src.<rest>
        sql = re.sub(rf"(?<![\w`.]){re.escape(src)}(?![\w`.])", tgt, sql)  # bare
    return sql


class _CatalogRewritingExecutor:
    """Wraps a SQL executor so every executed statement is catalog-rewritten.

    Applied only when a catalog mapping is supplied, so all import phases (DDL,
    grants, tags, ABAC, policies, and USE-CATALOG context) replay the bundle under
    the target catalog name. Non-``execute`` attributes delegate to the inner
    executor.
    """

    def __init__(self, inner: Any, mapping: dict[str, str]):
        self._inner = inner
        self._mapping = mapping

    def execute(self, sql: str) -> Any:
        return self._inner.execute(rewrite_catalog_references(sql, self._mapping))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class PackageImportEngine:
    """Execute CREATE/GRANT SQL from export_migrated_staging as source of truth."""

    def __init__(
        self,
        package_root: str,
        sql_executor: Any,
        *,
        dry_run: bool = False,
        apply_grants: bool = True,
        catalog_mapping: Optional[dict[str, str]] = None,
        toggles: Optional[dict[str, bool]] = None,
        workspace_client: Any = None,
        select_tables: Optional[Iterable[str]] = None,
        object_locations: Optional[ObjectLocations] = None,
        abac_sql_executor: Any = None,
    ):
        self.root = Path(package_root)
        self.workspace_client = workspace_client
        self.dry_run = dry_run
        self.apply_grants = apply_grants
        self.catalog_mapping = dict(catalog_mapping or {})
        # When a catalog mapping is supplied, every executed statement has its
        # catalog references rewritten source->target (so the bundle — captured
        # under source names — is replayed under the target catalog name). Wrap
        # the executor once so all phases (DDL, grants, tags, ABAC, policies, USE
        # CATALOG context) are covered uniformly.
        self.sql = (
            _CatalogRewritingExecutor(sql_executor, self.catalog_mapping)
            if self.catalog_mapping else sql_executor
        )
        # ABAC `CREATE POLICY` is rejected at parse on a classic Spark cluster and
        # only accepted on a SQL warehouse, so the ABAC phase runs on a dedicated
        # warehouse executor when supplied. Wrapped for catalog rewriting like the
        # main executor. When absent, an ABAC-carrying bundle fails fast
        # (ABAC_WAREHOUSE_REQUIRED) rather than leaving matched tables unprotected.
        self.abac_sql = (
            _CatalogRewritingExecutor(abac_sql_executor, self.catalog_mapping)
            if (abac_sql_executor is not None and self.catalog_mapping)
            else abac_sql_executor
        )
        # Views over masked / row-filtered tables are rejected on a classic Spark
        # cluster but succeed on a SQL warehouse (the mask/filter is bound to the
        # base table and evaluated on read, including through the view). So the
        # view-creation phase runs on the warehouse executor when one is supplied
        # (reuse the ABAC warehouse); otherwise it falls back to the main executor.
        self.warehouse_sql = self.abac_sql
        # Optional import-time TABLE scope filter (catalog/schema scoping is done
        # upstream at inventory via the `catalogs`/`schemas` selection). Empty =
        # import every table. Only table-like securables are narrowed; the
        # catalogs/schemas/functions/volumes a selected table needs still flow.
        self._sel_tables = {s for s in (select_tables or []) if s}
        # create_*/apply_* gates (default all-on). create_* gate object creation;
        # apply_* gate governance. apply_grants kw is kept for back-compat.
        self.toggles = {**(toggles or {})}
        if "apply_grants" not in self.toggles:
            self.toggles["apply_grants"] = apply_grants
        self.apply_grants = self.toggles["apply_grants"]
        self._context: tuple[str, str] = ("", "")
        # The warehouse executor is a SEPARATE SQL session, so it keeps its own
        # USE CATALOG / USE SCHEMA context independent of the main executor's.
        self._wh_context: tuple[str, str] = ("", "")
        # Ownership (`ALTER … OWNER TO`) transfers are deferred to a final phase so
        # the run principal keeps CREATE/MODIFY on each securable while it is still
        # building children and applying governance. Each entry is
        # (object_type, target_full_name, statement).
        self._deferred_owner: list[tuple[str, str, str]] = []
        self._ownership_transferred = 0
        self._ownership_skipped = 0
        # Explicit per-object target locations (schema MANAGED LOCATION / external
        # table+volume LOCATION) from the optional object-locations config.
        self.object_locations = object_locations
        # Existing-catalog (Mode B): set when the mapped target catalog already
        # exists, so storage-credential / external-location / catalog creation are
        # skipped (they are prerequisites) and only the contents are replicated.
        self._existing_catalog_mode = False
        # Fail-closed governance bookkeeping (see run()).
        #  _created_tables: target_full_name -> the create PackageImportResult for
        #    every table THIS run created (an empty shell — droppable). Pre-existing
        #    (SKIP_EXISTING) tables are never recorded, so they are never dropped.
        #  _failed_tables: target_full_name -> the governance feature that failed,
        #    populated by the tag / ABAC phases; the drop sweep drops each one and
        #    mutates its create result to FAILURE (PROTECTION_FAILED) in place.
        self._created_tables: dict[str, PackageImportResult] = {}
        self._failed_tables: dict[str, str] = {}
        #  _created_objects: target_full_name -> the create PackageImportResult for
        #    EVERY object this run touched (any type). A governed-tag failure on a
        #    non-table securable (catalog/schema/volume/view) marks that object's
        #    result FAILURE in place (no drop — dropping a catalog/schema is
        #    destructive and it may hold succeeded children), so the failure shows
        #    in the per-object count exactly like a dropped table does.
        #  _failed_objects: target_full_name -> the governance feature that failed,
        #    for those non-table securables.
        self._created_objects: dict[str, PackageImportResult] = {}
        self._failed_objects: dict[str, str] = {}

    # Object type → the create_* toggle that gates its creation.
    _CREATE_TOGGLE_FOR_TYPE = {
        "STORAGE_CREDENTIAL": "create_storage_credentials",
        "EXTERNAL_LOCATION": "create_external_locations",
        "CATALOG": "create_catalogs",
        "SCHEMA": "create_schemas",
        "VOLUME": "create_volumes",
        "EXTERNAL_VOLUME": "create_volumes",
        "FUNCTION": "create_functions",
        "TABLE": "create_tables",
        "EXTERNAL_TABLE": "create_tables",
        "MATERIALIZED_VIEW": "create_tables",
        "STREAMING_TABLE": "create_tables",
        "VIEW": "create_views",
        "DYNAMIC_VIEW": "create_views",
        "METRIC_VIEW": "create_views",
    }

    def _create_enabled(self, object_type: str) -> bool:
        toggle = self._CREATE_TOGGLE_FOR_TYPE.get(object_type)
        return self.toggles.get(toggle, True) if toggle else True

    # Table-like securables the tables filter narrows (everything else in an
    # in-scope schema — functions, volumes — still comes along).
    _TABLE_LIKE_TYPES = {
        "TABLE", "EXTERNAL_TABLE", "VIEW", "DYNAMIC_VIEW",
        "METRIC_VIEW", "MATERIALIZED_VIEW", "STREAMING_TABLE",
    }

    def _in_scope(self, object_type: str, full_name: str) -> bool:
        """Is this object within the optional import TABLE filter?

        Catalog/schema scoping is done upstream at inventory (the ``catalogs`` /
        ``schemas`` selection), so this narrows only table-like securables. A
        blank filter imports everything; when set, the catalogs, schemas,
        functions, and volumes a selected table depends on still flow through
        (only other tables/views are excluded). Names accept the fully-qualified
        or the bare table name.
        """
        if not self._sel_tables:
            return True
        if object_type not in self._TABLE_LIKE_TYPES:
            return True
        parts = str(full_name or "").split(".")
        table = parts[-1] if parts else ""
        return full_name in self._sel_tables or table in self._sel_tables

    def _create_storage_credential_via_rest(
        self, statements: list[str]
    ) -> tuple[str, str]:
        """Create an MI storage credential over REST (CREATE STORAGE CREDENTIAL
        is not valid SQL). Idempotent: an existing credential is a skip."""

        ddl = "\n".join(statements)
        name_m = re.search(
            r"STORAGE\s+CREDENTIAL\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([^`\s]+)`?",
            ddl, re.IGNORECASE,
        )
        conn_m = re.search(
            r"ACCESS_CONNECTOR_ID\s*=\s*'([^']+)'", ddl, re.IGNORECASE
        )
        if not (name_m and conn_m):
            return "MANUAL_ACTION_REQUIRED", "could not parse storage-credential DDL"
        name = name_m.group(1)
        body = {
            "name": name,
            "azure_managed_identity": {"access_connector_id": conn_m.group(1)},
            "comment": "created by UC governance migration",
        }
        try:
            self.workspace_client.post(
                "/api/2.1/unity-catalog/storage-credentials", body
            )
            return "SUCCESS", f"created storage credential {name} (REST)"
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if _is_already_exists_error(msg) or "already exists" in msg.lower():
                return "SUCCESS", f"storage credential {name} already exists"
            return "MANUAL_ACTION_REQUIRED", f"REST create failed: {msg[:300]}"

    def run(self) -> list[PackageImportResult]:
        if not self.root.exists():
            raise FileNotFoundError(
                f"Migrated export package not found: {self.root}"
            )
        inventory = self._load_inventory()
        self._maybe_enter_existing_catalog_mode()
        by_target = {
            str(row.get("target_full_name") or ""): row
            for row in inventory.values()
            if row.get("target_full_name")
        }
        ddl_files = sorted(
            [
                path
                for path in (self.root / "ddl").glob("*.sql")
                if path.is_file() and not path.name.startswith("all_")
            ],
            key=lambda path: (
                _type_rank(_parse_sql_filename(path.name)[0]),
                path.name,
            ),
        )
        results: list[PackageImportResult] = []
        self._created_tables = {}
        self._failed_tables = {}
        self._created_objects = {}
        self._failed_objects = {}
        # ABAC scope metadata (file stem -> policy full name + on-securable), so an
        # ABAC failure can be mapped to the table(s) the policy protects for the
        # fail-closed drop sweep.
        abac_meta = self._abac_meta_by_stem(inventory)

        # Views/matviews/streaming tables are created AFTER governance + the drop
        # sweep. Everything else (creds, locations, catalogs, schemas, volumes,
        # FUNCTIONS, then tables) is created first — functions before tables (rank),
        # so a table's inline mask/row-filter clause resolves.
        structural = [
            p for p in ddl_files
            if _parse_sql_filename(p.name)[0] not in _VIEW_LIKE_TYPES
        ]
        view_files = [
            p for p in ddl_files
            if _parse_sql_filename(p.name)[0] in _VIEW_LIKE_TYPES
        ]

        # Phase 1 — structure + full table definitions. Inline classic masks / row
        # filters make protection atomic: a missing mask/filter function fails the
        # CREATE TABLE itself, so no unprotected table survives.
        for path in structural:
            results.append(
                self._import_ddl_file(path, len(results) + 1, inventory, by_target)
            )

        # Phase 2 — governed tags on non-view objects. A tag failure on a TABLE
        # records that table for the drop sweep (fail-closed).
        if self.toggles.get("apply_tags", True):
            results.extend(self._apply_governance_dir(
                "tags", "APPLY_TAGS", inventory, by_target, len(results),
                type_predicate=lambda ot: ot not in _VIEW_LIKE_TYPES))

        # Phase 3 — ABAC policies, run on the SQL warehouse executor. A failure
        # (including "no warehouse configured" → ABAC_WAREHOUSE_REQUIRED) records
        # every created table the policy matches for the drop sweep.
        if self.toggles.get("create_abac_policies", True):
            results.extend(self._apply_governance_dir(
                "abac", "CREATE_POLICY", inventory, by_target, len(results),
                executor=self.abac_sql, abac_meta=abac_meta))

        # Phase 4 — drop sweep: every table a governance step failed on is dropped
        # and its create result is mutated to FAILURE (PROTECTION_FAILED) in place,
        # so the FAILURE shows in the Tables sheet, Issues sheet, uc_sync_audit and
        # uc_sync_state (all read from this same result list).
        self._drop_failed_tables()

        # Phase 5 — views / matviews, now that governed tables are settled. A view
        # on a dropped table fails naturally (its table is gone). Views run on the
        # warehouse executor when supplied (a classic Spark cluster errors on a
        # CREATE VIEW over a masked/row-filtered base table); otherwise on the main
        # executor. Its DDL, session context, existence probe, and ordinary grants
        # all route to that executor.
        for path in view_files:
            results.append(
                self._import_ddl_file(
                    path, len(results) + 1, inventory, by_target,
                    executor=self.warehouse_sql,
                )
            )

        # Phase 6 — governed tags on view-like objects (their securable now exists).
        if self.toggles.get("apply_tags", True):
            results.extend(self._apply_governance_dir(
                "tags", "APPLY_TAGS", inventory, by_target, len(results),
                type_predicate=lambda ot: ot in _VIEW_LIKE_TYPES))

        # Phase 7 — fail (without dropping) every non-table securable a governance
        # step failed on, plus any pre-existing table whose governance failed. A
        # governance failure IS an object failure: the object's own create result is
        # flipped to FAILURE in place so it shows in the per-object count, the Issues
        # sheet, uc_sync_audit and uc_sync_state — never reported as success. (Fresh
        # table shells were already dropped in Phase 4; catalogs/schemas/volumes/
        # views and data-bearing tables are never dropped.)
        self._mark_ungoverned_objects()

        # Ownership transfers run dead last, once every object exists and all
        # governance is applied, so the run principal never loses privileges it
        # still needs mid-run.
        self._apply_deferred_ownership()
        return results

    def _import_ddl_file(
        self,
        path: Path,
        order: int,
        inventory: dict[str, dict[str, Any]],
        by_target: dict[str, dict[str, Any]],
        *,
        executor: Any = None,
    ) -> "PackageImportResult":
        """Create one object from its ``ddl/<name>.sql`` file.

        Records tables THIS run actually creates (a fresh ``CREATE_OR_SKIP`` on a
        ``TABLE``/``EXTERNAL_TABLE``) in ``self._created_tables`` so the fail-closed
        drop sweep can undo them if their governance later fails.

        ``executor`` overrides the SQL executor (the view phase passes the warehouse
        executor so CREATE VIEW over a masked table succeeds); it defaults to the
        main executor. The object's DDL, ``USE`` context, existence probe, and
        ordinary grants all run on that executor.
        """
        exec_sql = executor if executor is not None else self.sql
        object_type, parsed_name = _parse_sql_filename(path.name)
        target_full_name = self._map_name(parsed_name)
        if not self._in_scope(object_type, target_full_name):
            return PackageImportResult(
                object_type=object_type,
                source_full_name=parsed_name,
                target_full_name=target_full_name,
                full_name=parsed_name,
                action="SKIP_FILTERED",
                status="SUCCESS",
                message="excluded by import scope filter",
                dependency_level=_type_rank(object_type),
                import_order=order,
            )
        inventory_row = (
            by_target.get(target_full_name)
            or inventory.get(parsed_name)
            or inventory.get(target_full_name)
            or inventory.get(self._guess_source_name(target_full_name, inventory))
            or {}
        )
        source_full_name = str(
            inventory_row.get("source_full_name")
            or inventory_row.get("full_name")
            or parsed_name
        )
        rank = _type_rank(object_type)
        grants_path = self.root / "grants" / path.name
        result = PackageImportResult(
            object_type=object_type,
            source_full_name=source_full_name,
            target_full_name=target_full_name,
            full_name=source_full_name,
            action="DRY_RUN" if self.dry_run else "CREATE",
            status="PENDING",
            ddl_path=str(path),
            grants_path=str(grants_path) if grants_path.exists() else "",
            dependency_level=rank,
            import_order=order,
            source_definition_hash=str(
                inventory_row.get("source_definition_hash")
                or inventory_row.get("definition_hash")
                or ""
            ),
            source_object_id=str(inventory_row.get("object_id") or ""),
            source_last_modified_at=inventory_row.get("last_modified_at"),
        )
        try:
            sql_text = path.read_text(encoding="utf-8")
            statements = [
                _normalize_create_statement(statement)
                for statement in _split_statements(sql_text)
            ]
            # Apply explicit target locations from the object-locations config
            # (schema MANAGED LOCATION / external LOCATION). An external
            # table/volume with no configured location in existing-catalog mode
            # cannot be placed — skip it with actionable guidance.
            statements, manual_reason = self._apply_object_locations(
                object_type, parsed_name, statements
            )
            if manual_reason and not self.dry_run and self._create_enabled(
                object_type
            ):
                result.status = "MANUAL_ACTION_REQUIRED"
                result.action = "MANUAL"
                result.error_code = "EXTERNAL_LOCATION_MISSING"
                result.message = manual_reason
                return result
            if not self._create_enabled(object_type):
                # create_*=false: the object is assumed to already exist on
                # target; skip creation but still (later) govern it. Grants
                # are still applied so existing objects get their ACLs.
                result.status = "SUCCESS"
                result.action = "SKIP_CREATE_DISABLED"
                if not self.dry_run:
                    grant_warning = self._apply_grants_file(
                        grants_path, object_type, target_full_name,
                        executor=executor,
                    )
                    result.message = (
                        "create disabled by toggle; assumed pre-existing"
                        + (f"; grant warning: {grant_warning}" if grant_warning else "")
                    )
                else:
                    result.message = "create disabled by toggle (dry run)"
                return result
            # MI-based storage credentials carry no secret and can be created
            # from the access-connector id — but CREATE STORAGE CREDENTIAL is
            # not valid SQL, so they go through the UC REST API when a
            # workspace client is available.
            mi_credential = object_type == "STORAGE_CREDENTIAL" and any(
                "AZURE_MANAGED_IDENTITY" in s.upper()
                and "ACCESS_CONNECTOR_ID" in s.upper()
                for s in statements
            )
            if mi_credential and not self.dry_run and self.workspace_client:
                status, msg = self._create_storage_credential_via_rest(statements)
                result.status = status
                result.action = "CREATE" if status == "SUCCESS" else "MANUAL"
                result.message = msg
                if status != "SUCCESS":
                    result.error_code = "STORAGE_CREDENTIAL_REST"
            elif object_type in _MANUAL_OBJECT_TYPES and not self.dry_run:
                # Credential/share DDL is not executable via Spark SQL; without
                # a REST client it is a manual step.
                result.status = "MANUAL_ACTION_REQUIRED"
                result.action = "MANUAL"
                result.error_code = "MANUAL_SQL_OBJECT"
                result.message = (
                    "Object type requires REST/API or admin SQL console; "
                    "DDL retained in migrated package for review."
                )
            elif self.dry_run:
                result.status = "PENDING"
                result.message = f"dry_run statements={len(statements)}"
            else:
                # SHOW CREATE emits two-part names, so without an explicit
                # context they would resolve against the workspace default
                # catalog instead of the mapped target.
                self._apply_context(
                    object_type, target_full_name, executor=executor
                )
                skipped_existing = False
                for statement in statements:
                    try:
                        exec_sql.execute(statement)
                    except Exception as exec_exc:  # noqa: BLE001
                        if _is_already_exists_error(str(exec_exc)):
                            skipped_existing = True
                            continue
                        raise
                if skipped_existing and not self._object_exists(
                    object_type, target_full_name, executor=executor
                ):
                    raise RuntimeError(
                        "create reported an existing object but "
                        f"{target_full_name} is not present in the target"
                    )
                grant_warning = self._apply_grants_file(
                    grants_path, object_type, target_full_name, executor=executor
                )
                if grant_warning and _is_not_found_error(grant_warning):
                    # Grants cannot resolve an object that was never created.
                    raise RuntimeError(
                        f"object missing after create: {grant_warning}"
                    )
                if grant_warning:
                    result.message = f"created; grant warning: {grant_warning}"
                result.status = "SUCCESS"
                result.action = (
                    "SKIP_EXISTING" if skipped_existing else "CREATE_OR_SKIP"
                )
                if not result.message:
                    prefix = "already exists; " if skipped_existing else ""
                    result.message = (
                        prefix + (statements[0][:1000] if statements else "")
                    )
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            exists = self._object_exists(
                object_type, target_full_name, executor=executor
            )
            # Idempotent re-runs: if the mapped securable is already present,
            # treat create conflicts (managed-location / overlap) as skips.
            if exists and (
                object_type in {"CATALOG", "SCHEMA", "EXTERNAL_LOCATION"}
                or _is_location_conflict_error(message)
                or _is_already_exists_error(message)
            ):
                grant_warning = self._apply_grants_file(
                    grants_path, object_type, target_full_name, executor=executor
                )
                result.status = "SUCCESS"
                result.action = "SKIP_EXISTING"
                result.error_code = ""
                result.message = (
                    "already exists; ignored create conflict on re-run"
                    + (f"; grant warning: {grant_warning}" if grant_warning else "")
                )
            else:
                result.status = "FAILURE"
                result.error_code = type(exc).__name__
                result.message = message
                if _is_location_conflict_error(message):
                    result.error_code = "LOCATION_OVERLAP"
                    result.message = (
                        "target storage path is already claimed by another "
                        "securable; supply location_mapping_csv_path so the "
                        f"target uses a distinct path. {message[:1200]}"
                    )
        # Record tables THIS run created (fresh, empty shells) so the fail-closed
        # drop sweep can undo them. A pre-existing (SKIP_EXISTING) table is left
        # untouched — never dropped.
        if (
            object_type in _DROPPABLE_TABLE_TYPES
            and result.status == "SUCCESS"
            and result.action == "CREATE_OR_SKIP"
        ):
            self._created_tables[target_full_name] = result
        # Record every object's create result so a governed-tag failure on a
        # non-table securable can mark it FAILURE in place (see _mark_failed_objects).
        if target_full_name:
            self._created_objects[target_full_name] = result
        return result

    def _abac_meta_by_stem(
        self, inventory: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, str]]:
        """Map each ``abac/<stem>.sql`` file stem to its policy identity + scope.

        Lets an ABAC failure be mapped to the table(s) it protects (drop sweep) and
        gives the ABAC result its real policy full name (``…#policy:name``) so the
        report/audit can key it. Built from the inventory's ABAC_POLICY rows.
        """
        from uc_sync.export import _safe_filename

        meta: dict[str, dict[str, str]] = {}
        for row in inventory.values():
            if row.get("object_type") != ObjectType.ABAC_POLICY.value:
                continue
            full_name = str(row.get("full_name") or row.get("source_full_name") or "")
            if not full_name:
                continue
            stem = _safe_filename(ObjectType.ABAC_POLICY.value, full_name)
            if stem in meta:
                continue
            d = row.get("definition") or {}
            meta[stem] = {
                "full_name": full_name,
                "on_type": str(d.get("on_securable_type") or "").upper(),
                "on_securable": str(d.get("on_securable") or ""),
            }
        return meta

    def _abac_matched_tables(self, meta: Optional[dict[str, str]]) -> list[str]:
        """Created tables the ABAC policy in ``meta`` protects (target-named)."""
        if not meta or not meta.get("on_securable"):
            return []
        on_type = meta.get("on_type", "")
        on_securable = self._map_name(meta["on_securable"])
        return [
            tbl for tbl in self._created_tables
            if _table_in_policy_scope(tbl, on_type, on_securable)
        ]

    def _record_governance_failure(
        self,
        object_type: str,
        target_full_name: str,
        action_label: str,
        meta: Optional[dict[str, str]],
        feature: str,
    ) -> bool:
        """Record the object(s) a failed governance op leaves unprotected.

        A governed-tag failure fails the object it targets, on **every** type:
        * a **table** is recorded for the drop sweep (dropped → FAILURE — fail-closed,
          it is an empty shell this run created, so no data is lost);
        * a **catalog / schema / volume / view** is recorded to be marked FAILURE in
          place (never dropped — that would be destructive and it may hold succeeded
          children).

        For an **ABAC** failure (``CREATE_POLICY``) the ABAC policy is itself an
        object whose own result already carries the FAILURE, so this only records the
        matched table(s) to drop. Returns True if it recorded a table for the drop
        sweep (kept for the caller's error-code choice).
        """
        if action_label == "CREATE_POLICY":
            policy = (meta or {}).get("full_name", "policy")
            recorded = False
            for tbl in self._abac_matched_tables(meta):
                self._failed_tables.setdefault(tbl, f"ABAC {policy} ({feature})")
                recorded = True
            return recorded
        if object_type in _DROPPABLE_TABLE_TYPES:
            self._failed_tables.setdefault(
                target_full_name, f"governed tag ({feature})"
            )
            return True
        # Non-table securable: mark FAILURE in place (no drop).
        self._failed_objects.setdefault(
            target_full_name, f"governed tag ({feature})"
        )
        return False

    def _drop_failed_tables(self) -> None:
        """Drop every table a governance step failed on and flip its create result.

        Fail-closed: a governed table that could not be fully protected must not
        survive. The create ``PackageImportResult`` is mutated to FAILURE in place
        so the outcome reaches the report, audit, and state (all read this list).
        """
        if self.dry_run or not self._failed_tables:
            return
        for target_full_name, feature in self._failed_tables.items():
            create_result = self._created_tables.get(target_full_name)
            if create_result is None:
                # Not a shell this run created (pre-existing, or its own CREATE
                # already failed) — nothing to drop.
                continue
            try:
                self.sql.execute(
                    f"DROP TABLE IF EXISTS {quote_full_name(target_full_name)}"
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[import] fail-closed drop of {target_full_name} raised: "
                    f"{str(exc)[:200]}"
                )
            create_result.status = "FAILURE"
            create_result.action = "DROP_PROTECTION_FAILED"
            create_result.error_code = "PROTECTION_FAILED"
            create_result.message = (
                "table dropped (fail-closed): a governance step failed — "
                f"{feature}"
            )

    def _mark_ungoverned_objects(self) -> None:
        """Flip to FAILURE every object a governance step failed on that was NOT
        dropped: catalog / schema / volume / view (never droppable), and any
        pre-existing table (has data — never dropped). A governance failure is an
        object failure, so the object's own result must never read success. The
        create result is mutated in place so it reaches the per-object count, the
        Issues sheet, uc_sync_audit and uc_sync_state.
        """
        if self.dry_run:
            return
        pending: dict[str, str] = dict(self._failed_objects)
        # Pre-existing tables recorded for the (table) drop sweep but never dropped
        # because they were not fresh shells this run created.
        for target, feature in self._failed_tables.items():
            if target not in self._created_tables:
                pending.setdefault(target, feature)
        for target_full_name, feature in pending.items():
            result = self._created_objects.get(target_full_name)
            if result is None or result.status == "FAILURE":
                continue
            result.status = "FAILURE"
            result.action = "GOVERNANCE_FAILED"
            result.error_code = "PROTECTION_FAILED"
            result.message = (
                "object not fully governed (fail-closed): a governance step "
                f"failed — {feature}"
            )

    def _apply_governance_dir(
        self,
        dirname: str,
        action_label: str,
        inventory: dict[str, dict[str, Any]],
        by_target: dict[str, dict[str, Any]],
        start_order: int,
        *,
        executor: Any = None,
        type_predicate: Any = None,
        abac_meta: Optional[dict[str, dict[str, str]]] = None,
    ) -> list["PackageImportResult"]:
        """Apply governed-tag (``tags/``) or ABAC (``abac/``) SQL files.

        Idempotent: an already-assigned tag or already-existing policy is a skip.

        Fail-closed: a failure applying a governed tag to a **table**, or an ABAC
        policy that matches created tables, records those table(s) for the drop
        sweep and marks the op FAILURE (``PROTECTION_FAILED``). A failure on a
        non-table securable whose prerequisite is simply missing keeps the softer
        ``MANUAL_ACTION_REQUIRED`` (the infosec prerequisite is owned elsewhere).

        ``executor`` overrides the SQL executor (ABAC runs on the SQL warehouse);
        ``type_predicate`` filters which object types are processed (views are
        tagged in a later pass, after they are created); ``abac_meta`` marks the
        ABAC phase and carries per-file policy scope for the drop mapping.
        """

        governance_dir = self.root / dirname
        if not governance_dir.exists():
            return []
        files = sorted(
            path for path in governance_dir.glob("*.sql")
            if path.is_file() and not path.name.startswith("all_")
        )
        is_abac = abac_meta is not None
        # ABAC runs ONLY on its dedicated warehouse executor and never falls back
        # to the Spark executor (which rejects CREATE POLICY at parse); without one
        # the bundle fails closed (matched tables dropped), never silently skipped.
        # Tag / classic phases use the main executor when none is supplied.
        exec_sql = executor if is_abac else (
            executor if executor is not None else self.sql
        )
        abac_no_warehouse = is_abac and exec_sql is None
        results: list[PackageImportResult] = []
        offset = 0
        for path in files:
            object_type, parsed_name = _parse_sql_filename(path.name)
            if type_predicate is not None and not type_predicate(object_type):
                continue
            target_full_name = self._map_name(parsed_name)
            if not self._in_scope(object_type, target_full_name):
                continue  # object excluded by the import scope filter
            offset += 1
            meta = abac_meta.get(path.stem) if is_abac else None
            if meta:
                # Key the ABAC result by the real policy full name so the report /
                # audit can resolve it (…on_securable#policy:name).
                source_full_name = meta["full_name"]
                display_name = meta["full_name"]
            else:
                inventory_row = (
                    by_target.get(target_full_name) or inventory.get(parsed_name) or {}
                )
                source_full_name = str(
                    inventory_row.get("source_full_name")
                    or inventory_row.get("full_name")
                    or parsed_name
                )
                display_name = target_full_name
            result = PackageImportResult(
                object_type=object_type,
                source_full_name=source_full_name,
                target_full_name=display_name,
                full_name=source_full_name,
                action="DRY_RUN" if self.dry_run else action_label,
                status="PENDING",
                policies_path=str(path),
                dependency_level=_type_rank(object_type),
                import_order=start_order + offset,
            )
            try:
                statements = _split_statements(path.read_text(encoding="utf-8"))
                if self.dry_run:
                    result.status = "PENDING"
                    result.message = f"dry_run statements={len(statements)}"
                elif abac_no_warehouse:
                    result.status = "FAILURE"
                    result.action = "MANUAL"
                    result.error_code = "ABAC_WAREHOUSE_REQUIRED"
                    result.message = (
                        "ABAC CREATE POLICY requires a SQL warehouse "
                        "(import_warehouse_id); it is rejected on a classic Spark "
                        "cluster. Matched table(s) are dropped fail-closed. Set "
                        "import_warehouse_id and re-run."
                    )
                    self._record_governance_failure(
                        object_type, target_full_name, action_label, meta,
                        "ABAC_WAREHOUSE_REQUIRED",
                    )
                else:
                    # tags/ALTER need session context; ABAC CREATE POLICY is fully
                    # qualified (context is skipped for ABAC_POLICY).
                    self._apply_context(object_type, target_full_name)
                    skipped = False
                    failed = ""
                    for statement in statements:
                        try:
                            exec_sql.execute(statement)
                        except Exception as exec_exc:  # noqa: BLE001
                            message = str(exec_exc)
                            if _is_policy_exists_error(message) or _is_already_exists_error(message):
                                skipped = True
                                continue
                            failed = message
                            break
                    if failed:
                        dropped = self._record_governance_failure(
                            object_type, target_full_name, action_label, meta, failed
                        )
                        if dropped:
                            result.status = "FAILURE"
                            result.action = "MANUAL"
                            result.error_code = "PROTECTION_FAILED"
                            result.message = (
                                "governance failed; protected table(s) dropped "
                                f"fail-closed: {failed[:400]}"
                            )
                        elif _is_governance_prereq_error(failed):
                            result.status = "MANUAL_ACTION_REQUIRED"
                            result.action = "MANUAL"
                            result.error_code = "GOVERNANCE_PREREQ_MISSING"
                            result.message = (
                                "Governed-tag definition or referenced function is "
                                f"missing on the target: {failed[:400]}"
                            )
                        else:
                            result.status = "FAILURE"
                            result.error_code = "GOVERNANCE_FAILED"
                            result.message = failed[:600]
                    else:
                        result.status = "SUCCESS"
                        result.action = "SKIP_EXISTING" if skipped else action_label
                        result.message = (
                            ("already applied; " if skipped else "")
                            + (statements[0][:500] if statements else "")
                        )
            except Exception as exc:  # noqa: BLE001
                result.status = "FAILURE"
                result.error_code = type(exc).__name__
                result.message = str(exc)
            results.append(result)
        return results

    def _apply_grants_file(
        self,
        grants_path: Path,
        object_type: str = "",
        target_full_name: str = "",
        *,
        executor: Any = None,
    ) -> str:
        """Run a grants file; return the last warning, if any.

        Ordinary ``GRANT … TO`` statements run inline (on ``executor`` when given —
        e.g. a view's grants run on the warehouse executor that created it — else on
        the main executor). ``ALTER … OWNER TO`` transfers are **queued** for a final
        ownership phase instead of running here, so the run principal keeps
        CREATE/MODIFY on the securable while it is still building children and
        applying governance (handing an external location or catalog to its source
        owner mid-run would strip the very privileges the next create/ALTER needs —
        the CREATE MANAGED STORAGE bug).
        """
        if not (self.apply_grants and grants_path.exists()):
            return ""
        exec_sql = executor if executor is not None else self.sql
        warning = ""
        for statement in _split_statements(
            grants_path.read_text(encoding="utf-8")
        ):
            if _is_owner_statement(statement):
                self._deferred_owner.append(
                    (object_type, target_full_name, statement)
                )
                continue
            try:
                exec_sql.execute(statement)
            except Exception as grant_exc:  # noqa: BLE001
                warning = str(grant_exc)
        return warning

    def _apply_deferred_ownership(self) -> None:
        """Run queued ``OWNER TO`` transfers, after all creates + governance.

        A source owner that does not exist on the target is expected in a region
        move, so a failed transfer is a **warning**, not a failure — the object
        simply stays owned by the run principal. Skipped in dry-run.
        """
        if self.dry_run or not self._deferred_owner:
            return
        for _object_type, _target, statement in self._deferred_owner:
            try:
                self.sql.execute(statement)
                self._ownership_transferred += 1
            except Exception as exc:  # noqa: BLE001
                self._ownership_skipped += 1
                print(
                    "[import] ownership not transferred (left with the run "
                    f"principal): {str(exc)[:300]} :: {statement[:200]}"
                )
        print(
            f"[import] ownership phase: {self._ownership_transferred} transferred, "
            f"{self._ownership_skipped} left with the run principal "
            "(owner missing on target or not permitted)."
        )

    def _maybe_enter_existing_catalog_mode(self) -> None:
        """Existing-catalog (Mode B) auto-detection.

        When a catalog mapping is supplied and the mapped **target** catalog(s)
        already exist on the target metastore, the catalog + its storage credential
        + external location are treated as prerequisites the user created: their
        creation is skipped and only the contents (schemas, tables, views,
        functions, volumes, governance) are replicated. If the target catalog does
        not exist, nothing changes and the utility creates everything from the
        mapping CSV as before (Mode A).

        Detected only in a real import (a workspace client is present) — pure-SQL
        unit tests without one keep whatever ``create_*`` toggles they set.
        """
        if self.dry_run or self._existing_catalog_mode:
            return
        if self.workspace_client is None or not self.catalog_mapping:
            return
        targets = sorted({t for t in self.catalog_mapping.values() if t})
        if not targets or not all(
            self._object_exists("CATALOG", target) for target in targets
        ):
            return
        for toggle in (
            "create_catalogs",
            "create_storage_credentials",
            "create_external_locations",
        ):
            self.toggles[toggle] = False
        self._existing_catalog_mode = True
        print(
            "[import] existing-catalog mode: target catalog(s) "
            f"{targets} already present — skipping storage-credential / external-"
            "location / catalog creation; replicating schemas + objects only."
        )

    def _apply_object_locations(
        self, object_type: str, source_full_name: str, statements: list[str]
    ) -> tuple[list[str], str]:
        """Apply configured schema / external-object locations to CREATE statements.

        Returns ``(statements, manual_reason)``. A non-empty ``manual_reason`` means
        the object must be skipped as ``MANUAL_ACTION_REQUIRED`` — an external
        table/volume with no configured location while in existing-catalog mode,
        which cannot be placed (there is no path to reparent onto, by design).
        """
        parts = [part for part in str(source_full_name or "").split(".") if part]
        schema = parts[1] if len(parts) >= 2 else ""
        leaf = parts[-1] if parts else ""
        locations = self.object_locations

        if object_type == "SCHEMA":
            schema_name = parts[-1] if len(parts) >= 2 else leaf
            location = locations.schema_location(schema_name) if locations else None
            if location:
                return (
                    [self._apply_schema_location(s, location) for s in statements],
                    "",
                )
            return statements, ""

        if object_type == "EXTERNAL_VOLUME":
            location = locations.volume_location(schema, leaf) if locations else None
            if location:
                return (
                    [self._replace_location_literal(s, location) for s in statements],
                    "",
                )
            if self._existing_catalog_mode:
                return statements, (
                    f"external volume {source_full_name} has no target location; "
                    f"add a row '{schema},{leaf},,<location>' to the object-locations "
                    "file (its external location must already exist on target)"
                )
            return statements, ""

        if object_type == "EXTERNAL_TABLE":
            location = locations.table_location(schema, leaf) if locations else None
            if location:
                return (
                    [self._replace_location_literal(s, location) for s in statements],
                    "",
                )
            if self._existing_catalog_mode:
                return statements, (
                    f"external table {source_full_name} has no target location; "
                    f"add a row '{schema},,{leaf},<location>' to the object-locations "
                    "file (its external location must already exist on target)"
                )
            return statements, ""

        return statements, ""

    def _apply_schema_location(self, statement: str, location: str) -> str:
        """Inject (or replace) a schema's ``MANAGED LOCATION`` from config."""

        escaped = str(location).replace("'", "''")
        stripped = re.sub(
            r"\s+MANAGED\s+LOCATION\s+'[^']*'", "", statement, flags=re.IGNORECASE
        )
        clause = f" MANAGED LOCATION '{escaped}'"
        idx = stripped.upper().find(" COMMENT ")
        if idx != -1:
            return stripped[:idx] + clause + stripped[idx:]
        trimmed = stripped.rstrip()
        if trimmed.endswith(";"):
            return trimmed[:-1] + clause + ";"
        return trimmed + clause

    @staticmethod
    def _replace_location_literal(statement: str, location: str) -> str:
        """Replace the first ``LOCATION '…'`` literal with the configured path."""

        escaped = str(location).replace("'", "''")
        return re.sub(
            r"(LOCATION\s+')[^']*(')",
            lambda match: f"{match.group(1)}{escaped}{match.group(2)}",
            statement,
            count=1,
            flags=re.IGNORECASE,
        )

    def _apply_context(
        self, object_type: str, target_full_name: str, *, executor: Any = None
    ) -> None:
        parts = str(target_full_name or "").split(".")
        catalog = parts[0] if len(parts) > 1 else ""
        schema = parts[1] if len(parts) > 2 else ""
        # ABAC CREATE POLICY is fully qualified, so it needs no USE context. Worse,
        # its target_full_name is the policy full name (…#policy:name), whose
        # decoded middle segment is a literal ``policy`` — USE SCHEMA on it raised
        # SCHEMA_NOT_FOUND. Skip context for ABAC entirely.
        if object_type in {
            "CATALOG", "STORAGE_CREDENTIAL", "EXTERNAL_LOCATION", "ABAC_POLICY",
        }:
            return
        if not catalog:
            return
        # The warehouse executor is a distinct SQL session with its own USE context,
        # so track (and compare against) a separate cache — never the main session's.
        exec_sql = executor if executor is not None else self.sql
        on_warehouse = executor is not None and executor is self.warehouse_sql
        cache = self._wh_context if on_warehouse else self._context
        if (catalog, schema) == cache:
            return
        exec_sql.execute(f"USE CATALOG `{catalog}`")
        if schema:
            exec_sql.execute(f"USE SCHEMA `{schema}`")
        if on_warehouse:
            self._wh_context = (catalog, schema)
        else:
            self._context = (catalog, schema)

    def _object_exists(
        self, object_type: str, target_full_name: str, *, executor: Any = None
    ) -> bool:
        """Best-effort existence probe; only a NOT_FOUND error proves absence."""
        if not target_full_name:
            return True
        exec_sql = executor if executor is not None else self.sql
        command = _DESCRIBE_COMMANDS.get(object_type, "DESCRIBE TABLE")
        try:
            exec_sql.execute(f"{command} {quote_full_name(target_full_name)}")
            return True
        except Exception as exc:  # noqa: BLE001
            return not _is_not_found_error(str(exc))

    def _map_name(self, full_name: str) -> str:
        name = str(full_name or "")
        if not name or not self.catalog_mapping:
            return name
        if "." not in name:
            return self.catalog_mapping.get(name, name)
        catalog, _, rest = name.partition(".")
        target = self.catalog_mapping.get(catalog)
        if not target:
            return name
        return f"{target}.{rest}" if rest else target

    def _load_inventory(self) -> dict[str, dict[str, Any]]:
        inventory_path = self.root / "inventory" / "objects.json"
        if not inventory_path.exists():
            return {}
        rows = json.loads(inventory_path.read_text(encoding="utf-8"))
        by_name: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in ("source_full_name", "full_name", "target_full_name"):
                name = str(row.get(key) or "")
                if name and name not in by_name:
                    by_name[name] = row
        return by_name

    @staticmethod
    def _guess_source_name(
        target_full_name: str, inventory: dict[str, dict[str, Any]]
    ) -> str:
        suffix = target_full_name.partition(".")[2]
        if not suffix:
            return target_full_name
        for source_name, row in inventory.items():
            candidate = str(
                row.get("source_full_name") or row.get("full_name") or source_name
            )
            if candidate.endswith(f".{suffix}") or candidate == suffix:
                return candidate
        return target_full_name
