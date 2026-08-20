"""Catalog / location rewrite helpers for exported SQL, YAML, and JSON."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from uc_sync.mapping import MappingResolver
from uc_sync.sql_ddl import quote_identifier


def rewrite_text(
    text: str,
    catalog_mapping: Mapping[str, str],
    *,
    location_resolver: MappingResolver | None = None,
) -> str:
    """Rewrite source catalog names (and optional storage URLs) in free-form text."""

    rewritten = str(text or "")
    # Longest catalog names first so overlapping prefixes rewrite correctly.
    for source_catalog, target_catalog in sorted(
        catalog_mapping.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if not source_catalog or not target_catalog:
            continue
        source_q = quote_identifier(source_catalog)
        target_q = quote_identifier(target_catalog)
        rewritten = rewritten.replace(f"{source_q}.", f"{target_q}.")
        rewritten = re.sub(
            rf"(?<![\w`]){re.escape(source_catalog)}\.",
            f"{target_catalog}.",
            rewritten,
        )
        # Bare single-segment catalog references (CREATE CATALOG `source`).
        rewritten = re.sub(
            rf"(?<![\w`]){re.escape(source_q)}(?![\w`.])",
            target_q,
            rewritten,
        )
        rewritten = re.sub(
            rf"(?<![\w`.]){re.escape(source_catalog)}(?![\w`.])",
            target_catalog,
            rewritten,
        )

    if location_resolver is not None:
        rewritten = _rewrite_storage_urls(rewritten, location_resolver)
    return rewritten


def _rewrite_storage_urls(text: str, resolver: MappingResolver) -> str:
    pattern = re.compile(
        r"(abfss://[^\s'\"`]+|abfs://[^\s'\"`]+|s3://[^\s'\"`]+|gs://[^\s'\"`]+)",
        re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        source = match.group(1)
        target = resolver.rewrite_location(source.rstrip("/"))
        return target if target else source

    return pattern.sub(replace, text)


def strip_managed_storage_clauses(text: str, object_type: str = "") -> str:
    """Drop source managed LOCATION clauses so the target metastore assigns storage.

    External tables/volumes/locations keep LOCATION/URL (rewritten separately).
    """

    upper = str(object_type or "").upper()
    if upper in {"EXTERNAL_TABLE", "EXTERNAL_VOLUME", "EXTERNAL_LOCATION"}:
        return text
    rewritten = str(text or "")
    rewritten = re.sub(
        r"\s+MANAGED\s+LOCATION\s+'[^']*'",
        "",
        rewritten,
        flags=re.IGNORECASE,
    )
    rewritten = re.sub(
        r'\s+MANAGED\s+LOCATION\s+"[^"]*"',
        "",
        rewritten,
        flags=re.IGNORECASE,
    )
    # Managed CREATE TABLE / CREATE VOLUME clauses (not EXTERNAL ...).
    if "EXTERNAL" not in rewritten.upper().split("LOCATION", 1)[0]:
        rewritten = re.sub(
            r"\s+LOCATION\s+'[^']*'",
            "",
            rewritten,
            flags=re.IGNORECASE,
        )
        rewritten = re.sub(
            r'\s+LOCATION\s+"[^"]*"',
            "",
            rewritten,
            flags=re.IGNORECASE,
        )
    # Newer runtimes emit a table-level `COLLATION '<name>'` clause in
    # SHOW CREATE TABLE output (e.g. `USING delta\nCOLLATION 'UTF8_BINARY'`).
    # Older SQL parsers reject that standalone clause, so replaying the captured
    # DDL fails with PARSE_SYNTAX_ERROR at 'COLLATION'. Drop it and let the target
    # use its default collation. The per-type `COLLATE <name>` qualifier is handled
    # separately by strip_inline_collate() below.
    rewritten = re.sub(
        r"\s+COLLATION\s+'[^']*'",
        "",
        rewritten,
        flags=re.IGNORECASE,
    )
    rewritten = re.sub(
        r'\s+COLLATION\s+"[^"]*"',
        "",
        rewritten,
        flags=re.IGNORECASE,
    )
    rewritten = strip_inline_collate(rewritten)
    rewritten = strip_inline_policy_clauses(rewritten)
    rewritten = strip_reserved_table_properties(rewritten)
    return rewritten


def strip_inline_collate(text: str) -> str:
    """Drop inline ``COLLATE <name>`` qualifiers from captured DDL.

    Distinct from the table-level ``COLLATION '<name>'`` clause handled above,
    this targets the per-type qualifier that rides on column and parameter type
    declarations, e.g. ``v STRING COLLATE UTF8_BINARY``. It shows up most often in
    synthesized *function* DDL: ``SHOW CREATE FUNCTION`` fails on some runtimes, so
    the DDL is rebuilt from catalog metadata whose ``type_text`` embeds the source
    collation, and table column definitions can carry the same qualifier.

    A target metastore that hasn't enabled collation rejects any ``COLLATE`` with
    ``[UNSUPPORTED_FEATURE.COLLATION] ... SQLSTATE: 0A000`` — a different error than
    the table-level clause's ``PARSE_SYNTAX_ERROR`` but the same root cause. The
    qualifier describes source string internals, so strip it and let the target use
    its default collation. The collation name is an unquoted identifier
    (``UTF8_BINARY``, ``UTF8_LCASE``, …) or a backtick-quoted one; ``COLLATION`` is
    left alone because it is never followed by whitespace here.
    """

    return re.sub(
        r"\s+COLLATE\s+(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)",
        "",
        str(text or ""),
        flags=re.IGNORECASE,
    )


# A fully-qualified name: backtick-quoted or bare identifiers joined by dots.
_FQ_NAME = (
    r"(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*\.\s*(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_]*))*"
)


def strip_inline_policy_clauses(text: str) -> str:
    """Drop inline column-mask and row-filter clauses from captured CREATE DDL.

    ``SHOW CREATE TABLE`` emits column masks and the table row filter inline, e.g.::

        ssn STRING COLLATE UTF8_BINARY MASK `cat`.`sec`.`mask_ssn`,
        ...
        WITH ROW FILTER `cat`.`sec`.`hr_dept_filter` ON (dept)

    Replaying that fails when the referenced function does not yet exist in the
    target (functions import after tables), so the clauses are stripped here and
    re-applied from the ``policies/*.sql`` artifact in a dedicated phase once every
    object exists. Mirrors :func:`strip_inline_collate`.
    """

    rewritten = str(text or "")
    # Table-level row filter: ``WITH ROW FILTER <fqname> ON (cols)``.
    rewritten = re.sub(
        rf"\s+WITH\s+ROW\s+FILTER\s+{_FQ_NAME}\s+ON\s*\([^)]*\)",
        "",
        rewritten,
        flags=re.IGNORECASE,
    )
    # Column mask: ``MASK <fqname> [USING COLUMNS (cols)]`` inside a column def.
    rewritten = re.sub(
        rf"\s+MASK\s+{_FQ_NAME}(?:\s+USING\s+COLUMNS\s*\([^)]*\))?",
        "",
        rewritten,
        flags=re.IGNORECASE,
    )
    return rewritten


def strip_reserved_table_properties(text: str) -> str:
    """Remove reserved/auto-managed ``delta.*`` keys from a TBLPROPERTIES block.

    ``SHOW CREATE TABLE`` emits the table's full property set, including
    protocol/feature keys and auto-generated row-tracking column names
    (``delta.rowTracking.materializedRowIdColumnName`` etc.). Replaying those on
    ``CREATE TABLE`` fails with ``DELTA_UNKNOWN_CONFIGURATION``. These describe
    source storage internals the target metastore manages itself, so drop every
    ``delta.*`` property and let the target assign its own. User-defined
    (non-``delta.``) properties are preserved; if none remain, the whole
    ``TBLPROPERTIES (...)`` clause is removed.
    """

    rewritten = str(text or "")

    def _filter_block(match: re.Match[str]) -> str:
        body = match.group(1)
        kept: list[str] = []
        # Entries look like: 'key' = 'value'  (comma-separated, possibly multiline)
        for key, value in re.findall(
            r"'([^']*)'\s*=\s*'([^']*)'", body
        ):
            if key.lower().startswith("delta."):
                continue
            kept.append(f"'{key}' = '{value}'")
        if not kept:
            return ""
        return "TBLPROPERTIES (\n  " + ",\n  ".join(kept) + ")"

    rewritten = re.sub(
        r"TBLPROPERTIES\s*\(([^)]*)\)",
        _filter_block,
        rewritten,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Tidy any dangling whitespace left where the clause was removed.
    rewritten = re.sub(r"[ \t]+\n", "\n", rewritten)
    rewritten = re.sub(r"\n{3,}", "\n\n", rewritten)
    return rewritten


def rewrite_external_location_identifiers(
    text: str,
    *,
    source_name: str,
    target_name: str,
    target_credential: str,
) -> str:
    """Rename external location + credential identifiers in CREATE SQL."""

    rewritten = str(text or "")
    if source_name and target_name and source_name != target_name:
        rewritten = rewritten.replace(
            f"`{source_name}`", f"`{target_name}`"
        )
        rewritten = re.sub(
            rf"(?<![\w`]){re.escape(source_name)}(?![\w`])",
            target_name,
            rewritten,
        )
    if target_credential:
        rewritten = re.sub(
            r"(STORAGE\s+CREDENTIAL\s+)`[^`]+`",
            rf"\1`{target_credential}`",
            rewritten,
            flags=re.IGNORECASE,
        )
    return rewritten


def rewrite_json_value(
    value: Any,
    catalog_mapping: Mapping[str, str],
    *,
    location_resolver: MappingResolver | None = None,
) -> Any:
    if isinstance(value, dict):
        return {
            key: rewrite_json_value(
                item, catalog_mapping, location_resolver=location_resolver
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            rewrite_json_value(
                item, catalog_mapping, location_resolver=location_resolver
            )
            for item in value
        ]
    if isinstance(value, str):
        return rewrite_text(
            value, catalog_mapping, location_resolver=location_resolver
        )
    return value


def rewrite_json_text(
    text: str,
    catalog_mapping: Mapping[str, str],
    *,
    location_resolver: MappingResolver | None = None,
) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return rewrite_text(
            text, catalog_mapping, location_resolver=location_resolver
        )
    rewritten = rewrite_json_value(
        payload, catalog_mapping, location_resolver=location_resolver
    )
    return json.dumps(rewritten, indent=2, default=str) + "\n"
