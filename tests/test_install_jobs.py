"""Tests for the declarative job-spec installer (notebooks/00_Install_Jobs)."""

from __future__ import annotations

import json

import pytest

from uc_sync.config import APPLY_TOGGLES, CREATE_TOGGLES
from uc_sync.install_jobs import (
    JOB_LABELS,
    JOB_SPECS,
    _default_specs_dir,
    install_jobs,
    load_job_spec,
    resolve_job_keys,
)


def _values(**overrides):
    base = {
        "notebook_dir": "/Workspace/Repos/uc_migration/notebooks",
        "job_name_prefix": "UC-Gov-Migration",
        "connectivity_mode": "direct",
        "catalogs": "ai27_uc_sales",
        "schemas": "",
        "output_volume_path": "/Volumes/ops/uc/exports",
        "ops_catalog": "ops",
        "ops_schema": "uc",
        "mapping_file_path": "/Volumes/ops/uc/config/mapping.csv",
        "run_id": "12345",
        "source_workspace_url": "",
        "source_oauth_secret_scope": "",
        "source_client_id_secret_key": "",
        "source_client_secret_key": "",
        "existing_cluster_id": "",
        "spark_version": "15.4.x-scala2.12",
        "node_type_id": "Standard_DS3_v2",
    }
    for t in (*CREATE_TOGGLES, *APPLY_TOGGLES):
        base[t] = "true"
    base.update(overrides)
    return base


def test_every_spec_file_exists_and_is_valid_json():
    specs_dir = _default_specs_dir()
    for filename in JOB_SPECS.values():
        spec = json.loads((specs_dir / filename).read_text())
        assert spec["name"]
        assert spec["tasks"]


def test_resolve_job_keys_from_labels_and_keys():
    assert resolve_job_keys("Airgap Import (target)") == ["airgap_import_target"]
    assert resolve_job_keys("e2e_dry_run,e2e_live") == ["e2e_dry_run", "e2e_live"]
    # de-dupes while preserving order
    assert resolve_job_keys("End-to-end Live,e2e_live") == ["e2e_live"]
    assert set(JOB_LABELS.values()) == set(JOB_SPECS)


def test_resolve_job_keys_rejects_unknown():
    with pytest.raises(ValueError):
        resolve_job_keys("not_a_job")


def test_run_as_spn_applied_to_target_jobs_only():
    spn = "11111111-2222-3333-4444-555555555555"
    for key in ("airgap_import_target", "e2e_dry_run", "e2e_live"):
        spec = load_job_spec(key, _values(run_as_spn=spn))
        assert spec["run_as"] == {"service_principal_name": spn}
    # The source-only inventory+export job is never run as the target SP.
    assert "run_as" not in load_job_spec("airgap_source", _values(run_as_spn=spn))


def test_run_as_omitted_when_blank():
    # A blank SP must not emit an empty run_as (the Jobs API would reject it).
    for key in ("airgap_import_target", "e2e_dry_run", "e2e_live"):
        assert "run_as" not in load_job_spec(key, _values(run_as_spn=""))


def test_import_table_filter_and_catalog_mapping_substituted():
    spec = load_job_spec(
        "e2e_live",
        _values(filter_tables="ai27_uc_gov_src.hr.employees",
                catalog_mapping_json='{"ai27_uc_gov_src":"ai27_uc_gov_copy"}'),
    )
    params = spec["tasks"][-1]["notebook_task"]["base_parameters"]
    assert params["filter_tables"] == "ai27_uc_gov_src.hr.employees"
    assert params["catalog_mapping_json"] == '{"ai27_uc_gov_src":"ai27_uc_gov_copy"}'
    # The duplicate catalog/schema import filters were removed.
    assert "filter_catalogs" not in params
    assert "filter_schemas" not in params


