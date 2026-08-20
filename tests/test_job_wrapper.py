"""Unit tests for the UC Sync job creation wrapper."""

from __future__ import annotations

import json

from uc_sync.config import derive_ops_paths
from uc_sync.job_wrapper import (
    UCSyncJobParams,
    build_job_settings,
    create_local_stage_jobs,
    create_uc_sync_job,
    resolve_local_stages,
)


def test_derive_ops_paths_from_three_inputs():
    out = derive_ops_paths(
        ops_catalog="c", ops_schema="s", output_volume_path="/Volumes/c/s/out"
    )
    # export + report volume are both the output volume; tables use standard names.
    assert out["export_volume_path"] == "/Volumes/c/s/out"
    assert out["report_volume_path"] == "/Volumes/c/s/out"
    assert out["audit_table"] == "c.s.uc_sync_audit"
    assert out["state_table"] == "c.s.uc_sync_state"


def test_derive_ops_paths_blank_inputs_yield_blanks():
    # Missing inputs -> blanks, so downstream validation fails fast.
    assert derive_ops_paths() == {
        "export_volume_path": "",
        "report_volume_path": "",
        "audit_table": "",
        "state_table": "",
    }
    # Volume without catalog/schema -> tables blank but volume still set.
    partial = derive_ops_paths(output_volume_path="/Volumes/c/s/out")
    assert partial["export_volume_path"] == "/Volumes/c/s/out"
    assert partial["audit_table"] == ""


def test_missing_output_volume_fails_fast():
    # ops base present but no output volume -> validation trips at create time.
    try:
        UCSyncJobParams(
            execution_mode="LOCAL",
            catalog_mapping_json='{"a":"b"}',
            ops_catalog="c",
            ops_schema="s",
        ).validate()
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "output_volume_path" in str(exc)


def test_missing_ops_catalog_schema_fails_fast():
    # output volume present but no ops catalog/schema -> validation trips.
    try:
        UCSyncJobParams(
            execution_mode="LOCAL",
            catalog_mapping_json='{"a":"b"}',
            output_volume_path="/Volumes/c/s/out",
        ).validate()
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "ops_catalog" in str(exc)


class _FakeJobs:
    def __init__(self):
        self.created = []
        self.reset_calls = []
        self.run_calls = []
        self._jobs = []

    def list(self, name=None):
        if name is None:
            return list(self._jobs)
        return [job for job in self._jobs if job.settings.name == name]

    def create(self, **settings):
        job_id = 1000 + len(self._jobs)
        job = type(
            "Job",
            (),
            {
                "job_id": job_id,
                "settings": type("Settings", (), {"name": settings["name"]})(),
            },
        )()
        self._jobs.append(job)
        self.created.append(settings)
        return type("CreateResponse", (), {"job_id": job_id})()

    def reset(self, job_id, new_settings):
        self.reset_calls.append((job_id, new_settings))
        for job in self._jobs:
            if job.job_id == job_id:
                job.settings.name = new_settings["name"]
                return None
        raise RuntimeError(f"unknown job {job_id}")

    def run_now(self, job_id):
        self.run_calls.append(job_id)
        return type("Run", (), {"run_id": 555})()


class _FakeClient:
    def __init__(self):
        self.jobs = _FakeJobs()
        self.config = type(
            "Cfg", (), {"host": "https://example.azuredatabricks.net"}
        )()


def test_local_params_require_mapping():
    try:
        UCSyncJobParams(execution_mode="LOCAL", catalog_mapping_json="").validate()
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "catalog_mapping" in str(exc)


def test_cross_workspace_params_require_auth():
    try:
        UCSyncJobParams(execution_mode="CROSS_WORKSPACE").validate()
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "CROSS_WORKSPACE" in str(exc)


def test_build_job_settings_includes_notebook_parameters():
    params = UCSyncJobParams(
        execution_mode="LOCAL",
        catalog_mapping_json='{"a":"b"}',
        ops_catalog="ops_cat",
        ops_schema="ops_sch",
        output_volume_path="/Volumes/ops_cat/ops_sch/out",
    ).to_notebook_parameters()
    # The three inputs derive the four artifact locations.
    assert params["export_volume_path"] == "/Volumes/ops_cat/ops_sch/out"
    assert params["report_volume_path"] == "/Volumes/ops_cat/ops_sch/out"
    assert params["audit_table"] == "ops_cat.ops_sch.uc_sync_audit"
    assert params["state_table"] == "ops_cat.ops_sch.uc_sync_state"
    settings = build_job_settings(
        job_name="UC-Sync-Test",
        notebook_path="/Repos/UCSync/notebooks/UC_Sync_Main",
        parameters=params,
    )
    assert settings["name"] == "UC-Sync-Test"
    task = settings["tasks"][0]
    assert task["notebook_task"]["base_parameters"]["execution_mode"] == "LOCAL"
    assert (
        task["notebook_task"]["base_parameters"]["catalog_mapping_json"]
        == '{"a":"b"}'
    )
    assert task["job_cluster_key"] == "uc_sync_cluster"
    assert "job_clusters" in settings


