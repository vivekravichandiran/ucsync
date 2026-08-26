"""SQL identity quoting, GRANT DDL, and CREATE DDL helpers for export."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

from uc_sync.models import ObjectType, UCObject

# Prefer live SHOW CREATE for these types when a SQL executor is available.
SHOW_CREATE_TABLE_TYPES = {
    ObjectType.TABLE,
    ObjectType.EXTERNAL_TABLE,
    ObjectType.VIEW,
    ObjectType.DYNAMIC_VIEW,
    ObjectType.MATERIALIZED_VIEW,
    ObjectType.STREAMING_TABLE,
}

SHOW_CREATE_FUNCTION_TYPES = {ObjectType.FUNCTION}

# Optional SHOW CREATE variants — attempted then fall back to synthesis.
SHOW_CREATE_OPTIONAL_TYPES = {
    ObjectType.CATALOG: "CATALOG",
    ObjectType.SCHEMA: "SCHEMA",
    ObjectType.VOLUME: "VOLUME",
    ObjectType.EXTERNAL_VOLUME: "VOLUME",
}

# UC GRANT ON <securable> keyword for each object type.
SECURABLE_SQL_KEYWORD = {
    ObjectType.CATALOG: "CATALOG",
    ObjectType.SCHEMA: "SCHEMA",
    ObjectType.TABLE: "TABLE",
    ObjectType.EXTERNAL_TABLE: "TABLE",
    ObjectType.VIEW: "VIEW",
    ObjectType.DYNAMIC_VIEW: "VIEW",
    ObjectType.METRIC_VIEW: "VIEW",
    ObjectType.MATERIALIZED_VIEW: "MATERIALIZED VIEW",
    ObjectType.STREAMING_TABLE: "TABLE",
    ObjectType.VOLUME: "VOLUME",
    ObjectType.EXTERNAL_VOLUME: "VOLUME",
    ObjectType.FUNCTION: "FUNCTION",
    ObjectType.MODEL: "MODEL",
    ObjectType.EXTERNAL_LOCATION: "EXTERNAL LOCATION",
    ObjectType.STORAGE_CREDENTIAL: "STORAGE CREDENTIAL",
    ObjectType.SERVICE_CREDENTIAL: "CREDENTIAL",
    ObjectType.CONNECTION: "CONNECTION",
    ObjectType.FOREIGN_CATALOG: "FOREIGN CATALOG",
    ObjectType.SHARE: "SHARE",
    ObjectType.RECIPIENT: "RECIPIENT",
    ObjectType.PROVIDER: "PROVIDER",
}


def quote_identifier(value: str) -> str:
    return f"`{str(value).replace('`', '``')}`"


def quote_full_name(full_name: str) -> str:
    return ".".join(quote_identifier(part) for part in str(full_name).split("."))


def quote_principal(principal: str) -> str:
    value = str(principal or "").strip()
    if not value:
        return "``"
    if value.startswith("`") and value.endswith("`"):
        return value
    return quote_identifier(value)


def escape_literal(value: Any) -> str:
    return str(value).replace("'", "''")


def comment_clause(value: Any) -> str:
    if value is None or str(value) == "":
        return ""
    return f" COMMENT '{escape_literal(value)}'"


def _as_object_type(object_type: ObjectType | str) -> Optional[ObjectType]:
    if isinstance(object_type, ObjectType):
        return object_type
    try:
        return ObjectType(object_type)
    except ValueError:
        return None


def prefers_show_create(object_type: ObjectType | str) -> bool:
    resolved = _as_object_type(object_type)
    if not resolved:
        return False
    return (
        resolved in SHOW_CREATE_TABLE_TYPES
        or resolved in SHOW_CREATE_FUNCTION_TYPES
    )


def supports_show_create(object_type: ObjectType | str) -> bool:
    """Backward-compatible alias used by export/tests."""
    return prefers_show_create(object_type) or bool(
        _as_object_type(object_type) in SHOW_CREATE_OPTIONAL_TYPES
    )


def show_create_command(object_type: ObjectType | str, full_name: str) -> str:
    resolved = _as_object_type(object_type)
    if not resolved:
        raise ValueError(f"Unsupported object type for SHOW CREATE: {object_type}")
    quoted = quote_full_name(full_name)
    if resolved in SHOW_CREATE_FUNCTION_TYPES:
        return f"SHOW CREATE FUNCTION {quoted}"
    if resolved in SHOW_CREATE_OPTIONAL_TYPES:
        keyword = SHOW_CREATE_OPTIONAL_TYPES[resolved]
        # Catalogs/external locations are single-segment names.
        target = (
            quote_identifier(full_name)
            if resolved == ObjectType.CATALOG
            else quoted
        )
        return f"SHOW CREATE {keyword} {target}"
    return f"SHOW CREATE TABLE {quoted}"


def ensure_semicolon(sql: str) -> str:
    text = str(sql or "").strip()
    if not text:
        return ""
    return text if text.endswith(";") else f"{text};"


def _normalize_privileges(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except json.JSONDecodeError:
                pass
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(raw, Iterable) and not isinstance(raw, (bytes, bytearray)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [str(raw).strip()]


def grant_statements_for_object(obj: UCObject) -> list[str]:
    """Build GRANT / OWNER DDL statements from inventory privilege assignments."""

    keyword = SECURABLE_SQL_KEYWORD.get(obj.object_type)
    if not keyword:
        return []
    target = quote_full_name(obj.full_name)
    statements: list[str] = []
    for assignment in obj.grants or []:
        if not isinstance(assignment, dict):
            continue
        principal = str(assignment.get("principal") or "").strip()
        if not principal or principal == "__PERMISSIONS_UNAVAILABLE__":
            continue
        privileges = _normalize_privileges(assignment.get("privileges"))
        quoted_principal = quote_principal(principal)
        for privilege in privileges:
            upper = privilege.upper().replace(" ", "_")
            if upper in {"OWNER", "OWN"}:
                statements.append(
                    f"ALTER {keyword} {target} OWNER TO {quoted_principal};"
                )
                continue
            statements.append(
                f"GRANT {upper} ON {keyword} {target} TO {quoted_principal};"
            )
    return statements


# Object types that can carry column masks / row filters. UC uses ``ALTER TABLE``
# to bind policies on all of these (materialized views and streaming tables
# included).
POLICY_TABLE_TYPES = {
    ObjectType.TABLE,
    ObjectType.EXTERNAL_TABLE,
    ObjectType.MATERIALIZED_VIEW,
    ObjectType.STREAMING_TABLE,
}


def _column_list(columns: Iterable[Any]) -> str:
    return ", ".join(quote_identifier(str(col)) for col in columns if str(col))


def mask_statements_for_object(obj: UCObject) -> list[str]:
    """Build ``ALTER TABLE ... ALTER COLUMN ... SET MASK`` statements.

    One statement per directly-defined column mask. ``USING COLUMNS`` is emitted
    only when the mask declares extra input columns.
    """

    if obj.object_type not in POLICY_TABLE_TYPES:
        return []
    target = quote_full_name(obj.full_name)
    statements: list[str] = []
    for mask in obj.column_masks():
        column = mask.get("column_name")
        function_name = mask.get("function_name")
        if not column or not function_name:
            continue
        using = mask.get("using_column_names") or []
        using_clause = f" USING COLUMNS ({_column_list(using)})" if using else ""
        statements.append(
            f"ALTER TABLE {target} ALTER COLUMN {quote_identifier(str(column))} "
            f"SET MASK {quote_full_name(str(function_name))}{using_clause};"
        )
    return statements


def row_filter_statements_for_object(obj: UCObject) -> list[str]:
    """Build the ``ALTER TABLE ... SET ROW FILTER ... ON (...)`` statement (0 or 1)."""

    if obj.object_type not in POLICY_TABLE_TYPES:
        return []
    row_filter = obj.row_filter()
    if not row_filter or not row_filter.get("function_name"):
        return []
    target = quote_full_name(obj.full_name)
    columns = _column_list(row_filter.get("input_column_names") or [])
    return [
        f"ALTER TABLE {target} SET ROW FILTER "
        f"{quote_full_name(str(row_filter['function_name']))} ON ({columns});"
    ]


def policy_statements_for_object(obj: UCObject) -> list[str]:
    """All column-mask + row-filter binding statements for a table.

    These are replayed in a dedicated phase after every object (tables *and*
    the mask/filter functions they reference) has been created.
    """

    return mask_statements_for_object(obj) + row_filter_statements_for_object(obj)


def render_sql_file(statements: list[str], *, header: str = "") -> str:
    lines = []
    if header:
        lines.append(f"-- {header}")
        lines.append("")
    lines.extend(statements)
    if not lines:
        return ""
    body = "\n".join(lines).rstrip() + "\n"
    return body


def _table_ddl_from_definition(obj: UCObject) -> Optional[str]:
    definition = obj.definition or {}
    columns = definition.get("columns") or []
    if not columns:
        return None
    # Defense-in-depth (plan P2-D): emit inline column MASK / WITH ROW FILTER so a
    # synthesized rebuild can never silently strip classic protection. With the
    # warehouse-only capture path (P2-A) this synthesizer is not reached for tables
    # in normal operation (a SHOW CREATE failure is a hard FAILURE, not a rebuild),
    # but keeping the clauses here removes the last way protection could be lost.
    masks_by_col = {
        str(mask.get("column_name")): mask
        for mask in obj.column_masks()
        if mask.get("column_name") and mask.get("function_name")
    }
    definitions = []
    for column in sorted(columns, key=lambda item: item.get("position", 0)):
        name = quote_identifier(str(column["name"]))
        data_type = str(
            column.get("type_text") or column.get("type_name") or ""
        ).strip()
        if not data_type:
            return None
        nullable = "" if column.get("nullable", True) else " NOT NULL"
        mask = masks_by_col.get(str(column["name"]))
        mask_clause = ""
        if mask:
            using = mask.get("using_column_names") or []
            using_clause = (
                f" USING COLUMNS ({_column_list(using)})" if using else ""
            )
            mask_clause = (
                f" MASK {quote_full_name(str(mask['function_name']))}{using_clause}"
            )
        definitions.append(
            f"{name} {data_type}{nullable}"
            f"{comment_clause(column.get('comment'))}{mask_clause}"
        )
    data_format = str(
        definition.get("data_source_format")
        or obj.data_source_format
        or "DELTA"
    ).upper()
    location = obj.storage_location or definition.get("storage_location") or ""
    location_clause = (
        f" LOCATION '{escape_literal(location)}'" if location else ""
    )
    portable_properties = {
        key: value
        for key, value in (obj.properties or {}).items()
        if not str(key).lower().startswith("delta.")
    }
    properties = ""
    if portable_properties:
        pairs = ", ".join(
            f"'{escape_literal(key)}' = '{escape_literal(value)}'"
            for key, value in sorted(portable_properties.items())
        )
        properties = f" TBLPROPERTIES ({pairs})"
    row_filter_clause = ""
    row_filter = obj.row_filter()
    if row_filter and row_filter.get("function_name"):
        columns_sql = _column_list(row_filter.get("input_column_names") or [])
        row_filter_clause = (
            f" WITH ROW FILTER "
            f"{quote_full_name(str(row_filter['function_name']))} "
            f"ON ({columns_sql})"
        )
    target = quote_full_name(obj.full_name)
    return (
        f"CREATE TABLE IF NOT EXISTS {target} "
        f"({', '.join(definitions)}) USING {data_format}"
        f"{location_clause}{comment_clause(definition.get('comment'))}"
        f"{properties}{row_filter_clause};"
    )


def _view_ddl_from_definition(obj: UCObject) -> Optional[str]:
    definition = obj.definition or {}
    view_sql = str(
        definition.get("view_definition")
        or definition.get("view_original_text")
        or ""
    ).strip()
    if not view_sql:
        return None
    target = quote_full_name(obj.full_name)
    return (
        f"CREATE OR REPLACE VIEW {target}"
        f"{comment_clause(definition.get('comment'))} AS {view_sql};"
    )


def _metric_view_ddl_from_definition(obj: UCObject) -> Optional[str]:
    definition = obj.definition or {}
    yaml_definition = str(
        definition.get("view_definition")
        or definition.get("view_original_text")
        or ""
    ).strip()
    if not yaml_definition:
        return None
    # REST may return either raw YAML or the complete CREATE statement.
    if re.match(r"(?is)^CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\b", yaml_definition):
        return ensure_semicolon(yaml_definition)
    target = quote_full_name(obj.full_name)
    return (
        f"CREATE OR REPLACE VIEW {target} WITH METRICS LANGUAGE YAML AS $$\n"
        f"{yaml_definition}\n"
        "$$;"
    )


def _function_ddl_from_definition(obj: UCObject) -> Optional[str]:
    definition = obj.definition or {}
    params = (definition.get("input_params") or {}).get("parameters") or []
    declarations = []
    for param in sorted(params, key=lambda item: item.get("position", 0)):
        raw_name = str(param["name"])
        name = (
            raw_name
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw_name)
            else quote_identifier(raw_name)
        )
        data_type = str(
            param.get("type_text") or param.get("type_name") or ""
        ).strip()
        if not data_type:
            return None
        declarations.append(f"{name} {data_type}")
    return_type = str(
        definition.get("full_data_type") or definition.get("data_type") or ""
    ).strip()
    body = str(definition.get("routine_definition") or "").strip()
    if not return_type or not body:
        return None
    target = quote_full_name(obj.full_name)
    return (
        f"CREATE FUNCTION IF NOT EXISTS {target}"
        f"({', '.join(declarations)}) RETURNS {return_type}"
        f"{comment_clause(definition.get('comment'))} RETURN {body};"
    )


def _rows(sql: Any, statement: str) -> list[list[Any]]:
    out = sql.execute(statement)
    return [list(r) if not isinstance(r, str) else [r] for r in (out or [])]


def function_ddl_from_information_schema(
    sql: Any, full_name: str
) -> Optional[str]:
    """Reassemble ``CREATE FUNCTION`` from ``information_schema`` over a warehouse.

    Plan P2-A: functions are captured warehouse-only via ``information_schema``
    (``routines`` = body/return, ``parameters`` = args), NOT via
    ``DESCRIBE FUNCTION EXTENDED`` (noisy ~130-line session-config dump, fragile to
    parse) and NOT via the REST catalog API (keeps capture warehouse-only).
    ``SHOW CREATE FUNCTION`` is unsupported in Databricks SQL. Functions carry no
    masks/row filters, so this is lossless. Returns ``None`` when the routine is
    absent or its body/return type cannot be read (caller decides the fallback).

    ``full_name`` is ``catalog.schema.function``. Overloaded UDFs are matched by
    ``specific_name`` so the parameter list belongs to the same overload.
    """

    parts = [p for p in str(full_name or "").split(".") if p]
    if len(parts) != 3:
        return None
    catalog, schema, name = parts
    cat_q = quote_identifier(catalog)
    routine_sql = (
        "SELECT specific_name, data_type, full_data_type, routine_definition, "
        "routine_body, is_deterministic, comment "
        f"FROM {cat_q}.information_schema.routines "
        f"WHERE routine_schema = '{escape_literal(schema)}' "
        f"AND routine_name = '{escape_literal(name)}' "
        "ORDER BY specific_name LIMIT 1"
    )
    rows = _rows(sql, routine_sql)
    if not rows:
        return None
    row = (list(rows[0]) + [None] * 7)[:7]
    specific_name, data_type, full_data_type, routine_definition, _body, _det, comment = row
    return_type = str(full_data_type or data_type or "").strip()
    body = str(routine_definition or "").strip()
    if not return_type or not body:
        return None

    declarations: list[str] = []
    if specific_name:
        param_sql = (
            "SELECT parameter_name, full_data_type, data_type, parameter_mode, "
            "ordinal_position "
            f"FROM {cat_q}.information_schema.parameters "
            f"WHERE specific_schema = '{escape_literal(schema)}' "
            f"AND specific_name = '{escape_literal(str(specific_name))}' "
            "ORDER BY ordinal_position"
        )
        try:
            param_rows = _rows(sql, param_sql)
        except Exception:  # noqa: BLE001 - parameters read is best-effort
            param_rows = []
        for prow in param_rows:
            prow = (list(prow) + [None] * 5)[:5]
            pname, pfull, pdata, pmode, _ord = prow
            # The RETURN row (and any OUT parameter) is not an input argument.
            if str(pmode or "IN").upper() != "IN":
                continue
            if pname is None or str(pname) == "":
                continue
            ptype = str(pfull or pdata or "").strip()
            if not ptype:
                continue
            raw = str(pname)
            pident = (
                raw
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw)
                else quote_identifier(raw)
            )
            declarations.append(f"{pident} {ptype}")

    target = quote_full_name(full_name)
    return (
        f"CREATE FUNCTION IF NOT EXISTS {target}"
        f"({', '.join(declarations)}) RETURNS {return_type}"
        f"{comment_clause(comment)} RETURN {body};"
    )


def _storage_credential_ddl(obj: UCObject) -> Optional[str]:
    """Best-effort credential DDL — secrets are never exported."""

    name = quote_identifier(obj.name)
    comment = comment_clause((obj.definition or {}).get("comment"))
    lines = [
        "-- WARNING: storage credential secrets are not exported.",
        "-- Review and complete cloud identity bindings before running.",
    ]
    credential_type = str(obj.credential_type or "").upper()
    if credential_type == "AZURE_MANAGED_IDENTITY" and obj.access_connector_id:
        lines.append(
            f"CREATE STORAGE CREDENTIAL IF NOT EXISTS {name} "
            f"WITH AZURE_MANAGED_IDENTITY ("
            f"ACCESS_CONNECTOR_ID = '{escape_literal(obj.access_connector_id)}'"
            f"){comment};"
        )
        if obj.user_assigned_managed_identity_id:
            lines.append(
                "-- user_assigned_managed_identity_id = "
                f"{obj.user_assigned_managed_identity_id}"
            )
        return "\n".join(lines) + "\n"
    lines.append(
        f"-- MANUAL: CREATE STORAGE CREDENTIAL IF NOT EXISTS {name} "
        f"-- credential_type={credential_type or 'UNKNOWN'}{comment}"
    )
    return "\n".join(lines) + "\n"


def create_ddl_for_object(obj: UCObject) -> Optional[str]:
    """Synthesize CREATE DDL from inventoried metadata when SHOW CREATE is unavailable."""

    kind = obj.object_type
    definition = obj.definition or {}
    target = quote_full_name(obj.full_name)

    if kind == ObjectType.CATALOG:
        storage_root = definition.get("storage_root")
        if storage_root is None and isinstance(obj.source_metadata, dict):
            storage_root = obj.source_metadata.get("storage_root")
        managed = (
            f" MANAGED LOCATION '{escape_literal(storage_root)}'"
            if storage_root
            else ""
        )
        return (
            f"CREATE CATALOG IF NOT EXISTS {target}{managed}"
            f"{comment_clause(definition.get('comment'))};"
        )

    if kind == ObjectType.SCHEMA:
        return (
            f"CREATE SCHEMA IF NOT EXISTS {target}"
            f"{comment_clause(definition.get('comment'))};"
        )

    if kind == ObjectType.VOLUME:
        return (
            f"CREATE VOLUME IF NOT EXISTS {target}"
            f"{comment_clause(definition.get('comment'))};"
        )

    if kind == ObjectType.EXTERNAL_VOLUME:
        location = obj.storage_location or definition.get("storage_location")
        if not location:
            return None
        return (
            f"CREATE EXTERNAL VOLUME IF NOT EXISTS {target} "
            f"LOCATION '{escape_literal(location)}'"
            f"{comment_clause(definition.get('comment'))};"
        )

    if kind == ObjectType.EXTERNAL_LOCATION:
        url = obj.storage_location or definition.get("url")
        credential = obj.storage_credential_name or definition.get(
            "credential_name"
        )
        if not url or not credential:
            return None
        return (
            f"CREATE EXTERNAL LOCATION IF NOT EXISTS {quote_identifier(obj.name)} "
            f"URL '{escape_literal(url)}' "
            f"WITH (STORAGE CREDENTIAL {quote_identifier(credential)})"
            f"{comment_clause(definition.get('comment'))};"
        )

    if kind == ObjectType.STORAGE_CREDENTIAL:
        return _storage_credential_ddl(obj)

    if kind in {ObjectType.TABLE, ObjectType.EXTERNAL_TABLE}:
        return _table_ddl_from_definition(obj)

    if kind in {
        ObjectType.VIEW,
        ObjectType.DYNAMIC_VIEW,
    }:
        return _view_ddl_from_definition(obj)

    if kind == ObjectType.METRIC_VIEW:
        return _metric_view_ddl_from_definition(obj)

    if kind == ObjectType.FUNCTION:
        return _function_ddl_from_definition(obj)

    # MATERIALIZED_VIEW / STREAMING_TABLE / MODEL: require SHOW CREATE or manual.
    return None


def format_ddl_file(
    obj: UCObject,
    ddl: str,
    *,
    source: str,
    command: str = "",
) -> str:
    header = [
        f"-- {obj.object_type.value} {obj.full_name}",
        f"-- source={source}",
    ]
    if command:
        header.append(f"-- captured via {command}")
    body = str(ddl).rstrip()
    if source != "SYNTHESIZED_MANUAL":
        body = ensure_semicolon(body)
    return "\n".join(header) + "\n" + body + "\n"
