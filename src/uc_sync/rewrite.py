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
