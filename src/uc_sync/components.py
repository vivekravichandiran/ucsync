"""Component / object-type selection presets for scoped UC Sync runs."""

from __future__ import annotations

from typing import Iterable, Optional

from uc_sync.models import ObjectType

# Structural parents are included automatically when a leaf component is selected
# so dependency-aware import can still create catalog/schema containers.
STRUCTURAL_PARENTS = {
    ObjectType.CATALOG.value,
    ObjectType.SCHEMA.value,
}

# Types InventoryService can currently discover (catalogs → schemas →
# tables | volumes | functions). Selecting anything else resolves to the
# structural parents alone, which looks like a catalog/schema-only report.
DISCOVERABLE_TYPES = {
    ObjectType.CATALOG.value,
    ObjectType.SCHEMA.value,
    ObjectType.TABLE.value,
    ObjectType.EXTERNAL_TABLE.value,
    ObjectType.STREAMING_TABLE.value,
    ObjectType.VIEW.value,
    ObjectType.DYNAMIC_VIEW.value,
    ObjectType.METRIC_VIEW.value,
    ObjectType.MATERIALIZED_VIEW.value,
    ObjectType.VOLUME.value,
    ObjectType.EXTERNAL_VOLUME.value,
    ObjectType.FUNCTION.value,
    ObjectType.EXTERNAL_LOCATION.value,
    ObjectType.STORAGE_CREDENTIAL.value,
}

COMPONENT_PRESETS: dict[str, set[str]] = {
    "ALL": set(),  # empty means no include filter
    "TABLES": {
        ObjectType.TABLE.value,
        ObjectType.EXTERNAL_TABLE.value,
        ObjectType.STREAMING_TABLE.value,
        ObjectType.EXTERNAL_LOCATION.value,
        ObjectType.STORAGE_CREDENTIAL.value,
    },
    "MANAGED_TABLES": {ObjectType.TABLE.value},
    "EXTERNAL_TABLES": {
        ObjectType.EXTERNAL_TABLE.value,
        ObjectType.EXTERNAL_LOCATION.value,
        ObjectType.STORAGE_CREDENTIAL.value,
    },
    "STREAMING_TABLES": {ObjectType.STREAMING_TABLE.value},
    "VIEWS": {
        ObjectType.VIEW.value,
        ObjectType.DYNAMIC_VIEW.value,
        ObjectType.METRIC_VIEW.value,
    },
    "DYNAMIC_VIEWS": {ObjectType.DYNAMIC_VIEW.value},
    "METRIC_VIEWS": {ObjectType.METRIC_VIEW.value},
    "MATERIALIZED_VIEWS": {ObjectType.MATERIALIZED_VIEW.value},
    "TABLES_VIEWS": {
        ObjectType.TABLE.value,
        ObjectType.EXTERNAL_TABLE.value,
        ObjectType.STREAMING_TABLE.value,
        ObjectType.EXTERNAL_LOCATION.value,
        ObjectType.STORAGE_CREDENTIAL.value,
        ObjectType.VIEW.value,
        ObjectType.DYNAMIC_VIEW.value,
        ObjectType.METRIC_VIEW.value,
    },
    "TABLES_VIEWS_MVS": {
        ObjectType.TABLE.value,
        ObjectType.EXTERNAL_TABLE.value,
        ObjectType.STREAMING_TABLE.value,
        ObjectType.EXTERNAL_LOCATION.value,
        ObjectType.STORAGE_CREDENTIAL.value,
        ObjectType.VIEW.value,
        ObjectType.DYNAMIC_VIEW.value,
        ObjectType.METRIC_VIEW.value,
        ObjectType.MATERIALIZED_VIEW.value,
    },
    "VOLUMES": {
        ObjectType.VOLUME.value,
        ObjectType.EXTERNAL_VOLUME.value,
    },
    "MANAGED_VOLUMES": {ObjectType.VOLUME.value},
    "EXTERNAL_VOLUMES": {ObjectType.EXTERNAL_VOLUME.value},
    "FUNCTIONS": {ObjectType.FUNCTION.value},
    "MODELS": {ObjectType.MODEL.value},
    "GRANTS": {ObjectType.GRANT.value},
    "BINDINGS": {ObjectType.BINDING.value},
    "STORAGE": {
        ObjectType.STORAGE_CREDENTIAL.value,
        ObjectType.SERVICE_CREDENTIAL.value,
        ObjectType.EXTERNAL_LOCATION.value,
    },
    "SHARING": {
        ObjectType.SHARE.value,
        ObjectType.RECIPIENT.value,
        ObjectType.PROVIDER.value,
    },
    "FEDERATION": {
        ObjectType.CONNECTION.value,
        ObjectType.FOREIGN_CATALOG.value,
    },
    "DATA_OBJECTS": {
        ObjectType.TABLE.value,
        ObjectType.EXTERNAL_TABLE.value,
        ObjectType.STREAMING_TABLE.value,
        ObjectType.VIEW.value,
        ObjectType.DYNAMIC_VIEW.value,
        ObjectType.METRIC_VIEW.value,
        ObjectType.MATERIALIZED_VIEW.value,
        ObjectType.VOLUME.value,
        ObjectType.EXTERNAL_VOLUME.value,
        ObjectType.FUNCTION.value,
        ObjectType.EXTERNAL_LOCATION.value,
    },
}

