"""Rewrite an export package into a target-mapped migrated package."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from uc_sync.mapping import MappingResolver
from uc_sync.models import ObjectType
from uc_sync.rewrite import (
    rewrite_access_connector_id,
    rewrite_json_text,
    rewrite_json_value,
    rewrite_text,
    strip_managed_storage_clauses,
)

# Longest values first so EXTERNAL_TABLE wins over TABLE when matching prefixes.
_OBJECT_TYPE_PREFIXES = tuple(
    sorted((member.value for member in ObjectType), key=len, reverse=True)
)


@dataclass
class MigrateItemResult:
    relative_path: str
    status: str
    source_path: str = ""
    target_path: str = ""
    error_code: str = ""
    error_message: str = ""
    artifact: str = ""
    object_type: str = ""
    source_full_name: str = ""
    target_full_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_artifact_name(relative: str) -> tuple[str, str, str]:
    """Derive (artifact, object_type, encoded_full_name) from a package file path."""

    posix = relative.replace("\\", "/")
    artifact = posix.split("/")[0] if "/" in posix else ""
    stem = posix.split("/")[-1]
    for suffix in (".sql", ".json", ".yaml", ".yml"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    upper = stem.upper()
    for object_type in _OBJECT_TYPE_PREFIXES:
        prefix = f"{object_type}_"
        if upper.startswith(prefix):
            return artifact, object_type, stem[len(prefix) :].replace("__", ".")
    return artifact, "", ""


class MigrateExportService:
    """Copy export_staging → export_migrated_staging with storage-path rewrite.

    Names (catalog/schema/table/external-location) are **never** rewritten — the
    utility recreates every securable under its source name. Only storage URLs are
    rewritten (via the mapping file), and captured ``SHOW CREATE`` DDL is passed
    through the replay sanitizers.
    """

    def __init__(
        self,
        *,
        source_root: str,
        target_root: str,
        mappings: Optional[dict[str, Any]] = None,
        volume_root: str | None = None,
        run_id: str = "",
        fs: Any = None,
    ):
        self.source_root = Path(source_root)
        self.target_root = Path(target_root)
        self.mapper = MappingResolver(mappings or {})
        self.run_id = run_id
        self.fs = fs
        self.volume_root = (
            Path(volume_root) / f"run_{run_id}" / "migrated"
            if volume_root and run_id
            else None
        )

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        if not self.source_root.exists():
            raise FileNotFoundError(
                f"Export staging package not found: {self.source_root}"
            )
        results: list[MigrateItemResult] = []
        files = [
            path
            for path in self.source_root.rglob("*")
            if path.is_file()
        ]
        if dry_run:
            return {
                "source_root": str(self.source_root),
                "target_root": str(self.target_root),
                "volume_root": str(self.volume_root) if self.volume_root else "",
                "migrated": len(files),
                "dry_run": True,
                "results": [
                    self._item(
                        relative=path.relative_to(self.source_root).as_posix(),
                        status="DRY_RUN",
                        source_path=str(path),
                    ).to_dict()
                    for path in files
                ],
                "errors": [],
            }

        self.target_root.mkdir(parents=True, exist_ok=True)
        for path in files:
            relative = path.relative_to(self.source_root).as_posix()
            target_relative = self._map_relative_path(relative)
            try:
                content = path.read_text(encoding="utf-8")
                rewritten = self._rewrite_file(relative, content)
                target_path = self.target_root / target_relative
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(rewritten, encoding="utf-8")
                if self.volume_root is not None:
                    self._write_volume(target_relative, rewritten)
                results.append(
                    self._item(
                        relative=target_relative,
                        status="SUCCESS",
                        source_path=str(path),
                        target_path=str(target_path),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    self._item(
                        relative=relative,
                        status="ERROR",
                        source_path=str(path),
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                    )
                )

        manifest = {
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_root": str(self.source_root),
            "target_root": str(self.target_root),
            "volume_root": str(self.volume_root) if self.volume_root else "",
            "file_count": len(results),
            "success_count": sum(item.status == "SUCCESS" for item in results),
        }
        (self.target_root / "migrate_manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        if self.volume_root is not None:
            self._write_volume(
                "migrate_manifest.json",
                json.dumps(manifest, indent=2) + "\n",
            )

        return {
            "source_root": str(self.source_root),
            "target_root": str(self.target_root),
            "volume_root": str(self.volume_root) if self.volume_root else "",
            "migrated": sum(item.status == "SUCCESS" for item in results),
            "dry_run": False,
            "results": [item.to_dict() for item in results],
            "errors": [
                item.to_dict() for item in results if item.status == "ERROR"
            ],
            "manifest": manifest,
        }

    def _item(
        self,
        *,
        relative: str,
        status: str,
        source_path: str = "",
        target_path: str = "",
        error_code: str = "",
        error_message: str = "",
    ) -> MigrateItemResult:
        artifact, object_type, encoded_name = _parse_artifact_name(relative)
        return MigrateItemResult(
            relative_path=relative,
            status=status,
            source_path=source_path,
            target_path=target_path,
            error_code=error_code,
            error_message=error_message,
            artifact=artifact,
            object_type=object_type,
            source_full_name=encoded_name,
            target_full_name=encoded_name,
        )

    def _map_relative_path(self, relative: str) -> str:
        """Names are never mapped, so package paths are copied verbatim."""

        return relative

    def _rewrite_file(self, relative: str, content: str) -> str:
        lowered = relative.lower()
        if lowered.endswith("inventory/objects.json") or lowered.endswith(
            "/objects.json"
        ):
            return self._rewrite_inventory(content)
        artifact, object_type, encoded = _parse_artifact_name(relative)
        if lowered.endswith(".json"):
            rewritten = rewrite_json_text(content, location_resolver=self.mapper)
        else:
            rewritten = rewrite_text(content, location_resolver=self.mapper)
            # Managed-storage / collation / inline-policy stripping is only valid
            # for CREATE DDL. Grants and policy files (``ALTER TABLE ... SET MASK
            # / SET ROW FILTER``) must pass through untouched apart from the path
            # rewrite — otherwise strip_inline_policy_clauses would delete the
            # SET MASK clause from the ALTER statement itself, corrupting it.
            if artifact == "ddl":
                rewritten = strip_managed_storage_clauses(rewritten, object_type)
                if object_type == "STORAGE_CREDENTIAL":
                    # Point the credential at the target-region access connector.
                    rewritten = rewrite_access_connector_id(
                        rewritten, self.mapper.target_access_connector_id() or ""
                    )
        return rewritten

    def _rewrite_inventory(self, content: str) -> str:
        """Keep source identity fields; add target_full_name (=source); rewrite paths."""

        rows = json.loads(content)
        if not isinstance(rows, list):
            return rewrite_json_text(content, location_resolver=self.mapper)
        rewritten_rows: list[Any] = []
        identity_keys = {
            "full_name",
            "source_full_name",
            "object_id",
            "name",
            "owner",
            "catalog",
            "schema",
        }
        for row in rows:
            if not isinstance(row, dict):
                rewritten_rows.append(row)
                continue
            source_full = str(row.get("full_name") or "")
            new_row = dict(row)
            new_row["source_full_name"] = source_full or str(
                row.get("source_full_name") or ""
            )
            # Names are never mapped: target identity == source identity.
            new_row["target_full_name"] = source_full
            # Preserve identity; rewrite storage paths in other string/nested values.
            for key, value in list(new_row.items()):
                if key in identity_keys or key in {
                    "source_full_name",
                    "target_full_name",
                    "object_type",
                }:
                    continue
                if isinstance(value, str):
                    new_row[key] = rewrite_text(value, location_resolver=self.mapper)
                elif isinstance(value, (dict, list)):
                    new_row[key] = rewrite_json_value(
                        value, location_resolver=self.mapper
                    )
            rewritten_rows.append(new_row)
        return json.dumps(rewritten_rows, indent=2, default=str) + "\n"

    def _write_volume(self, relative: str, content: str) -> str:
        assert self.volume_root is not None
        path = self.volume_root / relative
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return str(path)
        except OSError:
            if self.fs is None:
                return ""
            remote = str(path)
            staging = self.target_root / relative
            try:
                self.fs.mkdirs(remote.rsplit("/", 1)[0])
                self.fs.cp(str(staging), remote, recurse=False)
                return remote
            except Exception:  # noqa: BLE001
                return ""
