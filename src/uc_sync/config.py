"""Configuration loading: YAML defaults overridden by widgets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


from uc_sync.components import resolve_components
from uc_sync.location_mapping import (
    load_location_mapping_csv,
    parse_location_mappings,
)


@dataclass
class SyncConfig:
    execution_mode: str = "LOCAL"
    mode: str = "INVENTORY"
    dry_run: bool = True
    source_workspace_url: str = ""
    source_oauth_secret_scope: str = ""
    source_client_id_secret_key: str = ""
    source_client_secret_key: str = ""
    target_workspace_url: str = ""
    target_oauth_secret_scope: str = ""
    target_client_id_secret_key: str = ""
    target_client_secret_key: str = ""
    export_volume_path: str = ""
    report_volume_path: str = ""
    audit_table: str = ""
    state_table: str = ""
    import_package_path: str = ""
    location_mapping_csv_path: str = ""
    catalog_mapping: dict[str, str] = field(default_factory=dict)
    catalogs: list[str] = field(default_factory=list)
    schemas: list[str] = field(default_factory=list)
    exclude_object_types: list[str] = field(default_factory=list)
    include_object_types: list[str] = field(default_factory=list)
    components: str = "ALL"
    include_parents: bool = True
    include_regex: list[str] = field(default_factory=list)
    exclude_regex: list[str] = field(default_factory=list)
    import_mode: str = "CREATE_OR_SKIP"
    allow_destructive_operations: bool = False
    max_api_workers: int = 8
    mappings: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


AUDIT_TABLE_NAME = "uc_sync_audit"
STATE_TABLE_NAME = "uc_sync_state"


def derive_ops_paths(
    *,
    ops_catalog: str = "",
    ops_schema: str = "",
    output_volume_path: str = "",
) -> dict[str, str]:
    """Resolve UCSync's four operational-artifact locations from three inputs.

    UCSync writes its own bookkeeping to four places: the export volume, the report
    volume, and the audit + state Delta tables. The user supplies just three things:

    - ``ops_catalog`` + ``ops_schema`` — where the audit/state tables live
    - ``output_volume_path`` — the volume for exports + reports

    and the four locations are derived (table names are standard/fixed):

    - export volume / report volume -> ``output_volume_path`` (both)
    - audit table -> ``{ops_catalog}.{ops_schema}.uc_sync_audit``
    - state table -> ``{ops_catalog}.{ops_schema}.uc_sync_state``

    A field is returned blank when its inputs are missing, so downstream validation
    fails fast with a clear message instead of writing to a stale default.
    """

    oc = str(ops_catalog or "").strip()
    os_ = str(ops_schema or "").strip()
    volume = str(output_volume_path or "").strip()
    has_base = bool(oc and os_)

    return {
        "export_volume_path": volume,
        "report_volume_path": volume,
        "audit_table": f"{oc}.{os_}.{AUDIT_TABLE_NAME}" if has_base else "",
        "state_table": f"{oc}.{os_}.{STATE_TABLE_NAME}" if has_base else "",
    }


def _split_csv(value: str) -> list[str]:
    if not value or not str(value).strip():
        return []
    return [p.strip() for p in str(value).split(",") if p.strip()]


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_yaml(path: str | Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "PyYAML is required to load config files. Install pyyaml in the Job cluster."
        ) from exc
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {path}")
    return data


def load_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JSON config does not exist: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON config root must be an object: {path}")
    return data


def parse_catalog_mapping(
    inline_json: str = "",
    json_path: str = "",
    fallback: Optional[dict[str, Any]] = None,
) -> dict[str, str]:
    """Parse source->target catalog mapping from inline JSON or a Volume path."""
    if inline_json and inline_json.strip():
        data: dict[str, Any] = json.loads(inline_json)
    elif json_path and json_path.strip():
        data = load_json(json_path)
    else:
        data = dict(fallback or {})
    if "catalogs" in data:
        nested = data["catalogs"]
        if not isinstance(nested, dict):
            raise ValueError("'catalogs' in mapping JSON must be an object")
        data = nested
    mapping = {str(k).strip(): str(v).strip() for k, v in data.items()}
    if any(not k or not v for k, v in mapping.items()):
        raise ValueError("Catalog mapping cannot contain blank source/target names")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Each source catalog must map to a distinct target catalog")
    return mapping


def from_sources(
    widget_values: dict[str, Any],
    file_config: Optional[dict[str, Any]] = None,
) -> SyncConfig:
    """Merge file config with widget overrides (widgets win when non-empty)."""
    file_config = file_config or {}
    source = file_config.get("source", {}) or {}
    target = file_config.get("target", {}) or {}
    export = file_config.get("export", {}) or {}
    reporting = file_config.get("reporting", {}) or {}
    location_config = file_config.get("location_mapping", {}) or {}
    selection = file_config.get("selection", {}) or {}
    runtime = file_config.get("runtime", {}) or {}

    def pick(widget_key: str, *fallback_values: Any) -> Any:
        wv = widget_values.get(widget_key)
        if wv is not None and str(wv).strip() != "":
            return wv
        for fb in fallback_values:
            if fb is not None and str(fb).strip() != "":
                return fb
        return ""

    catalogs = pick("catalogs", None)
    if isinstance(catalogs, str):
        catalogs = _split_csv(catalogs) or list(selection.get("catalogs") or [])
    schemas = pick("schemas", None)
    if isinstance(schemas, str):
        schemas = _split_csv(schemas) or list(selection.get("schemas") or [])
    exclude_types = pick("exclude_object_types", None)
    if isinstance(exclude_types, str):
        exclude_types = _split_csv(exclude_types) or list(
            selection.get("exclude_object_types") or []
        )
    components_raw = str(
        pick(
            "components",
            selection.get("components"),
            "ALL",
        )
    )
    include_parents = _as_bool(
        pick(
            "include_parents",
            selection.get("include_parents"),
            runtime.get("include_parents"),
            True,
        ),
        True,
    )
    include_types_raw = pick("include_object_types", None)
    if isinstance(include_types_raw, str) and include_types_raw.strip():
        include_types = resolve_components(
            include_types_raw,
            include_parents=include_parents,
        )
        components_raw = include_types_raw
    else:
        file_include = selection.get("include_object_types") or []
        if file_include:
            include_types = resolve_components(
                file_include,
                include_parents=include_parents,
            )
        else:
            include_types = resolve_components(
                components_raw,
                include_parents=include_parents,
            )
    include_regex = pick("include_regex", None)
    if isinstance(include_regex, str):
        include_regex = _split_csv(include_regex) or list(selection.get("include_regex") or [])
    exclude_regex = pick("exclude_regex", None)
    if isinstance(exclude_regex, str):
        exclude_regex = _split_csv(exclude_regex) or list(selection.get("exclude_regex") or [])
    catalog_mapping = parse_catalog_mapping(
        str(pick("catalog_mapping_json", "")),
        str(pick("catalog_mapping_path", "")),
        file_config.get("catalog_mapping") or {},
    )
    location_mapping_csv_path = str(
        pick(
            "location_mapping_csv_path",
            location_config.get("csv_path"),
            file_config.get("location_mapping_csv_path"),
        )
    )
    if location_mapping_csv_path:
        location_mappings = [
            item.to_dict()
            for item in load_location_mapping_csv(location_mapping_csv_path)
        ]
    else:
        configured_mappings = file_config.get("location_mappings") or []
        location_mappings = [
            item.to_dict()
            for item in parse_location_mappings(configured_mappings)
        ]
    execution_mode = str(
        pick("execution_mode", runtime.get("execution_mode"), "LOCAL")
    ).upper()
    if execution_mode not in {"LOCAL", "CROSS_WORKSPACE"}:
        raise ValueError("execution_mode must be LOCAL or CROSS_WORKSPACE")
    if execution_mode == "LOCAL" and not catalog_mapping:
        raise ValueError(
            "LOCAL mode requires catalog_mapping_json, catalog_mapping_path, "
            "or catalog_mapping in the config file"
        )
    if execution_mode == "LOCAL" and not catalogs:
        catalogs = list(catalog_mapping)

    # Resolve UCSync's four operational-artifact locations from three inputs:
    # ops_catalog + ops_schema (audit/state tables) and output_volume_path
    # (exports + reports). See derive_ops_paths().
    ops_paths = derive_ops_paths(
        ops_catalog=str(pick("ops_catalog", export.get("ops_catalog"))),
        ops_schema=str(pick("ops_schema", export.get("ops_schema"))),
        output_volume_path=str(
            pick(
                "output_volume_path",
                export.get("volume_path"),
                reporting.get("volume_path"),
            )
        ),
    )

    return SyncConfig(
        execution_mode=execution_mode,
        mode=str(pick("mode", "INVENTORY")).upper(),
        dry_run=_as_bool(pick("dry_run", runtime.get("dry_run", True)), True),
        source_workspace_url=str(pick("source_workspace_url", source.get("workspace_url"))),
        source_oauth_secret_scope=str(
            pick("source_oauth_secret_scope", source.get("secret_scope"))
        ),
        source_client_id_secret_key=str(
            pick("source_client_id_secret_key", source.get("client_id_key"))
        ),
        source_client_secret_key=str(
            pick("source_client_secret_key", source.get("client_secret_key"))
        ),
        target_workspace_url=str(pick("target_workspace_url", target.get("workspace_url"))),
        target_oauth_secret_scope=str(
            pick("target_oauth_secret_scope", target.get("secret_scope"))
        ),
        target_client_id_secret_key=str(
            pick("target_client_id_secret_key", target.get("client_id_key"))
        ),
        target_client_secret_key=str(
            pick("target_client_secret_key", target.get("client_secret_key"))
        ),
        export_volume_path=ops_paths["export_volume_path"],
        report_volume_path=ops_paths["report_volume_path"],
        audit_table=ops_paths["audit_table"],
        state_table=ops_paths["state_table"],
        import_package_path=str(
            pick(
                "import_package_path",
                export.get("import_package_path"),
                file_config.get("import_package_path"),
            )
        ),
        location_mapping_csv_path=location_mapping_csv_path,
        catalog_mapping=catalog_mapping,
        catalogs=list(catalogs or []),
        schemas=list(schemas or []),
        exclude_object_types=[t.upper() for t in (exclude_types or [])],
        include_object_types=list(include_types or []),
        components=str(components_raw or "ALL"),
        include_parents=include_parents,
        include_regex=list(include_regex or []),
        exclude_regex=list(exclude_regex or []),
        import_mode=str(runtime.get("import_mode") or "CREATE_OR_SKIP"),
        allow_destructive_operations=_as_bool(
            runtime.get("allow_destructive_operations"), False
        ),
        max_api_workers=int(runtime.get("max_api_workers") or 8),
        mappings={
            "storage_credentials": file_config.get("storage_credentials") or {},
            "external_locations": file_config.get("external_locations") or {},
            "location_mappings": location_mappings,
            "managed_storage": file_config.get("managed_storage") or {},
            "principals": file_config.get("principals") or {},
            "workspaces": file_config.get("workspaces") or {},
            "catalogs": catalog_mapping,
        },
        raw=file_config,
    )
