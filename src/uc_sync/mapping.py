"""Target mapping resolution."""

from __future__ import annotations

from typing import Any, Optional


class MappingResolver:
    def __init__(self, mappings: dict[str, Any]):
        self.mappings = mappings or {}

    def storage_credential(self, source_name: str) -> Optional[str]:
        entry = (self.mappings.get("storage_credentials") or {}).get(source_name)
        if isinstance(entry, dict):
            return entry.get("target_credential")
        return str(entry) if entry else None

    def external_location(self, source_name: str) -> Optional[dict[str, Any]]:
        return (self.mappings.get("external_locations") or {}).get(source_name)

    def location_mappings(self) -> list[dict[str, str]]:
        return [
            {str(key): str(value) for key, value in item.items()}
            for item in (self.mappings.get("location_mappings") or [])
            if isinstance(item, dict)
        ]

    def location_mapping_for_url(
        self, source_location: str
    ) -> Optional[dict[str, str]]:
        """Return the longest source-root mapping containing this object path."""

        source = str(source_location or "").rstrip("/")
        matches = [
            item
            for item in self.location_mappings()
            if source == item.get("source_location", "").rstrip("/")
            or source.startswith(item.get("source_location", "").rstrip("/") + "/")
        ]
        return max(
            matches,
            key=lambda item: len(item.get("source_location", "")),
            default=None,
        )

    def rewrite_location(self, source_location: str) -> Optional[str]:
        mapping = self.location_mapping_for_url(source_location)
        if not mapping:
            return None
        source_root = mapping["source_location"].rstrip("/")
        target_root = mapping["target_location"].rstrip("/")
        suffix = str(source_location)[len(source_root) :]
        return target_root + suffix

    def external_location_mapping(
        self,
        source_name: str,
        source_location: str,
        source_credential: str = "",
    ) -> Optional[dict[str, str]]:
        """Resolve CSV mappings first, then the legacy YAML mapping shape."""

        by_name = [
            item
            for item in self.location_mappings()
            if item.get("source_external_location") == source_name
        ]
        mapping = (
            max(
                by_name,
                key=lambda item: len(item.get("source_location", "")),
                default=None,
            )
            or self.location_mapping_for_url(source_location)
        )
        if not mapping:
            source_root = str(source_location or "").rstrip("/")
            children = [
                item
                for item in self.location_mappings()
                if item.get("source_location", "").startswith(source_root + "/")
            ]
            if len(children) == 1:
                mapping = children[0]
        if mapping:
            return mapping

        legacy = self.external_location(source_name)
        if not isinstance(legacy, dict):
            return None
        target_credential = (
            legacy.get("target_credential")
            or legacy.get("target_storage_credential")
            or self.storage_credential(source_credential)
            or self.storage_credential(source_name)
        )
        target_location = legacy.get("target_location") or legacy.get("target_url")
        target_name = (
            legacy.get("target_external_location") or legacy.get("target_name")
        )
        if not target_location or not target_name or not target_credential:
            return None
        return {
            "source_external_location": source_name,
            "source_location": str(source_location).rstrip("/"),
            "target_external_location": str(target_name),
            "target_location": str(target_location).rstrip("/"),
            "target_credential": str(target_credential),
        }

    def target_access_connector_id_for_location(
        self, source_location: str
    ) -> Optional[str]:
        """Target access-connector id for the mapping row that owns this path.

        Per-catalog enterprise setups back each storage credential with its own
        connector, so the connector must be resolved from the specific source
        storage location the credential is used for (matched longest-prefix),
        not from an arbitrary "first row".
        """
        mapping = self.location_mapping_for_url(source_location)
        if mapping:
            value = str(mapping.get("target_access_connector_id") or "").strip()
            if value:
                return value
        return None

    def target_access_connector_id(self) -> Optional[str]:
        """First target access-connector id in the mapping file.

        Fallback for credentials whose source storage location cannot be
        resolved (e.g. a credential backing no external location). When a single
        target connector backs every credential this is also sufficient; prefer
        :meth:`target_access_connector_id_for_location` when the location is known.
        """
        for item in self.location_mappings():
            value = str(item.get("target_access_connector_id") or "").strip()
            if value:
                return value
        return None

    def managed_storage_root(self, catalog_name: str) -> Optional[str]:
        managed = self.mappings.get("managed_storage") or {}
        explicit = (managed.get("catalogs") or {}).get(catalog_name)
        if explicit:
            return str(explicit)
        base = managed.get("default_catalog_storage_root")
        return f"{str(base).rstrip('/')}/{catalog_name}" if base else None

    def principal(self, source_principal: str) -> str:
        return str(
            (self.mappings.get("principals") or {}).get(
                source_principal, source_principal
            )
        )

    def workspace_id(self, source_workspace_id: str | int) -> Optional[str]:
        mapped = (self.mappings.get("workspaces") or {}).get(
            str(source_workspace_id)
        )
        return str(mapped) if mapped is not None else None

    def target_catalog(self, source_catalog: str) -> Optional[str]:
        mapped = (self.mappings.get("catalogs") or {}).get(source_catalog)
        return str(mapped) if mapped is not None else None

    def target_full_name(self, source_full_name: str) -> Optional[str]:
        source_catalog, separator, remainder = source_full_name.partition(".")
        target_catalog = self.target_catalog(source_catalog)
        if not target_catalog:
            return None
        return target_catalog + (separator + remainder if separator else "")