def test_airgap_source_two_tasks_chained_by_job_run_id():
    spec = load_job_spec("airgap_source", _values())
    assert spec["name"] == "UC-Gov-Migration - Airgap Inventory+Export (source)"
    keys = [t["task_key"] for t in spec["tasks"]]
    assert keys == ["inventory", "export"]
    export = spec["tasks"][1]
    assert export["depends_on"] == [{"task_key": "inventory"}]
    inv_params = spec["tasks"][0]["notebook_task"]["base_parameters"]
    assert inv_params["connectivity_mode"] == "airgap"
    assert inv_params["run_id"] == "{{job.run_id}}"  # dynamic ref left intact
    assert inv_params["catalogs"] == "ai27_uc_sales"
    assert spec["tasks"][0]["notebook_task"]["notebook_path"].endswith("/01_Inventory")


def test_airgap_import_uses_run_id_job_parameter():
    spec = load_job_spec("airgap_import_target", _values(run_id="99999"))
    assert spec["parameters"] == [{"name": "run_id", "default": "99999"}]
    params = spec["tasks"][0]["notebook_task"]["base_parameters"]
    assert params["run_id"] == "{{job.parameters.run_id}}"
    assert params["dry_run"] == "false"
    assert params["create_tables"] == "true"


def test_e2e_dry_run_and_live_differ_only_in_dry_run():
    dry = load_job_spec("e2e_dry_run", _values())
    live = load_job_spec("e2e_live", _values())
    for spec in (dry, live):
        assert [t["task_key"] for t in spec["tasks"]] == [
            "inventory",
            "export",
            "import_bundle",
        ]
    dry_import = dry["tasks"][-1]["notebook_task"]["base_parameters"]
    live_import = live["tasks"][-1]["notebook_task"]["base_parameters"]
    assert dry_import["dry_run"] == "true"
    assert live_import["dry_run"] == "false"
    # end-to-end chains all three tasks on the same job run id
    assert dry_import["run_id"] == "{{job.run_id}}"


def test_toggles_flow_into_import_params():
    spec = load_job_spec(
        "e2e_live",
        _values(create_volumes="false", apply_masks_row_filters="false"),
    )
    params = spec["tasks"][-1]["notebook_task"]["base_parameters"]
    assert params["create_volumes"] == "false"
    assert params["apply_masks_row_filters"] == "false"
    assert params["create_tables"] == "true"


def test_existing_cluster_override_replaces_job_cluster():
    spec = load_job_spec("e2e_dry_run", _values(existing_cluster_id="0813-abc"))
    assert "job_clusters" not in spec
    for task in spec["tasks"]:
        assert "job_cluster_key" not in task
        assert task["existing_cluster_id"] == "0813-abc"


def test_new_job_cluster_is_user_isolation():
    spec = load_job_spec("e2e_dry_run", _values())
    cluster = spec["job_clusters"][0]["new_cluster"]
    assert cluster["data_security_mode"] == "USER_ISOLATION"
    assert cluster["spark_version"] == "15.4.x-scala2.12"
    assert cluster["node_type_id"] == "Standard_DS3_v2"


class _FakeJobs:
    def __init__(self):
        self.created = []
        self.reset = []
        self._next_id = 100

    def list(self, name=None):
        return []

    def create(self, **payload):
        self._next_id += 1
        self.created.append(payload)
        return type("R", (), {"job_id": self._next_id})()


class _FakeWorkspace:
    def __init__(self):
        self.jobs = _FakeJobs()
        self.api_client = None
        self.config = type("C", (), {"host": "https://example.databricks.com"})()


def test_install_jobs_creates_selected_jobs():
    ws = _FakeWorkspace()
    results = install_jobs(
        job_keys=["airgap_source", "e2e_live"],
        values=_values(),
        client=ws,
    )
    assert [r.job_name for r in results] == [
        "UC-Gov-Migration - Airgap Inventory+Export (source)",
        "UC-Gov-Migration - End-to-end Live",
    ]
    assert all(r.created for r in results)
    assert len(ws.jobs.created) == 2
    # payload carried nested tasks straight through to the Jobs API
    assert ws.jobs.created[0]["tasks"][0]["task_key"] == "inventory"