# Friendly aliases used in job parameters / widgets.
# Exact ObjectType names (TABLE, VIEW, …) resolve as themselves first.
_ALIASES: dict[str, str] = {
    "DYNAMIC_VIEW": "DYNAMIC_VIEWS",
    "METRIC_VIEW": "METRIC_VIEWS",
    "METRICS": "METRIC_VIEWS",
    "MATERIALIZED_VIEW": "MATERIALIZED_VIEWS",
    "MV": "MATERIALIZED_VIEWS",
    "MVS": "MATERIALIZED_VIEWS",
    "TABLES+VIEWS": "TABLES_VIEWS",
    "TABLES_AND_VIEWS": "TABLES_VIEWS",
    "TABLES+VIEWS+MVS": "TABLES_VIEWS_MVS",
}


def available_components() -> list[str]:
    return sorted(COMPONENT_PRESETS)


def _normalize_token(token: str) -> str:
    cleaned = (
        token.strip()
        .upper()
        .replace("-", "_")
        .replace(" ", "_")
        .replace("/", "_")
    )
    return cleaned


def _expand_token(token: str) -> set[str]:
    normalized = _normalize_token(token)
    if not normalized:
        return set()
    # Exact ObjectType wins over plural aliases (TABLE vs TABLES).
    try:
        return {ObjectType[normalized].value}
    except KeyError:
        pass
    aliased = _ALIASES.get(normalized, normalized)
    if aliased in COMPONENT_PRESETS:
        return set(COMPONENT_PRESETS[aliased])
    if normalized in COMPONENT_PRESETS:
        return set(COMPONENT_PRESETS[normalized])
    valid = ", ".join(available_components())
    raise ValueError(
        f"Unknown component '{token}'. Use a preset ({valid}) "
        "or an ObjectType name, optionally combined with '+' or ','."
    ) from None


def resolve_components(
    components: str | Iterable[str] | None,
    *,
    include_parents: bool = True,
) -> list[str]:
    """Resolve component presets / combinations into concrete object types.

    Examples:
      - ``tables``
      - ``tables_views``
      - ``tables+views``
      - ``TABLE,VIEW,DYNAMIC_VIEW``
      - ``tables+dynamic_views+functions``
      - ``ALL`` / blank → no include filter (empty list)
    """
    if components is None:
        return []
    if isinstance(components, str):
        raw = components.strip()
        if not raw or raw.upper() in {"ALL", "*"}:
            return []
        tokens = [
            part
            for chunk in raw.replace("|", "+").split(",")
            for part in chunk.split("+")
            if part.strip()
        ]
    else:
        tokens = [str(item) for item in components if str(item).strip()]
        if any(_normalize_token(token) in {"ALL", "*"} for token in tokens):
            return []

    selected: set[str] = set()
    for token in tokens:
        selected |= _expand_token(token)

    if not selected:
        return []

    if include_parents and selected - STRUCTURAL_PARENTS:
        selected |= STRUCTURAL_PARENTS

    return sorted(selected)


def undiscoverable_types(include_object_types: Iterable[str]) -> list[str]:
    """Selected types that inventory cannot list yet."""
    return sorted(
        {
            str(item).upper()
            for item in include_object_types
            if str(item).upper() not in DISCOVERABLE_TYPES
        }
    )


def describe_components(include_object_types: Iterable[str]) -> str:
    types = sorted({str(item).upper() for item in include_object_types})
    if not types:
        return "ALL"
    return ",".join(types)
