"""Object filtering helpers."""

from __future__ import annotations

import re
from typing import Iterable

from uc_sync.config import SyncConfig
from uc_sync.models import UCObject


def _compile(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in patterns if p]


# Securables the ``filter_tables`` allowlist narrows. Everything else (catalogs,
# schemas, functions, volumes, …) always flows through so a selected table keeps
# its prerequisites.
TABLE_LIKE_TYPES = frozenset({
    "TABLE", "EXTERNAL_TABLE", "VIEW", "DYNAMIC_VIEW",
    "METRIC_VIEW", "MATERIALIZED_VIEW", "STREAMING_TABLE",
})


def table_selected(selection: Iterable[str], *full_names: str) -> bool:
    """Match a table against the ``filter_tables`` allowlist.

    Each entry is a fully-qualified ``catalog.schema.table`` or a bare table name.
    Several candidate names may be passed (import checks both the source and the
    catalog-mapped target name) — any match selects the table. An empty
    selection selects everything.
    """
    wanted = {str(item).strip() for item in selection if str(item).strip()}
    if not wanted:
        return True
    for full_name in full_names:
        name = str(full_name or "")
        if not name:
            continue
        if name in wanted or name.split(".")[-1] in wanted:
            return True
    return False


def schema_selected(
    schemas: Iterable[str], catalog: str | None, schema: str | None
) -> bool:
    """Match a schema against the configured list.

    Both ``catalog.schema`` and the bare ``schema`` name are accepted so a
    short name does not silently exclude every object in that schema.
    """
    if not schema:
        return False
    wanted = {str(item).strip() for item in schemas if str(item).strip()}
    if schema in wanted:
        return True
    return bool(catalog) and f"{catalog}.{schema}" in wanted


def allowed(obj: UCObject, cfg: SyncConfig) -> bool:
    object_type = obj.object_type.value
    if object_type in {t.upper() for t in cfg.exclude_object_types}:
        return False
    if cfg.include_object_types and object_type not in {
        t.upper() for t in cfg.include_object_types
    }:
        return False
    if obj.schema == "information_schema":
        return False
    if obj.name == "information_schema" and object_type == "SCHEMA":
        return False
    if obj.catalog in {"system", "samples"} and object_type == "CATALOG":
        return False
    if cfg.catalogs and obj.catalog and obj.catalog not in cfg.catalogs:
        if object_type == "CATALOG" and obj.name not in cfg.catalogs:
            return False
        if object_type != "CATALOG":
            return False
    if cfg.schemas:
        if object_type == "SCHEMA" and not schema_selected(
            cfg.schemas, obj.catalog, obj.name
        ):
            return False
        if object_type not in {
            "CATALOG",
            "SCHEMA",
            "STORAGE_CREDENTIAL",
            "EXTERNAL_LOCATION",
            "CONNECTION",
        }:
            if obj.schema and not schema_selected(
                cfg.schemas, obj.catalog, obj.schema
            ):
                return False
    name = obj.full_name or obj.name
    if (
        cfg.filter_tables
        and object_type in TABLE_LIKE_TYPES
        and not table_selected(cfg.filter_tables, name)
    ):
        return False
    include = _compile(cfg.include_regex)
    exclude = _compile(cfg.exclude_regex)
    if include and not any(p.search(name) for p in include):
        return False
    if exclude and any(p.search(name) for p in exclude):
        return False
    return True
