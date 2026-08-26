"""Dependency planning / topological ordering."""

from __future__ import annotations

from typing import Iterable, List, Tuple

from uc_sync.models import ObjectType, UCObject

_TYPE_RANK = {
    ObjectType.STORAGE_CREDENTIAL: 10,
    ObjectType.SERVICE_CREDENTIAL: 11,
    ObjectType.EXTERNAL_LOCATION: 20,
    ObjectType.CATALOG: 30,
    ObjectType.SCHEMA: 40,
    ObjectType.VOLUME: 50,
    ObjectType.EXTERNAL_VOLUME: 51,
    # Functions rank BEFORE tables: a table's inline column MASK / row-filter
    # clause (kept in the CREATE TABLE for atomic fail-closed protection)
    # references a mask/filter function, so that function must already exist when
    # the table is created. (Mask functions are scalar and do not read tables.)
    ObjectType.FUNCTION: 55,
    ObjectType.TABLE: 60,
    ObjectType.EXTERNAL_TABLE: 61,
    ObjectType.MODEL: 80,
    ObjectType.VIEW: 90,
    ObjectType.DYNAMIC_VIEW: 91,
    ObjectType.METRIC_VIEW: 92,
    ObjectType.MATERIALIZED_VIEW: 100,
    ObjectType.STREAMING_TABLE: 101,
    # ABAC policies are created once every table + mask/filter function they
    # reference exists, and before grants (governed-before-granted).
    ObjectType.ABAC_POLICY: 105,
    ObjectType.GRANT: 110,
    ObjectType.BINDING: 120,
}


def plan(objects: Iterable[UCObject]) -> List[Tuple[int, int, UCObject]]:
    """Return list of (dependency_level, import_order, obj)."""
    objs = list(objects)
    ranked = sorted(
        objs,
        key=lambda o: (_TYPE_RANK.get(o.object_type, 999), o.full_name or o.name),
    )
    # Level ≈ type-rank bucket for v1; view SQL dependency parse comes later.
    level_for_type = {
        t: i for i, t in enumerate(sorted(set(_TYPE_RANK.values())))
    }
    planned: list[Tuple[int, int, UCObject]] = []
    for idx, obj in enumerate(ranked, start=1):
        rank = _TYPE_RANK.get(obj.object_type, 999)
        level = level_for_type.get(rank, len(level_for_type))
        planned.append((level, idx, obj))
    return planned
