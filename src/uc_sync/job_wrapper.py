"""Create / update / run the UC Sync Databricks Job."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from uc_sync.security import redact
from uc_sync.components import resolve_components
from uc_sync.config import derive_ops_paths

DEFAULT_NOTEBOOK_PATH = "/Repos/UCSync/notebooks/UC_Sync_Main"
DEFAULT_TASK_KEY = "uc_sync"


@dataclass
class JobCreateResult:
    job_id: int
    job_name: str
    notebook_path: str
    parameters: dict[str, str]
    run_id: Optional[int] = None
    run_page_url: Optional[str] = None
    created: bool = True
    updated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UCSyncJobParams:
    """Parameters forwarded to notebooks/UC_Sync_Main as job base_parameters."""

    execution_mode: str = "LOCAL"
    mode: str = "SYNC"
    dry_run: str = "true"
    catalog_mapping_json: str = ""
    catalog_mapping_path: str = ""
    location_mapping_csv_path: str = ""
    catalogs: str = ""
    schemas: str = ""
    components: str = "ALL"
    include_object_types: str = ""
    include_parents: str = "true"
    exclude_object_types: str = "MODEL"
    include_regex: str = ""
    exclude_regex: str = ".*_TEMP$"
    # Single ops base — the four artifact locations below are derived from it when
    # UCSync's operational-artifact locations. The user supplies three inputs:
    # ops_catalog + ops_schema (audit/state tables) and output_volume_path
    # (exports + reports). The four fields below are derived from them before
    # validation (see uc_sync.config.derive_ops_paths) and emitted as concrete,
    # visible values in the job's base_parameters.
    ops_catalog: str = ""
    ops_schema: str = ""
    output_volume_path: str = ""
    export_volume_path: str = ""
    report_volume_path: str = ""
    audit_table: str = ""
    state_table: str = ""
    import_package_path: str = ""
    config_path: str = ""
    # Cross-workspace only — leave blank for LOCAL
    source_workspace_url: str = ""
    source_oauth_secret_scope: str = ""
    source_client_id_secret_key: str = ""
    source_client_secret_key: str = ""
    target_workspace_url: str = ""
    target_oauth_secret_scope: str = ""
    target_client_id_secret_key: str = ""
    target_client_secret_key: str = ""

    def resolve_ops_paths(self) -> None:
        """Derive the four artifact locations from the three ops inputs.

        Runs before validation so the emitted base_parameters carry concrete,
        visible paths (export/report volume = output_volume_path; audit/state =
        {ops_catalog}.{ops_schema}.uc_sync_audit/_state).
        """

        resolved = derive_ops_paths(
            ops_catalog=self.ops_catalog,
            ops_schema=self.ops_schema,
            output_volume_path=self.output_volume_path,
        )
        self.export_volume_path = resolved["export_volume_path"]
        self.report_volume_path = resolved["report_volume_path"]
        self.audit_table = resolved["audit_table"]
        self.state_table = resolved["state_table"]

    def to_notebook_parameters(self) -> dict[str, str]:
        self.validate()
        mapping = asdict(self)
        return {key: "" if value is None else str(value) for key, value in mapping.items()}

    def validate(self) -> None:
        self.resolve_ops_paths()
        mode = self.mode.upper()
        execution_mode = self.execution_mode.upper()
        if mode not in {
            "INVENTORY",
            "EXPORT",
            "IMPORT",
            "SYNC",
            "COMPARE",
            "VALIDATE",
        }:
            raise ValueError(f"Unsupported mode: {self.mode}")
        if execution_mode not in {"LOCAL", "CROSS_WORKSPACE"}:
            raise ValueError(f"Unsupported execution_mode: {self.execution_mode}")
        if execution_mode == "LOCAL":
            if not (self.catalog_mapping_json.strip() or self.catalog_mapping_path.strip()):
                raise ValueError(
                    "LOCAL mode requires catalog_mapping_json or catalog_mapping_path"
                )
            if self.catalog_mapping_json.strip():
                parsed = json.loads(self.catalog_mapping_json)
                if not isinstance(parsed, dict):
                    raise ValueError("catalog_mapping_json must be a JSON object")
        else:
            required = {
                "source_workspace_url": self.source_workspace_url,
                "source_oauth_secret_scope": self.source_oauth_secret_scope,
                "source_client_id_secret_key": self.source_client_id_secret_key,
                "source_client_secret_key": self.source_client_secret_key,
                "target_workspace_url": self.target_workspace_url,
                "target_oauth_secret_scope": self.target_oauth_secret_scope,
                "target_client_id_secret_key": self.target_client_id_secret_key,
                "target_client_secret_key": self.target_client_secret_key,
            }
            missing = [name for name, value in required.items() if not str(value).strip()]
            if missing:
                raise ValueError(
                    "CROSS_WORKSPACE mode requires: " + ", ".join(missing)
                )
        if not self.output_volume_path.strip():
            raise ValueError(
                "output_volume_path is required (the volume for exports + reports)"
            )
        if not (self.ops_catalog.strip() and self.ops_schema.strip()):
            raise ValueError(
                "ops_catalog and ops_schema are required "
                "(the audit/state tables are derived from them)"
            )
        # Validate component expression early so Job create fails fast.
        resolve_components(
            self.include_object_types or self.components,
            include_parents=str(self.include_parents).lower()
            in {"1", "true", "yes", "y"},
        )


def _sdk_client(
    *,
    host: Optional[str] = None,
    token: Optional[str] = None,
    profile: Optional[str] = None,
    client: Any = None,
) -> Any:
    if client is not None:
        return client
    from databricks.sdk import WorkspaceClient

    if profile:
        return WorkspaceClient(profile=profile)
    if host and token:
        return WorkspaceClient(host=host, token=token)
    # Current Databricks notebook / default auth chain
    return WorkspaceClient()


def _jobs_create(ws: Any, settings: Mapping[str, Any]) -> int:
    """Create a job via SDK objects or the Jobs REST API."""

    payload = dict(settings)
    # Prefer REST — nested dicts are accepted by /api/2.1/jobs/create.
    api_client = getattr(ws, "api_client", None)
    if api_client is not None and hasattr(api_client, "do"):
        response = api_client.do("POST", "/api/2.1/jobs/create", body=payload)
        return int(response["job_id"])

    # SDK fallback: pass the plain settings dict straight through. The Jobs SDK
    # accepts keyword args mirroring the REST body; keeping dicts (rather than
    # typed JobSettings) preserves nested task/cluster shapes.
    response = ws.jobs.create(**payload)
    return int(response.job_id)


def _jobs_reset(ws: Any, job_id: int, settings: Mapping[str, Any]) -> None:
    payload = dict(settings)
    api_client = getattr(ws, "api_client", None)
    if api_client is not None and hasattr(api_client, "do"):
        api_client.do(
            "POST",
            "/api/2.1/jobs/reset",
            body={"job_id": job_id, "new_settings": payload},
        )
        return
    ws.jobs.reset(job_id=job_id, new_settings=payload)


def _jobs_run_now(ws: Any, job_id: int) -> int:
    api_client = getattr(ws, "api_client", None)
    if api_client is not None and hasattr(api_client, "do"):
        response = api_client.do(
            "POST", "/api/2.1/jobs/run-now", body={"job_id": job_id}
        )
        return int(response["run_id"])
    run = ws.jobs.run_now(job_id=job_id)
    return int(getattr(run, "run_id", run))


def _find_job_id_by_name(client: Any, job_name: str) -> Optional[int]:
    try:
        jobs = list(client.jobs.list(name=job_name))
    except TypeError:
        jobs = list(client.jobs.list())
    for job in jobs:
        settings = getattr(job, "settings", None)
        name = getattr(settings, "name", None) if settings else None
        if name == job_name and getattr(job, "job_id", None) is not None:
            return int(job.job_id)
    return None


def _job_cluster_spec(
    *,
    spark_version: str,
    node_type_id: str,
    num_workers: int,
    data_security_mode: str = "USER_ISOLATION",
) -> dict[str, Any]:
    return {
        "job_cluster_key": "uc_sync_cluster",
        "new_cluster": {
            "spark_version": spark_version,
            "node_type_id": node_type_id,
            "num_workers": num_workers,
            "data_security_mode": data_security_mode,
            "spark_conf": {},
            "custom_tags": {"uc_sync": "true"},
        },
    }


def build_job_settings(
    *,
    job_name: str,
    notebook_path: str,
    parameters: Mapping[str, str],
    existing_cluster_id: Optional[str] = None,
    spark_version: str = "15.4.x-scala2.12",
    node_type_id: str = "Standard_DS3_v2",
    num_workers: int = 0,
    data_security_mode: str = "USER_ISOLATION",
    libraries: Optional[list[dict[str, Any]]] = None,
    timeout_seconds: int = 0,
    max_concurrent_runs: int = 1,
    tags: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    notebook_task: dict[str, Any] = {
        "notebook_path": notebook_path,
        "base_parameters": dict(parameters),
    }
    task: dict[str, Any] = {
        "task_key": DEFAULT_TASK_KEY,
        "notebook_task": notebook_task,
        "libraries": libraries
        or [
            {"pypi": {"package": "databricks-sdk>=0.36.0"}},
            {"pypi": {"package": "PyYAML>=6.0"}},
            {"pypi": {"package": "openpyxl>=3.1"}},
        ],
    }
    settings: dict[str, Any] = {
        "name": job_name,
        "tasks": [task],
        "max_concurrent_runs": max_concurrent_runs,
        "tags": dict(tags or {"utility": "uc-sync"}),
    }
    if timeout_seconds:
        settings["timeout_seconds"] = timeout_seconds

    if existing_cluster_id:
        task["existing_cluster_id"] = existing_cluster_id
    else:
        mode = str(data_security_mode or "USER_ISOLATION").upper()
        cluster = _job_cluster_spec(
            spark_version=spark_version,
            node_type_id=node_type_id,
            num_workers=num_workers,
            data_security_mode=mode,
        )
        if mode == "SINGLE_USER" and num_workers == 0:
            # Single-user single-node job cluster needs spark.master local[*].
            # NOTE: single-user (assigned) access mode cannot apply column masks
            # or row filters — use USER_ISOLATION (Standard) or serverless for
            # workloads that migrate policies.
            cluster["new_cluster"]["spark_conf"] = {
                "spark.master": "local[*]",
                "spark.databricks.cluster.profile": "singleNode",
            }
            cluster["new_cluster"]["custom_tags"] = {
                "ResourceClass": "SingleNode",
                "uc_sync": "true",
            }
            cluster["new_cluster"]["num_workers"] = 0
        elif num_workers == 0:
            # Standard (shared) access mode does not support the single-node
            # local[*] profile; ensure at least one worker for a valid cluster.
            cluster["new_cluster"]["num_workers"] = 1
        settings["job_clusters"] = [cluster]
        task["job_cluster_key"] = "uc_sync_cluster"

    return settings


def create_uc_sync_job(
    *,
    job_name: str = "UC-Sync",
    notebook_path: str = DEFAULT_NOTEBOOK_PATH,
    params: Optional[UCSyncJobParams] = None,
    run_now: bool = False,
    update_if_exists: bool = True,
    existing_cluster_id: Optional[str] = None,
    spark_version: str = "15.4.x-scala2.12",
    node_type_id: str = "Standard_DS3_v2",
    num_workers: int = 0,
    data_security_mode: str = "USER_ISOLATION",
    host: Optional[str] = None,
    token: Optional[str] = None,
    profile: Optional[str] = None,
    client: Any = None,
    tags: Optional[Mapping[str, str]] = None,
) -> JobCreateResult:
    """Create (or update) a Databricks Job that runs UC_Sync_Main.

    LOCAL mode example::

        create_uc_sync_job(
            job_name="UC-Sync-Local-Sandbox",
            params=UCSyncJobParams(
                execution_mode="LOCAL",
                mode="SYNC",
                catalog_mapping_json='{"ril_sandbox":"ril_sandbox_copy"}',
                dry_run="true",
            ),
            run_now=True,
            profile="uc-target",
        )
    """
    job_params = params or UCSyncJobParams()
    notebook_parameters = job_params.to_notebook_parameters()
    settings = build_job_settings(
        job_name=job_name,
        notebook_path=notebook_path,
        parameters=notebook_parameters,
        existing_cluster_id=existing_cluster_id,
        spark_version=spark_version,
        node_type_id=node_type_id,
        num_workers=num_workers,
        data_security_mode=data_security_mode,
        tags=tags,
    )

    ws = _sdk_client(host=host, token=token, profile=profile, client=client)
    existing_id = _find_job_id_by_name(ws, job_name)
    created = False
    updated = False

    try:
        if existing_id is not None and update_if_exists:
            _jobs_reset(ws, existing_id, settings)
            job_id = existing_id
            updated = True
        elif existing_id is not None and not update_if_exists:
            job_id = existing_id
        else:
            job_id = _jobs_create(ws, settings)
            created = True
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(redact(f"Failed to create/update UC Sync job: {exc}")) from exc

    run_id: Optional[int] = None
    run_page_url: Optional[str] = None
    if run_now:
        try:
            run_id = _jobs_run_now(ws, job_id)
            # Best-effort URL for convenience in notebooks
            host_url = getattr(getattr(ws, "config", None), "host", None) or host or ""
            if host_url and run_id is not None:
                run_page_url = (
                    f"{host_url.rstrip('/')}/#job/{job_id}/run/{run_id}"
                )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                redact(
                    f"Job {job_id} was created/updated but run_now failed: {exc}"
                )
            ) from exc

    return JobCreateResult(
        job_id=job_id,
        job_name=job_name,
        notebook_path=notebook_path,
        parameters=notebook_parameters,
        run_id=run_id,
        run_page_url=run_page_url,
        created=created,
        updated=updated,
    )


def create_local_sync_job(
    *,
    catalog_mapping_json: str = "",
    catalog_mapping_path: str = "",
    location_mapping_csv_path: str = "",
    job_name: str = "UC-Sync-Local",
    mode: str = "SYNC",
    dry_run: str = "true",
    ops_catalog: str = "",
    ops_schema: str = "",
    output_volume_path: str = "",
    notebook_path: str = DEFAULT_NOTEBOOK_PATH,
    run_now: bool = False,
    **kwargs: Any,
) -> JobCreateResult:
    """Convenience wrapper for same-workspace catalog-to-catalog sync jobs.

    Provide ``ops_catalog`` + ``ops_schema`` (audit/state tables) and
    ``output_volume_path`` (exports + reports).
    """
    return create_uc_sync_job(
        job_name=job_name,
        notebook_path=notebook_path,
        params=UCSyncJobParams(
            execution_mode="LOCAL",
            mode=mode,
            dry_run=dry_run,
            catalog_mapping_json=catalog_mapping_json,
            catalog_mapping_path=catalog_mapping_path,
            location_mapping_csv_path=location_mapping_csv_path,
            ops_catalog=ops_catalog,
            ops_schema=ops_schema,
            output_volume_path=output_volume_path,
        ),
        run_now=run_now,
        **kwargs,
    )


LOCAL_STAGE_ALIASES = {
    "INVENTORY": ("INVENTORY",),
    "EXPORT": ("EXPORT",),
    "IMPORT": ("IMPORT",),
    "ALL": ("INVENTORY", "EXPORT", "IMPORT"),
    "SYNC": ("SYNC",),
}


def resolve_local_stages(stages: str) -> list[str]:
    """Expand a stage selector into concrete UC_Sync_Main modes."""

    raw = str(stages or "ALL").strip().upper()
    if not raw:
        raw = "ALL"
    selected: list[str] = []
    for token in raw.replace(";", ",").split(","):
        part = token.strip().upper()
        if not part:
            continue
        if part not in LOCAL_STAGE_ALIASES:
            raise ValueError(
                f"Unsupported local stage '{part}'. "
                "Use INVENTORY, EXPORT, IMPORT, ALL, or SYNC."
            )
        for mode in LOCAL_STAGE_ALIASES[part]:
            if mode not in selected:
                selected.append(mode)
    return selected


def create_local_stage_jobs(
    *,
    stages: str = "ALL",
    catalog_mapping_json: str = "",
    catalog_mapping_path: str = "",
    location_mapping_csv_path: str = "",
    catalogs: str = "",
    schemas: str = "",
    components: str = "ALL",
    job_name_prefix: str = "UC-Sync-Local",
    dry_run: str = "true",
    ops_catalog: str = "",
    ops_schema: str = "",
    output_volume_path: str = "",
    import_package_path: str = "",
    notebook_path: str = DEFAULT_NOTEBOOK_PATH,
    run_now: bool = False,
    update_if_exists: bool = True,
    existing_cluster_id: Optional[str] = None,
    **kwargs: Any,
) -> list[JobCreateResult]:
    """Create one LOCAL job per selected stage (inventory / export / import / all).

    Example::

        create_local_stage_jobs(
            stages="ALL",
            catalog_mapping_json='{"ril_sandbox":"ril_sandbox_ucsync_local"}',
            location_mapping_csv_path="/Volumes/.../config/location-mapping.csv",
            catalogs="ril_sandbox",
            schemas="ril_sandbox.ucsync_local_01",
            ops_catalog="catalog_2_pih5aa",
            ops_schema="wsmig_operations",
            output_volume_path="/Volumes/catalog_2_pih5aa/wsmig_operations/uc_exports",
            existing_cluster_id="0813-072811-phmehy1u",
            dry_run="false",
            run_now=False,
        )

    ``ops_catalog`` + ``ops_schema`` set the audit/state tables; ``output_volume_path``
    is the volume for exports + reports.
    """

    modes = resolve_local_stages(stages)
    results: list[JobCreateResult] = []
    for mode in modes:
        suffix = mode.title().replace("_", "-")
        job_name = (
            job_name_prefix
            if mode == "SYNC" and stages.strip().upper() in {"SYNC", ""}
            else f"{job_name_prefix}-{suffix}"
        )
        results.append(
            create_uc_sync_job(
                job_name=job_name,
                notebook_path=notebook_path,
                params=UCSyncJobParams(
                    execution_mode="LOCAL",
                    mode=mode,
                    dry_run=dry_run,
                    catalog_mapping_json=catalog_mapping_json,
                    catalog_mapping_path=catalog_mapping_path,
                    location_mapping_csv_path=location_mapping_csv_path,
                    catalogs=catalogs,
                    schemas=schemas,
                    components=components,
                    ops_catalog=ops_catalog,
                    ops_schema=ops_schema,
                    output_volume_path=output_volume_path,
                    import_package_path=import_package_path,
                ),
                run_now=run_now,
                update_if_exists=update_if_exists,
                existing_cluster_id=existing_cluster_id,
                tags={"utility": "uc-sync", "execution_mode": "LOCAL", "stage": mode},
                **kwargs,
            )
        )
    return results

