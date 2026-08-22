"""Validation / compare stubs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, List, Optional

from uc_sync.config import SyncConfig
from uc_sync.export import canonical_hash
from uc_sync.inventory import (
    _column_masks_from_payload,
    _row_filter_from_payload,
)
from uc_sync.mapping import MappingResolver
from uc_sync.models import UCObject, ValidationStatus
from uc_sync.sql_ddl import POLICY_TABLE_TYPES
from uc_sync.workspace_client import WorkspaceClient


@dataclass
class ValidationResult:
    object_type: str
    source_full_name: str
    target_full_name: str
    status: str
    detail: str = ""
    source_definition_hash: str = ""
    target_definition_hash: str = ""
    error_code: str = ""
    error_message: str = ""
    source_location: str = ""
    expected_target_location: str = ""
    actual_target_location: str = ""
    expected_target_credential: str = ""
    actual_target_credential: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ValidationService:
    def __init__(
        self, target: WorkspaceClient, cfg: Optional[SyncConfig] = None
    ):
        self.target = target
        self.mapper = MappingResolver(cfg.mappings) if cfg else None

    def compare(
        self, source_objects: Iterable[UCObject]
    ) -> List[ValidationResult]:
        results: list[ValidationResult] = []
        for obj in source_objects:
            location_mapping = self._location_mapping(obj)
            target_name = ""
            if obj.object_type.value == "EXTERNAL_LOCATION" and location_mapping:
                target_name = str(
                    location_mapping.get("target_external_location") or ""
                )
            else:
                target_name = (
                    self.mapper.target_full_name(obj.full_name)
                    if self.mapper
                    else obj.full_name
                ) or ""
            if (
                not target_name
                and self.mapper
                and not self.mapper.mappings.get("catalogs")
            ):
                target_name = obj.full_name
            try:
                target_details = self._get_target(obj, target_name)
            except Exception as exc:  # noqa: BLE001
                results.append(
                    ValidationResult(
                        object_type=obj.object_type.value,
                        source_full_name=obj.full_name,
                        target_full_name=target_name,
                        status=ValidationStatus.ERROR.value,
                        detail=str(exc),
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                    )
                )
                continue
            if target_details is None:
                results.append(
                    ValidationResult(
                        object_type=obj.object_type.value,
                        source_full_name=obj.full_name,
                        target_full_name=target_name,
                        status=ValidationStatus.MISSING_TARGET.value,
                        detail="not found on target",
                        source_definition_hash=canonical_hash(obj),
                    )
                )
            else:
                comparison = self._location_comparison(
                    obj, target_details, location_mapping
                )
                policy_ok, policy_detail = self._policy_comparison(
                    obj, target_details
                )
                matches = comparison["matches"] and policy_ok
                detail = comparison["detail"]
                if not policy_ok:
                    detail = (f"{detail}; " if detail else "") + policy_detail
                results.append(
                    ValidationResult(
                        object_type=obj.object_type.value,
                        source_full_name=obj.full_name,
                        target_full_name=target_name,
                        status=(
                            ValidationStatus.MATCH.value
                            if matches
                            else ValidationStatus.DIFFERENT.value
                        ),
                        detail=detail,
                        source_definition_hash=canonical_hash(obj),
                        source_location=comparison["source_location"],
                        expected_target_location=comparison[
                            "expected_target_location"
                        ],
                        actual_target_location=comparison[
                            "actual_target_location"
                        ],
                        expected_target_credential=comparison[
                            "expected_target_credential"
                        ],
                        actual_target_credential=comparison[
                            "actual_target_credential"
                        ],
                    )
                )
        return results

    def _get_target(
        self, obj: UCObject, target_full_name: str
    ) -> Optional[dict[str, Any]]:
        path_map = {
            "CATALOG": f"/api/2.1/unity-catalog/catalogs/{target_full_name}",
            "SCHEMA": f"/api/2.1/unity-catalog/schemas/{target_full_name}",
            "TABLE": f"/api/2.1/unity-catalog/tables/{target_full_name}",
            "EXTERNAL_TABLE": f"/api/2.1/unity-catalog/tables/{target_full_name}",
            "VIEW": f"/api/2.1/unity-catalog/tables/{target_full_name}",
            "DYNAMIC_VIEW": f"/api/2.1/unity-catalog/tables/{target_full_name}",
            "METRIC_VIEW": f"/api/2.1/unity-catalog/tables/{target_full_name}",
            "MATERIALIZED_VIEW": f"/api/2.1/unity-catalog/tables/{target_full_name}",
            "VOLUME": f"/api/2.1/unity-catalog/volumes/{target_full_name}",
            "EXTERNAL_VOLUME": f"/api/2.1/unity-catalog/volumes/{target_full_name}",
            "FUNCTION": f"/api/2.1/unity-catalog/functions/{target_full_name}",
            "EXTERNAL_LOCATION": (
                f"/api/2.1/unity-catalog/external-locations/{target_full_name}"
            ),
        }
        path = path_map.get(obj.object_type.value)
        if not path:
            return None
        try:
            return self.target.get(path)
        except Exception as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def _location_mapping(
        self, obj: UCObject
    ) -> Optional[dict[str, str]]:
        if not self.mapper:
            return None
        source_location = obj.storage_location or str(
            obj.definition.get("storage_location")
            or obj.definition.get("url")
            or ""
        )
        if obj.object_type.value == "EXTERNAL_LOCATION":
            return self.mapper.external_location_mapping(
                obj.name,
                source_location,
                str(obj.definition.get("credential_name") or ""),
            )
        if obj.object_type.value == "EXTERNAL_TABLE":
            return self.mapper.location_mapping_for_url(source_location)
        return None

    @staticmethod
    def _policy_comparison(
        obj: UCObject, target: dict[str, Any]
    ) -> tuple[bool, str]:
        """Diff directly-defined column masks / row filter, source vs target.

        Function names are compared by their ``schema.object`` suffix because the
        catalog differs between source and target.
        """

        if obj.object_type not in POLICY_TABLE_TYPES:
            return True, ""

        def _suffix(name: Any) -> str:
            text = str(name or "")
            return text.partition(".")[2] or text

        src_masks = {
            (m.get("column_name"), _suffix(m.get("function_name")))
            for m in obj.column_masks()
        }
        tgt_masks = {
            (m.get("column_name"), _suffix(m.get("function_name")))
            for m in _column_masks_from_payload(target)
        }
        source_rf = obj.row_filter()
        target_rf = _row_filter_from_payload(target)
        src_rf = _suffix(source_rf["function_name"]) if source_rf else None
        tgt_rf = _suffix(target_rf["function_name"]) if target_rf else None

        problems: list[str] = []
        if src_masks != tgt_masks:
            problems.append(
                f"column masks differ (source={sorted(src_masks)}, "
                f"target={sorted(tgt_masks)})"
            )
        if src_rf != tgt_rf:
            problems.append(
                f"row filter differs (source={src_rf}, target={tgt_rf})"
            )
        return (not problems), "; ".join(problems)

    def _location_comparison(
        self,
        obj: UCObject,
        target: dict[str, Any],
        mapping: Optional[dict[str, str]],
    ) -> dict[str, Any]:
        source_location = obj.storage_location or str(
            obj.definition.get("storage_location")
            or obj.definition.get("url")
            or ""
        )
        expected_location = ""
        expected_credential = ""
        actual_location = ""
        actual_credential = ""
        if obj.object_type.value == "EXTERNAL_TABLE" and self.mapper:
            expected_location = self.mapper.rewrite_location(source_location) or ""
            actual_location = str(target.get("storage_location") or "").rstrip("/")
        elif obj.object_type.value == "EXTERNAL_LOCATION" and mapping:
            expected_location = str(
                mapping.get("target_external_location_url")
                or mapping.get("target_location")
                or ""
            ).rstrip("/")
            expected_credential = str(mapping.get("target_credential") or "")
            actual_location = str(target.get("url") or "").rstrip("/")
            actual_credential = str(target.get("credential_name") or "")

        location_matches = not expected_location or actual_location == expected_location
        credential_matches = (
            not expected_credential or actual_credential == expected_credential
        )
        matches = location_matches and credential_matches
        if not expected_location and obj.object_type.value not in {
            "EXTERNAL_TABLE",
            "EXTERNAL_LOCATION",
        }:
            detail = "target object exists"
        elif matches:
            detail = "target object exists with mapped location and credential"
        else:
            detail = (
                f"mapped storage mismatch: expected location={expected_location!r}, "
                f"actual location={actual_location!r}, "
                f"expected credential={expected_credential!r}, "
                f"actual credential={actual_credential!r}"
            )
        return {
            "matches": matches,
            "detail": detail,
            "source_location": source_location,
            "expected_target_location": expected_location,
            "actual_target_location": actual_location,
            "expected_target_credential": expected_credential,
            "actual_target_credential": actual_credential,
        }
