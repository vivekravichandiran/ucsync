"""Import Unity Catalog objects from a migrated export package."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from uc_sync.dependency import _TYPE_RANK
from uc_sync.models import ObjectType


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
    ):
        self.root = Path(package_root)
        self.sql = sql_executor
        self.dry_run = dry_run
        self.apply_grants = apply_grants
        self.catalog_mapping = dict(catalog_mapping or {})
        # create_*/apply_* gates (default all-on). create_* gate object creation;
        # apply_* gate governance. apply_grants kw is kept for back-compat.
        self.toggles = {**(toggles or {})}
        if "apply_grants" not in self.toggles:
            self.toggles["apply_grants"] = apply_grants
        self.apply_grants = self.toggles["apply_grants"]
        self._context: tuple[str, str] = ("", "")

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

    def run(self) -> list[PackageImportResult]:
        if not self.root.exists():
            raise FileNotFoundError(
                f"Migrated export package not found: {self.root}"
            )
        inventory = self._load_inventory()
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
        for order, path in enumerate(ddl_files, start=1):
            object_type, parsed_name = _parse_sql_filename(path.name)
            target_full_name = self._map_name(parsed_name)
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
                if not self._create_enabled(object_type):
                    # create_*=false: the object is assumed to already exist on
                    # target; skip creation but still (later) govern it. Grants
                    # are still applied so existing objects get their ACLs.
                    result.status = "SUCCESS"
                    result.action = "SKIP_CREATE_DISABLED"
                    if not self.dry_run:
                        grant_warning = self._apply_grants_file(grants_path)
                        result.message = (
                            "create disabled by toggle; assumed pre-existing"
                            + (f"; grant warning: {grant_warning}" if grant_warning else "")
                        )
                    else:
                        result.message = "create disabled by toggle (dry run)"
                    results.append(result)
                    continue
                # MI-based storage credentials carry no secret and CAN be created
                # from the access-connector id, so they are executed (not MANUAL).
                mi_credential = object_type == "STORAGE_CREDENTIAL" and any(
                    "AZURE_MANAGED_IDENTITY" in s.upper()
                    and "ACCESS_CONNECTOR_ID" in s.upper()
                    for s in statements
                )
                if (
                    object_type in _MANUAL_OBJECT_TYPES
                    and not mi_credential
                    and not self.dry_run
                ):
                    # Credential/share DDL is often not executable via Spark SQL.
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
                    self._apply_context(object_type, target_full_name)
                    skipped_existing = False
                    for statement in statements:
                        try:
                            self.sql.execute(statement)
                        except Exception as exec_exc:  # noqa: BLE001
                            if _is_already_exists_error(str(exec_exc)):
                                skipped_existing = True
                                continue
                            raise
                    if skipped_existing and not self._object_exists(
                        object_type, target_full_name
                    ):
                        raise RuntimeError(
                            "create reported an existing object but "
                            f"{target_full_name} is not present in the target"
                        )
                    grant_warning = self._apply_grants_file(grants_path)
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
                exists = self._object_exists(object_type, target_full_name)
                # Idempotent re-runs: if the mapped securable is already present,
                # treat create conflicts (managed-location / overlap) as skips.
                if exists and (
                    object_type in {"CATALOG", "SCHEMA", "EXTERNAL_LOCATION"}
                    or _is_location_conflict_error(message)
                    or _is_already_exists_error(message)
                ):
                    grant_warning = self._apply_grants_file(grants_path)
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
            results.append(result)
        # Governance phases (after every object exists): governed tags, then ABAC
        # policies, then classic mask / row-filter bindings — each toggle-gated.
        if self.toggles.get("apply_tags", True):
            results.extend(self._apply_governance_dir(
                "tags", "APPLY_TAGS", inventory, by_target, len(results)))
        if self.toggles.get("create_abac_policies", True):
            results.extend(self._apply_governance_dir(
                "abac", "CREATE_POLICY", inventory, by_target, len(results)))
        if self.toggles.get("apply_masks_row_filters", True):
            results.extend(self._apply_policy_files(inventory, by_target, len(results)))
        return results

    def _apply_governance_dir(
        self,
        dirname: str,
        action_label: str,
        inventory: dict[str, dict[str, Any]],
        by_target: dict[str, dict[str, Any]],
        start_order: int,
    ) -> list["PackageImportResult"]:
        """Apply governed-tag (``tags/``) or ABAC (``abac/``) SQL files.

        Idempotent: an already-assigned tag or already-existing policy is a skip.
        A missing governed-tag definition or referenced function surfaces as
        MANUAL_ACTION_REQUIRED (the infosec prerequisite is owned elsewhere).
        """

        governance_dir = self.root / dirname
        if not governance_dir.exists():
            return []
        files = sorted(
            path for path in governance_dir.glob("*.sql")
            if path.is_file() and not path.name.startswith("all_")
        )
        results: list[PackageImportResult] = []
        for offset, path in enumerate(files, start=1):
            object_type, parsed_name = _parse_sql_filename(path.name)
            target_full_name = self._map_name(parsed_name)
            inventory_row = (
                by_target.get(target_full_name) or inventory.get(parsed_name) or {}
            )
            source_full_name = str(
                inventory_row.get("source_full_name")
                or inventory_row.get("full_name")
                or parsed_name
            )
            result = PackageImportResult(
                object_type=object_type,
                source_full_name=source_full_name,
                target_full_name=target_full_name,
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
                else:
                    # tags/ALTER need session context; abac CREATE POLICY is fully
                    # qualified but context is harmless.
                    self._apply_context(object_type, target_full_name)
                    skipped = False
                    manual = ""
                    for statement in statements:
                        try:
                            self.sql.execute(statement)
                        except Exception as exec_exc:  # noqa: BLE001
                            message = str(exec_exc)
                            if _is_policy_exists_error(message) or _is_already_exists_error(message):
                                skipped = True
                                continue
                            if _is_governance_prereq_error(message):
                                manual = message
                                break
                            raise
                    if manual:
                        result.status = "MANUAL_ACTION_REQUIRED"
                        result.action = "MANUAL"
                        result.error_code = "GOVERNANCE_PREREQ_MISSING"
                        result.message = (
                            "Governed-tag definition or referenced function is "
                            f"missing on the target: {manual[:400]}"
                        )
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

    def _apply_policy_files(
        self,
        inventory: dict[str, dict[str, Any]],
        by_target: dict[str, dict[str, Any]],
        start_order: int,
    ) -> list["PackageImportResult"]:
        """Apply column masks / row filters after every object is created.

        Runs as a dedicated late phase so the referenced mask/filter functions
        (created after tables) and the tables themselves already exist. Each
        ``policies/<name>.sql`` holds ``ALTER TABLE`` binding statements; re-runs
        treat an already-bound policy as a skip.
        """

        policy_dir = self.root / "policies"
        if not policy_dir.exists():
            return []
        policy_files = sorted(
            path
            for path in policy_dir.glob("*.sql")
            if path.is_file() and not path.name.startswith("all_")
        )
        results: list[PackageImportResult] = []
        for offset, path in enumerate(policy_files, start=1):
            object_type, parsed_name = _parse_sql_filename(path.name)
            target_full_name = self._map_name(parsed_name)
            inventory_row = (
                by_target.get(target_full_name)
                or inventory.get(parsed_name)
                or inventory.get(target_full_name)
                or {}
            )
            source_full_name = str(
                inventory_row.get("source_full_name")
                or inventory_row.get("full_name")
                or parsed_name
            )
            result = PackageImportResult(
                object_type=object_type,
                source_full_name=source_full_name,
                target_full_name=target_full_name,
                full_name=source_full_name,
                action="DRY_RUN" if self.dry_run else "APPLY_POLICY",
                status="PENDING",
                policies_path=str(path),
                dependency_level=_type_rank(object_type),
                import_order=start_order + offset,
            )
            try:
                statements = _split_statements(path.read_text(encoding="utf-8"))
                if self.dry_run:
                    result.status = "PENDING"
                    result.message = f"dry_run policy statements={len(statements)}"
                else:
                    self._apply_context(object_type, target_full_name)
                    skipped_existing = False
                    unsupported = ""
                    for statement in statements:
                        try:
                            self.sql.execute(statement)
                        except Exception as exec_exc:  # noqa: BLE001
                            message = str(exec_exc)
                            if _is_policy_exists_error(message):
                                skipped_existing = True
                                continue
                            if _is_policy_unsupported_error(message):
                                unsupported = message
                                break
                            raise
                    if unsupported:
                        result.status = "MANUAL_ACTION_REQUIRED"
                        result.action = "MANUAL"
                        result.error_code = "POLICY_COMPUTE_UNSUPPORTED"
                        result.message = f"{_POLICY_COMPUTE_HINT} {unsupported[:400]}"
                    else:
                        result.status = "SUCCESS"
                        result.action = (
                            "SKIP_EXISTING" if skipped_existing else "APPLY_POLICY"
                        )
                        prefix = "already applied; " if skipped_existing else ""
                        result.message = prefix + (
                            statements[0][:1000] if statements else ""
                        )
            except Exception as exc:  # noqa: BLE001
                result.status = "FAILURE"
                result.error_code = type(exc).__name__
                result.message = str(exc)
            results.append(result)
        return results

    def _apply_grants_file(self, grants_path: Path) -> str:
        """Run grant/owner statements; return the last warning, if any."""
        if not (self.apply_grants and grants_path.exists()):
            return ""
        warning = ""
        for statement in _split_statements(
            grants_path.read_text(encoding="utf-8")
        ):
            try:
                self.sql.execute(statement)
            except Exception as grant_exc:  # noqa: BLE001
                warning = str(grant_exc)
        return warning

    def _apply_context(self, object_type: str, target_full_name: str) -> None:
        parts = str(target_full_name or "").split(".")
        catalog = parts[0] if len(parts) > 1 else ""
        schema = parts[1] if len(parts) > 2 else ""
        if object_type in {"CATALOG", "STORAGE_CREDENTIAL", "EXTERNAL_LOCATION"}:
            return
        if (catalog, schema) == self._context or not catalog:
            return
        self.sql.execute(f"USE CATALOG `{catalog}`")
        if schema:
            self.sql.execute(f"USE SCHEMA `{schema}`")
        self._context = (catalog, schema)

    def _object_exists(self, object_type: str, target_full_name: str) -> bool:
        """Best-effort existence probe; only a NOT_FOUND error proves absence."""
        if not target_full_name:
            return True
        command = _DESCRIBE_COMMANDS.get(object_type, "DESCRIBE TABLE")
        try:
            self.sql.execute(f"{command} {quote_full_name(target_full_name)}")
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