def test_create_uc_sync_job_creates_and_runs():
    client = _FakeClient()
    result = create_uc_sync_job(
        job_name="UC-Sync-Local",
        params=UCSyncJobParams(
            execution_mode="LOCAL",
            mode="INVENTORY",
            catalog_mapping_json=json.dumps(
                {"ril_sandbox": "ril_sandbox_copy"}
            ),
            ops_catalog="ops_cat",
            ops_schema="ops_sch",
            output_volume_path="/Volumes/ops_cat/ops_sch/out",
        ),
        run_now=True,
        client=client,
    )
    assert result.created is True
    assert result.job_id == 1000
    assert result.run_id == 555
    assert result.run_page_url.endswith("/#job/1000/run/555")
    assert (
        client.jobs.created[0]["tasks"][0]["notebook_task"]["base_parameters"][
            "mode"
        ]
        == "INVENTORY"
    )


class _FakeApiClient:
    def __init__(self, jobs: _FakeJobs):
        self._jobs = jobs

    def do(self, method, path, body=None):
        body = body or {}
        if method == "POST" and path.endswith("/jobs/create"):
            created = self._jobs.create(**body)
            return {"job_id": created.job_id}
        if method == "POST" and path.endswith("/jobs/reset"):
            self._jobs.reset(body["job_id"], body["new_settings"])
            return {}
        if method == "POST" and path.endswith("/jobs/run-now"):
            run = self._jobs.run_now(body["job_id"])
            return {"run_id": run.run_id}
        raise RuntimeError(f"unexpected {method} {path}")


class _FakeClientWithApi(_FakeClient):
    def __init__(self):
        super().__init__()
        self.api_client = _FakeApiClient(self.jobs)


def test_create_via_api_client_rest_path():
    client = _FakeClientWithApi()
    result = create_uc_sync_job(
        job_name="UC-Sync-Local-Rest",
        params=UCSyncJobParams(
            execution_mode="LOCAL",
            mode="EXPORT",
            catalog_mapping_json='{"a":"b"}',
            ops_catalog="ops_cat",
            ops_schema="ops_sch",
            output_volume_path="/Volumes/ops_cat/ops_sch/out",
        ),
        run_now=True,
        client=client,
    )
    assert result.created is True
    assert result.run_id == 555
    assert client.jobs.created[0]["tasks"][0]["notebook_task"]["base_parameters"][
        "mode"
    ] == "EXPORT"


def test_create_uc_sync_job_updates_existing():
    client = _FakeClient()
    first = create_uc_sync_job(
        job_name="UC-Sync-Local",
        params=UCSyncJobParams(
            execution_mode="LOCAL",
            catalog_mapping_json='{"a":"b"}',
            ops_catalog="ops_cat",
            ops_schema="ops_sch",
            output_volume_path="/Volumes/ops_cat/ops_sch/out",
        ),
        client=client,
    )
    second = create_uc_sync_job(
        job_name="UC-Sync-Local",
        params=UCSyncJobParams(
            execution_mode="LOCAL",
            mode="COMPARE",
            catalog_mapping_json='{"a":"b"}',
            ops_catalog="ops_cat",
            ops_schema="ops_sch",
            output_volume_path="/Volumes/ops_cat/ops_sch/out",
        ),
        update_if_exists=True,
        client=client,
    )
    assert first.created is True
    assert second.updated is True
    assert second.job_id == first.job_id
    assert client.jobs.reset_calls
    assert (
        client.jobs.reset_calls[0][1]["tasks"][0]["notebook_task"][
            "base_parameters"
        ]["mode"]
        == "COMPARE"
    )


def test_resolve_local_stages_all_and_aliases():
    assert resolve_local_stages("ALL") == ["INVENTORY", "EXPORT", "IMPORT"]
    assert resolve_local_stages("inventory,import") == ["INVENTORY", "IMPORT"]
    assert resolve_local_stages("SYNC") == ["SYNC"]
    try:
        resolve_local_stages("BAD")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Unsupported local stage" in str(exc)


def test_create_local_stage_jobs_creates_three_for_all():
    client = _FakeClient()
    results = create_local_stage_jobs(
        stages="ALL",
        catalog_mapping_json='{"ril_sandbox":"ril_sandbox_ucsync_local"}',
        location_mapping_csv_path="/Volumes/x/config/location-mapping.csv",
        catalogs="ril_sandbox",
        schemas="ril_sandbox.ucsync_local_01",
        ops_catalog="ops_cat",
        ops_schema="ops_sch",
        output_volume_path="/Volumes/ops_cat/ops_sch/out",
        dry_run="false",
        run_now=False,
        client=client,
    )
    assert len(results) == 3
    # Every stage job carries the derived ops locations in its base_parameters.
    for item in results:
        assert item.parameters["export_volume_path"] == "/Volumes/ops_cat/ops_sch/out"
        assert item.parameters["state_table"] == "ops_cat.ops_sch.uc_sync_state"
    assert [item.parameters["mode"] for item in results] == [
        "INVENTORY",
        "EXPORT",
        "IMPORT",
    ]
    assert [item.job_name for item in results] == [
        "UC-Sync-Local-Inventory",
        "UC-Sync-Local-Export",
        "UC-Sync-Local-Import",
    ]
    assert all(item.created for item in results)
    assert all(
        item.parameters["execution_mode"] == "LOCAL" for item in results
    )
    assert (
        results[0].parameters["catalog_mapping_json"]
        == '{"ril_sandbox":"ril_sandbox_ucsync_local"}'
    )
