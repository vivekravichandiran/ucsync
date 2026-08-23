"""Install the UC Governance Migration Databricks Jobs from JSON resource specs.

The four job definitions live as declarative specs under ``jobs/`` at the repo
root. This module fills their ``${...}`` placeholders with the widget values
entered in ``notebooks/00_Install_Jobs`` and creates (or updates) the selected
jobs via the Jobs API. Databricks dynamic references (``{{job.run_id}}``,
``{{job.parameters.run_id}}``) are left untouched — only ``${name}`` tokens are
substituted.

Job keys
--------
- ``airgap_source``        — 01 Inventory -> 02 Export on the SOURCE workspace.
- ``airgap_import_target`` — 03 Import on the TARGET workspace; ``run_id`` is a
  job parameter the operator sets per run to match the source bundle folder.
- ``e2e_dry_run``          — 01 -> 02 -> 03 in one run, import ``dry_run=true``.
- ``e2e_live``             — 01 -> 02 -> 03 in one run, import ``dry_run=false``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from uc_sync.job_wrapper import (
    JobCreateResult,
    _find_job_id_by_name,
    _jobs_create,
    _jobs_reset,
    _jobs_run_now,
    _sdk_client,
)

# Ordered so the installer creates jobs in a predictable, readable sequence and
# the notebook can offer them as a stable multiselect.
JOB_SPECS: dict[str, str] = {
    "airgap_source": "airgap_source.json",
    "airgap_import_target": "airgap_import_target.json",
    "e2e_dry_run": "e2e_dry_run.json",
    "e2e_live": "e2e_live.json",
}

# Friendly labels shown in the notebook multiselect <-> internal job keys.
JOB_LABELS: dict[str, str] = {
    "Airgap Inventory+Export (source)": "airgap_source",
    "Airgap Import (target)": "airgap_import_target",
    "End-to-end Dry Run": "e2e_dry_run",
    "End-to-end Live": "e2e_live",
}

_PLACEHOLDER = re.compile(r"\$\{([a-zA-Z0-9_]+)\}")


def _default_specs_dir() -> Path:
    """``jobs/`` at the repo root (two levels up from ``src/uc_sync``)."""

    return Path(__file__).resolve().parents[2] / "jobs"


def _substitute(node: Any, values: Mapping[str, Any]) -> Any:
    """Replace every ``${key}`` in string leaves with ``values[key]`` (blank if absent)."""

    if isinstance(node, str):
        return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), node)
    if isinstance(node, dict):
        return {k: _substitute(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, values) for v in node]
    return node


def _apply_cluster_override(spec: dict[str, Any], existing_cluster_id: str) -> dict[str, Any]:
    """Point every task at an existing cluster instead of the shared job cluster.

    When the operator supplies ``existing_cluster_id`` the spec's ``job_clusters``
    block is dropped and each task is rewired to that cluster; otherwise the spec
    keeps its own USER_ISOLATION job cluster (required so masks/row filters apply).
    """

    existing = str(existing_cluster_id or "").strip()
    if not existing:
        return spec
    spec.pop("job_clusters", None)
    for task in spec.get("tasks", []):
        task.pop("job_cluster_key", None)
        task["existing_cluster_id"] = existing
    return spec


def load_job_spec(
    job_key: str,
    values: Mapping[str, Any],
    specs_dir: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Load one job spec, substitute placeholders, and apply the cluster override."""

    if job_key not in JOB_SPECS:
        raise ValueError(f"Unknown job key '{job_key}'. Known: {sorted(JOB_SPECS)}")
    base = Path(specs_dir) if specs_dir else _default_specs_dir()
    raw = json.loads((base / JOB_SPECS[job_key]).read_text(encoding="utf-8"))
    spec = _substitute(raw, values)
    return _apply_cluster_override(spec, str(values.get("existing_cluster_id", "")))


def resolve_job_keys(selection: Iterable[str] | str) -> list[str]:
    """Map notebook multiselect labels (or raw keys) to canonical job keys.

    Accepts a comma-joined string (as ``dbutils.widgets.get`` returns for a
    multiselect) or an iterable of labels/keys.
    """

    if isinstance(selection, str):
        items = [p.strip() for p in selection.split(",") if p.strip()]
    else:
        items = [str(p).strip() for p in selection if str(p).strip()]
    keys: list[str] = []
    for item in items:
        key = JOB_LABELS.get(item, item)
        if key not in JOB_SPECS:
            raise ValueError(f"Unknown job selection '{item}'.")
        if key not in keys:
            keys.append(key)
    return keys


def install_jobs(
    *,
    job_keys: Iterable[str],
    values: Mapping[str, Any],
    specs_dir: Optional[str | Path] = None,
    run_now: bool = False,
    update_if_exists: bool = True,
    client: Any = None,
    profile: Optional[str] = None,
    host: Optional[str] = None,
    token: Optional[str] = None,
) -> list[JobCreateResult]:
    """Create (or update) each selected job from its filled-in spec."""

    ws = _sdk_client(client=client, profile=profile, host=host, token=token)
    results: list[JobCreateResult] = []
    for key in job_keys:
        spec = load_job_spec(key, values, specs_dir)
        name = spec["name"]
        first_task = spec.get("tasks", [{}])[0]
        notebook_path = first_task.get("notebook_task", {}).get("notebook_path", "")
        base_parameters = first_task.get("notebook_task", {}).get("base_parameters", {})

        existing_id = _find_job_id_by_name(ws, name)
        created = updated = False
        if existing_id is not None and update_if_exists:
            _jobs_reset(ws, existing_id, spec)
            job_id, updated = existing_id, True
        elif existing_id is not None:
            job_id = existing_id
        else:
            job_id = _jobs_create(ws, spec)
            created = True

        run_id: Optional[int] = None
        run_page_url: Optional[str] = None
        if run_now:
            run_id = _jobs_run_now(ws, job_id)
            host_url = getattr(getattr(ws, "config", None), "host", None) or host or ""
            if host_url and run_id is not None:
                run_page_url = f"{host_url.rstrip('/')}/#job/{job_id}/run/{run_id}"

        results.append(
            JobCreateResult(
                job_id=job_id,
                job_name=name,
                notebook_path=notebook_path,
                parameters=dict(base_parameters),
                run_id=run_id,
                run_page_url=run_page_url,
                created=created,
                updated=updated,
            )
        )
    return results
