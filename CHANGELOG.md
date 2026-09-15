# Changelog

All notable changes to the UC Governance Migration Utility are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) (`MAJOR.MINOR.PATCH`). The
version is the single source of truth in [`VERSION`](VERSION); it is read by
`src/uc_sync/__init__.py` and stamped into every bundle's `manifest.json`, `uc_sync_audit`, and
`uc_sync_state`, so each artifact records exactly which build produced it.

Bump rules:

- **PATCH** (`1.0.0 → 1.0.1`) — backward-compatible fixes (a corrected capture, a fixed report label).
- **MINOR** (`1.0.0 → 1.1.0`) — backward-compatible additions (a new object type, a new widget/option).
- **MAJOR** (`1.0.0 → 2.0.0`) — breaking changes (bundle-format or state-schema change, removed/renamed
  widgets, a changed default that needs operator action).

---

## [Unreleased]

_Nothing yet._

## [1.0.0] - 2026-09-15

First production release. Migrates **Unity Catalog structure + governance** (no table data) from a
source metastore to a target metastore, across Azure regions.

### Added
- **Notebook-based, config-driven migration** of UC **structure** (storage credentials, external
  locations, catalogs, schemas, volumes, functions, views) + **full table definitions** (columns,
  comments, `TBLPROPERTIES`, partitioning, clustering, constraints, generated & identity columns —
  everything except data) + **governance** (governed-tag **definitions** recreated via `CREATE GOVERNED
  TAG` (Phase 0, idempotent) + **assignments**, ABAC policies verbatim incl. each `EXCEPT`,
  classic column masks / row filters, grants & ownership).
- **Four notebooks** — `00_Install_Jobs`, `01_Inventory`, `02_Export`, `03_Import` — plus
  declarative Jobs specs in `jobs/` (Airgap source, Airgap import, End-to-end Dry Run, End-to-end Live).
- **Two connectivity modes:** `direct` (default; `03` runs in the target, source read over REST +
  the source SQL warehouse) and `airgap` (two-sided, manual `run_<id>/` bundle handoff).
- **Warehouse-only DDL capture, no silent downgrade:** `SHOW CREATE` for the table/view family runs
  on `source_warehouse_id` in **both** modes; a failed capture is a **hard `DDL_CAPTURE_FAILED`**
  (no synthesized fallback that would drop masks/constraints). Functions are reassembled losslessly
  from `information_schema`.
- **Fail-closed governance (always on):** a governed table that cannot be fully protected never
  survives — inline masks/filters fail `CREATE TABLE` atomically; a governed-tag or ABAC failure
  drops the freshly-created table (`PROTECTION_FAILED`) via the drop-sweep phase. Pre-existing tables
  are `SKIP_EXISTING` and never dropped.
- **Governance-aware dependency order:** SC → EL → catalogs → schemas → volumes → **functions →
  tables (inline masks) → governed tags → ABAC → drop-sweep → views** → grants → ownership (last).
- **Single-warehouse import (FEAT-1):** the **entire import replay** (tables, functions, masks / row
  filters, views, materialized views, ABAC, tags, grants) runs on one serverless SQL warehouse —
  there is no classic-Spark DDL path (so `CREATE POLICY` and masked-table `CREATE VIEW`, both rejected
  on classic Spark, succeed). `import_warehouse_id` is **required**; the import fails fast without it.
- **Additive & idempotent, with auto-detected incremental (delta) sync** via the `uc_sync_state`
  Delta table: first run seeds the baseline; later runs diff each object's DDL / governance / grant
  fingerprints and apply **only deltas** — removals are **report-only**, never applied.
- **BYO-by-default posture:** catalog / schema / storage-credential / external-location creation is
  **off by default** (customer prerequisites); a 3-column `external_locations.csv` turns SC/EL
  creation back on. Existing-catalog mode is auto-detected; `catalog_mapping_json` renames a catalog.
- **Two location/mapping inputs**, each distinct and none dead: `external_locations_path` (the single
  storage-mapping CSV) and `object_locations_path` (exact per-object override). *(The legacy
  `mapping_file_path` widget was retired; a legacy CSV can still be supplied via the YAML
  `location_mapping_csv_path`.)*
- **Catalog-scoped permissions — no metastore/account admin ever.** Two SPs (source read, target
  run); `PERMISSIONS_GUIDE` + `testing/permission_probe.py` state and verify the minimum live.
- **Reporting model with wsmig functional parity:** one `last_action` status vocabulary shared by
  the report and `uc_sync_state`; per-object-type sheets; a **Summary** with current-run failures /
  manual steps / deleted-in-source; an **Outstanding** sheet listing cumulative failures from state;
  **Governed Tags Applied** naming. (Legacy Issues/Delta sheets removed.)
- **Metadata tables:** `uc_sync_audit`, `uc_sync_state` (three documented fingerprints —
  `source_definition_hash` / `ddl_hash` / `governance_hash` — plus `first_seen`, `connectivity_mode`,
  `failure_category`, `last_error_raw`), and `uc_sync_volume_files` (with `status` / `created_at` /
  `updated_at`) for optional volume-file copy.
- **Graded environment preflight** (`preflight_enforce`) — a missing report library or unreachable
  warehouse is a loud NO-GO, never a silent degrade.
- **Full documentation set** under `docs/` (architecture, configuration, runbook, permissions,
  object-support matrix) with Mermaid-sourced diagrams.

### Out of scope (stated deliberately)
- Table **data** (owned by the data-migration utility: Delta Share + Deep Clone).
- **Identities** (owned by the workspace-migration utility; must pre-exist on target). *(Governed-tag
  definitions are recreated by this tool in Phase 0, best-effort — not out of scope.)*
- **Volume file contents** (definitions migrate; bytes only when `copy_volume_data=true`).
- **Tier-A AI assets** (registered models, vector-search indexes, online tables, monitors, UC
  secrets) — inventoried & reported, not migrated. Streaming tables / materialized views are
  report-only (MV only behind `migrate_materialized_views=true`).
- **Tier-B/C metastore-scoped objects** (Lakehouse Federation connections & foreign catalogs, service
  credentials, workspace bindings, unreferenced storage credentials, Delta Sharing shares/recipients/
  providers, clean rooms) — neither inventoried nor migrated (out of a catalog-scoped principal's reach).

[Unreleased]: https://example.com/compare/v1.0.0...HEAD
[1.0.0]: https://example.com/releases/tag/v1.0.0
