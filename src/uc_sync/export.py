"""Export service — write self-describing package to UC Volume and Workspace."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from uc_sync import __version__
from uc_sync.models import UCObject
from uc_sync.sql_ddl import (
    create_ddl_for_object,
    format_ddl_file,
    grant_statements_for_object,
    prefers_show_create,
    render_sql_file,
    show_create_command,
    supports_show_create,
)


@dataclass
class ExportItemResult:
    object_type: str
    full_name: str
    status: str
    definition_hash: str = ""
    metadata_path: str = ""
    ddl_path: str = ""
    grants_path: str = ""
    workspace_ddl_path: str = ""
    workspace_grants_path: str = ""
    error_code: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_hash(obj: UCObject) -> str:
    payload = {
        "object_type": obj.object_type.value,
        "full_name": obj.full_name,
        "definition": obj.definition,
        "properties": obj.properties,
        "owner": obj.owner,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_filename(object_type: str, full_name: str) -> str:
    return f"{object_type}_{full_name.replace('.', '__')}"


class ExportService:
    """Writes export packages under a UC Volume and a Workspace staging tree."""

    def __init__(
        self,
        volume_root: str,
        run_id: str,
        *,
        sql_executor: Any = None,
        workspace_root: str | None = None,
        fs: Any = None,
    ):
        if not volume_root:
            raise ValueError("export_volume_path is required")
        self.run_id = run_id
        self.sql = sql_executor
        self.fs = fs
        self.root = Path(volume_root.rstrip("/")) / f"run_{run_id}"
        default_workspace = (
            "/Workspace/Users/vivek.ravichandiran@databricks.com/"
            f"UCSync/export_staging/{run_id}"
        )
        self.workspace_root = Path(workspace_root or default_workspace)

    def run(self, objects: Iterable[UCObject], dry_run: bool = True) -> dict[str, Any]:
        objects_list = list(objects)
        manifest = {
            "run_id": self.run_id,
            "utility_version": __version__,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "object_count": len(objects_list),
            "dry_run": dry_run,
            "root": str(self.root),
            "workspace_root": str(self.workspace_root),
            "ddl_via_show_create": bool(self.sql),
        }
        if dry_run:
            results = [
                ExportItemResult(
                    object_type=obj.object_type.value,
                    full_name=obj.full_name,
                    status="DRY_RUN",
                    definition_hash=canonical_hash(obj),
                ).to_dict()
                for obj in objects_list
            ]
            return {
                "manifest": manifest,
                "exported": len(objects_list),
                "path": str(self.root),
                "workspace_path": str(self.workspace_root),
                "dry_run": True,
                "results": results,
                "errors": [],
                "ddl_files": 0,
                "grant_files": 0,
            }

        for sub in (
            "inventory",
            "ddl",
            "metadata",
            "grants",
            "bindings",
            "validation",
            "checksums",
            "logs",
        ):
            self._ensure_dir(sub)

        inventory_rows = []
        checksums = {}
        results: list[ExportItemResult] = []
        all_object_ddls: list[str] = []
        all_table_ddls: list[str] = []
        all_grant_ddls: list[str] = []
        ddl_files = 0
        grant_files = 0
        ddl_by_source: dict[str, int] = {}

        for obj in objects_list:
            try:
                digest = canonical_hash(obj)
                checksums[obj.full_name] = digest
                inventory_rows.append(
                    {**obj.to_dict(), "source_definition_hash": digest}
                )
                stem = _safe_filename(obj.object_type.value, obj.full_name)
                meta_rel = f"metadata/{stem}.json"
                meta_paths = self._write_text(
                    meta_rel,
                    json.dumps(obj.to_dict(), indent=2, default=str) + "\n",
                )

                ddl_path = ""
                workspace_ddl_path = ""
                grants_path = ""
                workspace_grants_path = ""
                warnings: list[str] = []

                ddl_sql, ddl_source = self._capture_object_ddl(obj, warnings)
                if ddl_sql:
                    ddl_rel = f"ddl/{stem}.sql"
                    ddl_paths = self._write_text(ddl_rel, ddl_sql)
                    ddl_path = ddl_paths.get("volume", "")
                    workspace_ddl_path = ddl_paths.get("workspace", "")
                    all_object_ddls.append(ddl_sql.rstrip() + "\n")
                    if prefers_show_create(obj.object_type) or obj.object_type.value in {
                        "TABLE",
                        "EXTERNAL_TABLE",
                        "VIEW",
                        "DYNAMIC_VIEW",
                        "METRIC_VIEW",
                        "MATERIALIZED_VIEW",
                        "STREAMING_TABLE",
                        "FUNCTION",
                    }:
                        all_table_ddls.append(ddl_sql.rstrip() + "\n")
                    ddl_files += 1
                    ddl_by_source[ddl_source or "UNKNOWN"] = (
                        ddl_by_source.get(ddl_source or "UNKNOWN", 0) + 1
                    )

                grant_sql_statements = grant_statements_for_object(obj)
                if grant_sql_statements:
                    grant_body = render_sql_file(
                        grant_sql_statements,
                        header=(
                            f"Grants for {obj.object_type.value} {obj.full_name}"
                        ),
                    )
                    grant_rel = f"grants/{stem}.sql"
                    grant_paths = self._write_text(grant_rel, grant_body)
                    grants_path = grant_paths.get("volume", "")
                    workspace_grants_path = grant_paths.get("workspace", "")
                    all_grant_ddls.append(grant_body.rstrip() + "\n")
                    grant_files += 1

                status = "SUCCESS"
                error_code = ""
                error_message = ""
                if warnings:
                    status = "SUCCESS_WITH_WARNINGS"
                    error_code = "DDL_PARTIAL"
                    error_message = "; ".join(warnings)

                results.append(
                    ExportItemResult(
                        object_type=obj.object_type.value,
                        full_name=obj.full_name,
                        status=status,
                        definition_hash=digest,
                        metadata_path=meta_paths.get("volume")
                        or meta_paths.get("workspace", ""),
                        ddl_path=ddl_path,
                        grants_path=grants_path,
                        workspace_ddl_path=workspace_ddl_path,
                        workspace_grants_path=workspace_grants_path,
                        error_code=error_code,
                        error_message=error_message,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - report per-object errors
                results.append(
                    ExportItemResult(
                        object_type=obj.object_type.value,
                        full_name=obj.full_name,
                        status="ERROR",
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                    )
                )

        self._write_text(
            "inventory/objects.json",
            json.dumps(inventory_rows, indent=2, default=str) + "\n",
        )
        self._write_text(
            "checksums/sha256.json",
            json.dumps(checksums, indent=2) + "\n",
        )
        if all_object_ddls:
            self._write_text(
                "ddl/all_objects.sql",
                render_sql_file(
                    [block.rstrip() for block in all_object_ddls],
                    header=f"CREATE DDLs for run {self.run_id}",
                ),
            )
        if all_table_ddls:
            self._write_text(
                "ddl/all_tables.sql",
                render_sql_file(
                    [block.rstrip() for block in all_table_ddls],
                    header=(
                        f"Table/view/function CREATE DDLs for run {self.run_id}"
                    ),
                ),
            )
        if all_grant_ddls:
            self._write_text(
                "grants/all_grants.sql",
                render_sql_file(
                    [block.rstrip() for block in all_grant_ddls],
                    header=f"Grant DDLs for run {self.run_id}",
                ),
            )
        manifest["ddl_files"] = ddl_files
        manifest["grant_files"] = grant_files
        manifest["ddl_by_source"] = ddl_by_source
        self._write_text(
            "manifest.json",
            json.dumps(manifest, indent=2) + "\n",
        )
        return {
            "manifest": manifest,
            "exported": sum(
                item.status.startswith("SUCCESS") for item in results
            ),
            "path": str(self.root),
            "workspace_path": str(self.workspace_root),
            "dry_run": False,
            "results": [item.to_dict() for item in results],
            "errors": [
                item.to_dict()
                for item in results
                if item.status == "ERROR"
            ],
            "ddl_files": ddl_files,
            "grant_files": grant_files,
            "ddl_by_source": ddl_by_source,
        }

    def _capture_object_ddl(
        self, obj: UCObject, warnings: list[str]
    ) -> tuple[str | None, str | None]:
        """Return (ddl_text, source) using SHOW CREATE when possible."""

        tried_show = False
        if self.sql is not None and supports_show_create(obj.object_type):
            tried_show = True
            try:
                ddl = self._capture_show_create(obj)
                return ddl, "SHOW_CREATE"
            except Exception as exc:  # noqa: BLE001
                if prefers_show_create(obj.object_type):
                    warnings.append(f"SHOW_CREATE_FAILED: {exc}")
                # Optional SHOW CREATE types fall through to synthesis quietly.

        synthesized = create_ddl_for_object(obj)
        if synthesized:
            source = (
                "SYNTHESIZED_MANUAL"
                if obj.object_type.value == "STORAGE_CREDENTIAL"
                and "MANUAL:" in synthesized
                else "SYNTHESIZED"
            )
            return (
                format_ddl_file(
                    obj,
                    synthesized,
                    source=source,
                    command="",
                ),
                source,
            )

        if tried_show and prefers_show_create(obj.object_type):
            warnings.append("DDL_UNAVAILABLE: SHOW CREATE failed and no synthesis")
        return None, None

    def _capture_show_create(self, obj: UCObject) -> str:
        command = show_create_command(obj.object_type, obj.full_name)
        if self.sql is None:
            raise RuntimeError(
                "sql_executor is required to capture SHOW CREATE DDLs"
            )
        if hasattr(self.sql, "show_create") and prefers_show_create(
            obj.object_type
        ):
            # SparkSqlExecutor.show_create historically only knows table/function.
            ddl = self.sql.show_create(obj.object_type.value, obj.full_name)
        elif hasattr(self.sql, "execute"):
            rows = self.sql.execute(command)
            if not rows:
                raise RuntimeError(
                    f"SHOW CREATE returned no rows for {obj.full_name}"
                )
            first = rows[0]
            ddl = str(first[0] if not isinstance(first, str) else first)
        else:
            raise RuntimeError("sql_executor cannot execute SHOW CREATE")
        return format_ddl_file(
            obj,
            str(ddl),
            source="SHOW_CREATE",
            command=command,
        )

    def _ensure_dir(self, relative: str) -> None:
        for root in (self.workspace_root, self.root):
            try:
                (root / relative).mkdir(parents=True, exist_ok=True)
            except OSError:
                if root == self.root and self.fs is not None:
                    try:
                        self.fs.mkdirs(str(root / relative))
                    except Exception:  # noqa: BLE001
                        pass

    def _write_text(self, relative: str, content: str) -> dict[str, str]:
        """Write the same relative path under workspace and volume roots."""

        written: dict[str, str] = {}
        workspace_path = self.workspace_root / relative
        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        workspace_path.write_text(content, encoding="utf-8")
        written["workspace"] = str(workspace_path)

        volume_path = self.root / relative
        try:
            volume_path.parent.mkdir(parents=True, exist_ok=True)
            volume_path.write_text(content, encoding="utf-8")
            written["volume"] = str(volume_path)
            return written
        except OSError:
            pass

        if self.fs is not None:
            remote = str(volume_path)
            try:
                self.fs.mkdirs(remote.rsplit("/", 1)[0])
                self.fs.cp(str(workspace_path), remote, recurse=False)
                written["volume"] = remote
            except Exception:  # noqa: BLE001 - workspace copy still usable
                pass
        return written
