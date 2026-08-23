"""Governed-tag and ABAC-policy inventory reads + DDL builders.

Tags and ABAC policies are UC governance that lives in ``information_schema`` /
``system.information_schema`` (SQL, not the REST catalog API), so these helpers
take a SQL executor. Reconstructed DDL is replayed verbatim on the target — names
are never remapped, and each ABAC policy keeps its source ``EXCEPT`` list.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from uc_sync.models import ObjectType, UCObject
from uc_sync.sql_ddl import escape_literal, quote_full_name, quote_identifier


# --- SQL reads --------------------------------------------------------------

_TAG_QUERIES = {
    # level: (info_schema table, grouping columns that identify the securable)
    "catalog": ("catalog_tags", ["catalog_name"]),
    "schema": ("schema_tags", ["catalog_name", "schema_name"]),
    "table": ("table_tags", ["catalog_name", "schema_name", "table_name"]),
    "column": (
        "column_tags",
        ["catalog_name", "schema_name", "table_name", "column_name"],
    ),
    "volume": ("volume_tags", ["catalog_name", "schema_name", "volume_name"]),
}


def _rows(sql: Any, statement: str) -> list[list[Any]]:
    out = sql.execute(statement)
    return list(out or [])


def read_tags(sql: Any, catalog: str) -> dict[str, Any]:
    """Read all governed/plain tag assignments in a catalog.

    Returns ``{"objects": {full_name: {tag: value}},
               "columns": {table_full_name: {column: {tag: value}}}}``.
    """

    objects: dict[str, dict[str, str]] = {}
    columns: dict[str, dict[str, dict[str, str]]] = {}
    cat_q = quote_identifier(catalog)
    for level, (table, cols) in _TAG_QUERIES.items():
        stmt = (
            f"SELECT {', '.join(cols)}, tag_name, tag_value "
            f"FROM {cat_q}.information_schema.{table}"
        )
        try:
            rows = _rows(sql, stmt)
        except Exception:  # noqa: BLE001 - tag table may be empty/absent
            continue
        for row in rows:
            *ident, tag_name, tag_value = row
            if level == "column":
                cat, sch, tbl, col = ident
                full = f"{cat}.{sch}.{tbl}"
                columns.setdefault(full, {}).setdefault(col, {})[tag_name] = (
                    tag_value
                )
            else:
                full = ".".join(str(p) for p in ident)
                objects.setdefault(full, {})[tag_name] = tag_value
    return {"objects": objects, "columns": columns}


def _describe_policy(
    sql: Any, name: str, on_type: str, on_securable: str
) -> dict[str, str]:
    """DESCRIBE POLICY → {info_name: info_value} (function, on-column, comment…)."""

    stmt = f"DESCRIBE POLICY {quote_identifier(name)} ON {on_type} "
    stmt += (
        quote_identifier(on_securable)
        if on_type == "CATALOG"
        else quote_full_name(on_securable)
    )
    info: dict[str, str] = {}
    try:
        for row in _rows(sql, stmt):
            if len(row) >= 2 and row[0] is not None:
                info[str(row[0]).strip()] = row[1]
    except Exception:  # noqa: BLE001
        pass
    return info


def read_abac_policies(sql: Any, catalog: str) -> list[UCObject]:
    """Inventory ABAC policies attached at/under ``catalog`` as UCObjects."""

    stmt = (
        "SELECT policy_name, policy_type, catalog_name, schema_name, "
        "securable_name, on_securable_type, to_principals, except_principals, "
        "for_securable_type, when_condition, match_columns "
        "FROM system.information_schema.abac_policy_definitions "
        f"WHERE catalog_name = '{escape_literal(catalog)}'"
    )
    try:
        rows = _rows(sql, stmt)
    except Exception:  # noqa: BLE001
        return []
    policies: list[UCObject] = []
    for row in rows:
        (
            name,
            policy_type,
            cat,
            schema,
            securable,
            on_type,
            to_principals,
            except_principals,
            for_type,
            when_condition,
            match_columns,
        ) = row
        # Build the securable full name the policy is attached ON.
        if on_type == "CATALOG":
            on_securable = cat
        elif on_type == "SCHEMA":
            on_securable = f"{cat}.{schema}"
        else:  # TABLE
            on_securable = f"{cat}.{schema}.{securable}"
        described = _describe_policy(sql, name, on_type, on_securable)
        definition = {
            "policy_name": name,
            "policy_type": policy_type,
            "on_securable_type": on_type,
            "on_securable": on_securable,
            "for_securable_type": for_type,
            "to_principals": _json_list(to_principals),
            "except_principals": _json_list(except_principals),
            "match_columns": _json_list(match_columns),
            "when_condition": when_condition or "",
            "function_name": described.get("Function Name", ""),
            "on_column": described.get("On Column", ""),
            "using_columns": described.get("Using Columns", ""),
            "comment": described.get("Comment", ""),
        }
        policies.append(
            UCObject(
                object_type=ObjectType.ABAC_POLICY,
                name=str(name),
                full_name=f"{on_securable}#policy:{name}",
                catalog=cat,
                schema=schema,
                definition=definition,
            )
        )
    return policies


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    except json.JSONDecodeError:
        pass
    return [text]


# --- DDL builders -----------------------------------------------------------

# ALTER <keyword> for SET TAGS on each object level.
_TAG_ALTER_KEYWORD = {
    ObjectType.CATALOG: "CATALOG",
    ObjectType.SCHEMA: "SCHEMA",
    ObjectType.TABLE: "TABLE",
    ObjectType.EXTERNAL_TABLE: "TABLE",
    ObjectType.VIEW: "VIEW",
    ObjectType.DYNAMIC_VIEW: "VIEW",
    ObjectType.MATERIALIZED_VIEW: "MATERIALIZED VIEW",
    ObjectType.STREAMING_TABLE: "TABLE",
    ObjectType.VOLUME: "VOLUME",
    ObjectType.EXTERNAL_VOLUME: "VOLUME",
}


def _tags_clause(tags: dict[str, str]) -> str:
    pairs = ", ".join(
        f"'{escape_literal(k)}' = '{escape_literal(v)}'"
        for k, v in sorted(tags.items())
    )
    return f"SET TAGS ({pairs})"


def tag_statements_for_object(obj: UCObject) -> list[str]:
    """``ALTER … SET TAGS`` for object-level tags and per-column tags."""

    statements: list[str] = []
    keyword = _TAG_ALTER_KEYWORD.get(obj.object_type)
    target = (
        quote_identifier(obj.name)
        if obj.object_type == ObjectType.CATALOG
        else quote_full_name(obj.full_name)
    )
    if keyword and obj.tags:
        statements.append(f"ALTER {keyword} {target} {_tags_clause(obj.tags)};")
    # Column tags (tables/views only) live in definition['column_tags'].
    column_tags = (obj.definition or {}).get("column_tags") or {}
    if keyword in {"TABLE", "VIEW", "MATERIALIZED VIEW"} and column_tags:
        for column, tags in sorted(column_tags.items()):
            if not tags:
                continue
            statements.append(
                f"ALTER {keyword} {target} ALTER COLUMN "
                f"{quote_identifier(column)} {_tags_clause(tags)};"
            )
    return statements


def _principal_list(principals: list[str]) -> str:
    return ", ".join(quote_identifier(p) for p in principals if p)


def abac_policy_create_statement(obj: UCObject) -> Optional[str]:
    """Rebuild ``CREATE POLICY`` verbatim (incl. source EXCEPT) from inventory."""

    d = obj.definition or {}
    name = d.get("policy_name") or obj.name
    on_type = str(d.get("on_securable_type") or "").upper()
    on_securable = str(d.get("on_securable") or "")
    func = str(d.get("function_name") or "")
    if not (name and on_type and on_securable and func):
        return None
    securable_sql = (
        quote_identifier(on_securable)
        if on_type == "CATALOG"
        else quote_full_name(on_securable)
    )
    policy_type = str(d.get("policy_type") or "").upper()
    to_principals = d.get("to_principals") or []
    except_principals = d.get("except_principals") or []
    match_columns = d.get("match_columns") or []
    when_condition = str(d.get("when_condition") or "").strip()
    comment = d.get("comment") or ""

    parts = [f"CREATE POLICY {quote_identifier(name)} ON {on_type} {securable_sql}"]
    if comment:
        parts.append(f"COMMENT '{escape_literal(comment)}'")
    if policy_type == "ROW_FILTER":
        parts.append(f"ROW FILTER {quote_full_name(func)}")
    else:
        parts.append(f"COLUMN MASK {quote_full_name(func)}")
    if to_principals:
        parts.append(f"TO {_principal_list(to_principals)}")
    if except_principals:
        parts.append(f"EXCEPT {_principal_list(except_principals)}")
    parts.append("FOR TABLES")
    if when_condition:
        parts.append(f"WHEN {when_condition}")
    if match_columns:
        parts.append(f"MATCH COLUMNS {', '.join(match_columns)}")
    if policy_type == "ROW_FILTER":
        using = str(d.get("using_columns") or "").strip()
        if using:
            # DESCRIBE renders using-columns as a bare/paren list; normalize.
            cols = using.strip("()")
            parts.append(f"USING COLUMNS ({cols})")
    else:
        on_column = str(d.get("on_column") or "").strip()
        if on_column:
            parts.append(f"ON COLUMN {quote_identifier(on_column)}")
    return "\n".join(parts) + ";"
