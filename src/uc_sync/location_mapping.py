"""External storage location mapping loaded from CSV or YAML configuration."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class LocationMapping:
    """One source external-location root mapped to a target root and credential."""

    source_location: str
    target_location: str
    target_external_location: str
    target_credential: str
    source_external_location: str = ""
    target_external_location_url: str = ""
    target_access_connector_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "source_location": self.source_location,
            "target_location": self.target_location,
            "target_external_location": self.target_external_location,
            "target_credential": self.target_credential,
            "source_external_location": self.source_external_location,
            "target_external_location_url": (
                self.target_external_location_url or self.target_location
            ),
            "target_access_connector_id": self.target_access_connector_id,
        }


_ALIASES = {
    "source_url": "source_location",
    "target_url": "target_location",
    "source_external_location_name": "source_external_location",
    "target_external_location_name": "target_external_location",
    "target_name": "target_external_location",
    "target_storage_credential": "target_credential",
    "credential": "target_credential",
    "target_connector_id": "target_access_connector_id",
    "access_connector_id": "target_access_connector_id",
}


def _clean_location(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _canonical_row(row: dict[str, Any]) -> dict[str, str]:
    canonical: dict[str, str] = {}
    for raw_key, raw_value in row.items():
        key = _ALIASES.get(str(raw_key or "").strip().lower(), str(raw_key or "").strip().lower())
        canonical[key] = str(raw_value or "").strip()
    return canonical


def parse_location_mappings(
    rows: Iterable[dict[str, Any]],
) -> list[LocationMapping]:
    """Validate and normalize mapping records.

    Required CSV columns are just ``source_location`` and ``target_location`` (the
    path rewrite). ``target_access_connector_id`` is also required in practice when
    the utility creates the target storage credential. Because securable **names
    are never mapped**, the storage credential and external location are recreated
    under their *source* names, so ``target_credential`` and
    ``target_external_location`` are optional (kept for the direct-import path /
    validation). ``source_external_location`` is optional too.
    """

    mappings: list[LocationMapping] = []
    for index, raw in enumerate(rows, start=2):
        row = _canonical_row(raw)
        mapping = LocationMapping(
            source_location=_clean_location(row.get("source_location")),
            target_location=_clean_location(row.get("target_location")),
            target_external_location=str(
                row.get("target_external_location") or ""
            ).strip(),
            target_credential=str(row.get("target_credential") or "").strip(),
            source_external_location=str(
                row.get("source_external_location") or ""
            ).strip(),
            target_external_location_url=_clean_location(
                row.get("target_external_location_url")
                or row.get("external_location_target_url")
                or row.get("target_location")
            ),
            target_access_connector_id=str(
                row.get("target_access_connector_id") or ""
            ).strip(),
        )
        missing = [
            field
            for field in (
                "source_location",
                "target_location",
            )
            if not getattr(mapping, field)
        ]
        if missing:
            raise ValueError(
                f"Location mapping row {index} is missing: {', '.join(missing)}"
            )
        mappings.append(mapping)

    source_roots = [item.source_location for item in mappings]
    target_roots = [item.target_location for item in mappings]
    if len(set(source_roots)) != len(source_roots):
        raise ValueError("Each source_location must appear only once")
    # Many table paths can legitimately share one target external location, so
    # only the target storage path itself must be unique per row.
    if len(set(target_roots)) != len(target_roots):
        raise ValueError("Each target_location must appear only once")
    return mappings


def load_location_mapping_csv(path: str | Path) -> list[LocationMapping]:
    mapping_path = Path(path)
    if not mapping_path.exists():
        raise FileNotFoundError(f"Location mapping CSV does not exist: {path}")
    with mapping_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Location mapping CSV has no header: {path}")
        return parse_location_mappings(reader)


@dataclass(frozen=True)
class ObjectLocations:
    """Explicit target locations for schemas / external volumes / external tables.

    Sourced from the optional ``object_locations`` CSV (``schema, volume, table,
    location``). Which columns are filled decides the row's meaning:

    * ``schema`` + ``location`` → the schema's ``MANAGED LOCATION``;
    * ``schema`` + ``volume`` + ``location`` → that external volume's ``LOCATION``;
    * ``schema`` + ``table`` + ``location`` → that external table's ``LOCATION``.

    Keys are **source** names (only the catalog is remapped on import, so schema /
    volume / table names are identical source→target).
    """

    schemas: dict[str, str]
    volumes: dict[tuple[str, str], str]
    tables: dict[tuple[str, str], str]

    def schema_location(self, schema: str) -> Optional[str]:
        return self.schemas.get(str(schema or ""))

    def volume_location(self, schema: str, volume: str) -> Optional[str]:
        return self.volumes.get((str(schema or ""), str(volume or "")))

    def table_location(self, schema: str, table: str) -> Optional[str]:
        return self.tables.get((str(schema or ""), str(table or "")))

    def __bool__(self) -> bool:
        return bool(self.schemas or self.volumes or self.tables)


def parse_object_locations(rows: Iterable[dict[str, Any]]) -> ObjectLocations:
    """Validate and index ``object_locations`` rows."""

    schemas: dict[str, str] = {}
    volumes: dict[tuple[str, str], str] = {}
    tables: dict[tuple[str, str], str] = {}
    for index, raw in enumerate(rows, start=2):
        row = {
            str(key or "").strip().lower(): str(value or "").strip()
            for key, value in raw.items()
        }
        schema = row.get("schema", "")
        volume = row.get("volume", "")
        table = row.get("table", "")
        location = _clean_location(row.get("location"))
        if not any((schema, volume, table, location)):
            continue  # blank line
        if not schema or not location:
            raise ValueError(
                f"object_locations row {index} needs both a schema and a location"
            )
        if volume and table:
            raise ValueError(
                f"object_locations row {index} sets both volume and table; "
                "use one per row"
            )
        if volume:
            volumes[(schema, volume)] = location
        elif table:
            tables[(schema, table)] = location
        else:
            schemas[schema] = location
    return ObjectLocations(schemas=schemas, volumes=volumes, tables=tables)


def load_object_locations_csv(path: str | Path) -> ObjectLocations:
    locations_path = Path(path)
    if not locations_path.exists():
        raise FileNotFoundError(f"object-locations CSV does not exist: {path}")
    with locations_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"object-locations CSV has no header: {path}")
        return parse_object_locations(reader)


# --- Task 2: the single external-storage mapping file -----------------------


@dataclass(frozen=True)
class ExternalLocationMapping:
    """Unified source→target external-storage mapping (``external_locations.csv``).

    The **column shape auto-selects behavior** — there is no extra toggle:

    * **2 columns** (``source_base_path,target_base_path``) → **BYO mode**: the
      storage credential + external location are prerequisites the customer created;
      the utility creates neither and only **prefix-swaps** each external object's
      ``LOCATION`` onto the target base.
    * **3 columns** (``…,access_connector_id``) → **CREATE mode**: the utility
      creates the target storage credential (from the Azure access connector) and the
      external location, then places objects (parity with the legacy Mode-A mapping).

    ``creates_storage`` is True iff **any** row carries an access connector, so the
    presence of that column is the only thing the operator must get right.
    """

    pairs: tuple[tuple[str, str], ...]        # (source_base, target_base), file order
    connectors: dict[str, str]                # target_base -> access_connector_id
    creates_storage: bool                     # True iff any row has a connector

    def rewrite(self, location: str) -> Optional[str]:
        """Prefix-swap a source ``LOCATION`` onto its paired target base.

        Deliberately simple (per the plan): plain ``startswith``, **first matching
        row wins**, and the remainder after the source base is kept byte-for-byte.
        No longest-prefix logic, no segment math, no path normalization — base paths
        are expected non-overlapping (one per account/container). Returns ``None``
        when no source base matches (the caller then skips / reports the object).
        """
        loc = str(location or "")
        for source_base, target_base in self.pairs:
            if loc == source_base or loc.startswith(source_base):
                return target_base + loc[len(source_base):]
        return None

    def covers_target(self, location: str) -> bool:
        """True when ``location`` already sits under a target base — i.e. it was
        already prefix-swapped (e.g. at export). Such an object is already placed,
        not an unmapped skip."""
        loc = str(location or "")
        return any(
            loc == target_base or loc.startswith(target_base)
            for _src, target_base in self.pairs
        )

    def to_location_mappings(self) -> list[dict[str, str]]:
        """Feed the legacy ``location_mappings`` machinery (export-time path rewrite
        + Mode-A storage-credential/external-location creation) from this one file,
        so both modes are driven by ``external_locations.csv`` alone."""
        rows: list[dict[str, str]] = []
        for source_base, target_base in self.pairs:
            row = {
                "source_location": source_base,
                "target_location": target_base,
                "target_external_location_url": target_base,
            }
            connector = self.connectors.get(target_base)
            if connector:
                row["target_access_connector_id"] = connector
            rows.append(row)
        return rows

    def __bool__(self) -> bool:
        return bool(self.pairs)


_EXTERNAL_LOCATION_ALIASES = {
    "source_base_path": "source_base_path",
    "source_location": "source_base_path",
    "source_url": "source_base_path",
    "source": "source_base_path",
    "target_base_path": "target_base_path",
    "target_location": "target_base_path",
    "target_url": "target_base_path",
    "target": "target_base_path",
    "access_connector_id": "access_connector_id",
    "target_access_connector_id": "access_connector_id",
    "connector_id": "access_connector_id",
    "target_connector_id": "access_connector_id",
}


def parse_external_locations(rows: Iterable[dict[str, Any]]) -> ExternalLocationMapping:
    """Validate + index ``external_locations.csv`` rows; detect 2- vs 3-column shape."""

    pairs: list[tuple[str, str]] = []
    connectors: dict[str, str] = {}
    creates_storage = False
    seen_sources: set[str] = set()
    for index, raw in enumerate(rows, start=2):
        row: dict[str, str] = {}
        for key, value in raw.items():
            canonical = _EXTERNAL_LOCATION_ALIASES.get(
                str(key or "").strip().lower()
            )
            if canonical:
                row[canonical] = str(value or "").strip()
        source_base = row.get("source_base_path", "")
        target_base = row.get("target_base_path", "")
        connector = row.get("access_connector_id", "")
        if not any((source_base, target_base, connector)):
            continue  # blank line
        if not source_base or not target_base:
            raise ValueError(
                f"external_locations row {index} needs both source_base_path and "
                "target_base_path"
            )
        if source_base in seen_sources:
            raise ValueError(
                f"external_locations row {index}: duplicate source_base_path "
                f"{source_base!r}"
            )
        seen_sources.add(source_base)
        pairs.append((source_base, target_base))
        if connector:
            connectors[target_base] = connector
            creates_storage = True
    return ExternalLocationMapping(
        pairs=tuple(pairs), connectors=connectors, creates_storage=creates_storage
    )


def load_external_locations_csv(path: str | Path) -> ExternalLocationMapping:
    locations_path = Path(path)
    if not locations_path.exists():
        raise FileNotFoundError(f"external_locations CSV does not exist: {path}")
    with locations_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"external_locations CSV has no header: {path}")
        return parse_external_locations(reader)


def mappings_from_legacy_config(
    external_locations: dict[str, Any],
    storage_credentials: dict[str, Any],
    source_locations: dict[str, str] | None = None,
) -> list[LocationMapping]:
    """Normalize the existing YAML mappings when source URLs are available."""

    rows = []
    source_locations = source_locations or {}
    for source_name, raw_entry in (external_locations or {}).items():
        entry = raw_entry if isinstance(raw_entry, dict) else {}
        source_credential = str(entry.get("source_credential") or "")
        credential_entry = (storage_credentials or {}).get(source_credential)
        if not credential_entry:
            credential_entry = (storage_credentials or {}).get(source_name)
        if isinstance(credential_entry, dict):
            mapped_credential = credential_entry.get("target_credential")
        else:
            mapped_credential = credential_entry
        rows.append(
            {
                "source_external_location": source_name,
                "source_location": entry.get("source_location")
                or entry.get("source_url")
                or source_locations.get(source_name),
                "target_external_location": entry.get("target_external_location")
                or entry.get("target_name"),
                "target_location": entry.get("target_location")
                or entry.get("target_url"),
                "target_credential": entry.get("target_credential")
                or entry.get("target_storage_credential")
                or mapped_credential,
                "target_external_location_url": (
                    entry.get("target_external_location_url")
                    or entry.get("target_url")
                ),
            }
        )
    return parse_location_mappings(rows) if rows else []
