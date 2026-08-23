# UC Governance Migration Utility

Migrates **Unity Catalog structure + governance** (metadata + ACLs) from a source
metastore (region 1) to a target metastore (region 2). It **never moves table
data** — it creates empty, fully-governed table shells; a separate data-migration
utility clones data via Delta Share + Deep Clone.

It is one of three utilities in a region move:

| Utility | Owns |
|---|---|
| Workspace-migration | identities (users/groups/SPs) + **account-level governed-tag definitions** |
| **This utility** | UC structure + full table definitions + governance (tags, ABAC, classic masks, grants) |
| Data-migration | table data via Delta Share + Deep Clone (run as an ABAC-exempt principal) |

## What it reproduces (under the same names as source)
Storage credentials, external locations, catalogs, schemas, volumes, functions,
views; **full table definitions** (columns, comments, `TBLPROPERTIES`,
partitioning, clustering, constraints, generated & identity columns — everything
except data); **governed-tag assignments**; **ABAC policies** (verbatim, incl.
each policy's `EXCEPT`); **classic column masks / row filters**; **grants**.

## Quick start
1. Prerequisites (once): target metastore + storage, and account-level governed-tag
   definitions — see [`docs/manual-actions.md`](docs/manual-actions.md).
2. Run **`notebooks/01_Inventory`** on the source (scope with `catalogs`/`schemas`).
3. Run **`notebooks/02_Export`** (same `run_id`, provide `mapping_file_path`).
4. (Airgap) move the `run_<id>/` folder to the target.
5. Run **`notebooks/03_Import`** on the target (`create_*`/`apply_*` toggles).

Follow [`docs/runbook.md`](docs/runbook.md) — a scenario-by-scenario guide with exact
widget values. Reference: [`docs/configuration.md`](docs/configuration.md),
[`docs/object-support-matrix.md`](docs/object-support-matrix.md),
[`docs/architecture.md`](docs/architecture.md),
[`docs/troubleshooting.md`](docs/troubleshooting.md).

## Model
- **Names are never mapped** — same catalog/schema/table names on target (a region
  move uses two metastores, so no collision). Only **storage paths** are rewritten
  (via the mapping file).
- **Additive & idempotent** — re-runs pick up new grants/tags/policies and skip
  unchanged objects; removals are reported, never applied.
- **Compute:** Standard (USER_ISOLATION) or serverless (masks/row filters).
