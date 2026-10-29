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

## [1.2.1] - 2026-10-29

Patch release: two import fixes found via a production migration in which a
failed catalog-level ABAC policy dropped every new table of the run fail-closed.
Backward compatible — no bundle or state-schema change. Both fixes change what
02_Export writes, so an affected run must **re-export, then re-import**.

### Fixed
- **An ABAC policy whose `MATCH COLUMNS` alias contains a non-identifier
  character failed with `INVALID_IDENTIFIER`, and a catalog- or schema-level
  policy then dropped every fresh table in its scope fail-closed.**
  `information_schema.abac_policy_definitions` returns the alias unquoted even
  when the source policy was created with back-quotes (`… AS
  mc_sales-fleet-vol-mtd` for a column `sales-fleet-vol-mtd`), and the export
  replayed it verbatim. `abac_policy_create_statement` now back-quotes the alias
  after the last top-level `AS` when it is not a plain identifier (an `AS` inside
  a tag literal is ignored, an already-quoted alias is kept). Row-filter `USING
  COLUMNS` aliases get the same quoting. The fail-closed drop scope itself is
  unchanged (by design).
- **A SQL stored procedure failed with `PARSE_SYNTAX_ERROR … near 'WITH'` /
  `'DECLARE'`.** Procedures are listed in `information_schema.routines` with
  `routine_type = 'PROCEDURE'` and `data_type = 'NULL'`, and were rebuilt as
  `CREATE FUNCTION … RETURNS NULL RETURN BEGIN …`. They are now rebuilt as
  `CREATE OR REPLACE PROCEDURE … LANGUAGE SQL SQL SECURITY <type> AS BEGIN … END`,
  keeping every parameter's `IN` / `OUT` / `INOUT` mode and IN-parameter
  `DEFAULT`s. The import's statement splitter also no longer treats a `;` inside
  the `BEGIN … END` body of a `CREATE PROCEDURE` / `FUNCTION` as a statement
  boundary (nested blocks, labels, `END IF` / `END LOOP` / `END CASE` and
  `CASE … END` expressions handled). Grants and ownership use the `FUNCTION`
  keyword, which UC accepts for procedures, so they are unchanged.

## [1.2.0] - 2026-10-16

Minor release: two DDL-replay fixes found via real production migration
failures (the statement splitter and view TBLPROPERTIES), plus source-side
table filtering. Backward compatible — no bundle or
state-schema change.

### Fixed
- **A table whose column or table COMMENT contains an escaped apostrophe *and* a
  `;` failed on import with `PARSE_SYNTAX_ERROR … at or near '''`.** `SHOW CREATE
  TABLE` serializes an embedded quote with a backslash (`'Today\'s flag … -> Red;
  NK: …'`). The `[1.1.2]` fix taught `rewrite._tokenize_sql` this escape, but the
  import's statement splitter (`package_import._split_statements`) still only knew
  the doubled-quote form: the `\'` closed the literal early, the comment's own `;`
  became a statement boundary, and the CREATE TABLE was sent to the warehouse cut
  in half. The splitter now consumes a backslash plus its next character as a unit
  inside single- and double-quoted strings. Tables with only an apostrophe or only
  a `;` were never affected. The splitter also reads the grants/tags/policies/ABAC
  files, so those are covered too. Verified against the real failing DDL.
- **A commented VIEW whose query contains a `)` failed on import with
  `PARSE_SYNTAX_ERROR` (e.g. `… at or near 'THEN'`).** For a view, `SHOW CREATE
  TABLE` emits `TBLPROPERTIES ('comment' = …)` *before* `WITH SCHEMA COMPENSATION
  AS <query>`. `rewrite.strip_reserved_table_properties` assumed TBLPROPERTIES was
  the last clause and matched greedily to the **last** `)` in the text, so it
  replaced the `AS <query>` up to its final `)` with the rebuilt property list.
  The view was then replayed without its query head. It now finds the block with
  the quote-aware tokenizer and stops at the `)` that balances the opening one,
  and leaves a block with nothing to drop untouched. Two related problems are
  fixed by the same change: a property value with an escaped quote (`'it\'s'`)
  was truncated, and `TBLPROPERTIES (` text inside a comment was treated as the
  clause. Tables were not affected in practice (TBLPROPERTIES is their last
  clause).
- **An object whose name contains `__` failed on import with
  `REQUIRES_SINGLE_PART_NAMESPACE`.** Bundle file names encode `.` as `__`, and the
  import decoded the file name back into the object name, so `c.s.orders__daily`
  became `c.s.orders.daily` (a 4-part name). The same happened to a name part
  ending in `_` (`c.stage_.t`). The wrong name was used for the CREATE, grants,
  tags, policies, the report row and `uc_sync_state`. The import now takes each
  object's real name from the bundle inventory (re-encoding inventory names, as
  ABAC policies already did). It falls back to decoding the file name only when
  the inventory has no row for it. The bundle file format is unchanged, so no
  re-export is needed: re-run the import (e.g. with `filter_tables` set to the
  affected tables).
- **A catalog with materialized views or streaming tables that carry tags, masks
  or ABAC failed the import as a governance failure.** These objects are
  report-only (never created on the target; MVs unless
  `migrate_materialized_views=true`), but their column tags, classic masks and a
  table-level ABAC policy on them were still applied. Each failed against the
  missing object, counted as `GOVERNANCE_FAILED`, and hard-failed the run. They
  are now skipped, and the object's report row notes that its tags, masks and
  ABAC policies must be re-applied once its pipeline recreates it on the target.
  Catalog/schema-level ABAC policies and the governance of migrated tables are
  unchanged. In the report, the object's governance rows read **Skipped (object
  not migrated)**: its rows on *Tags Applied* (was `— (no tag op this run)`, which
  read as unchanged), a policy attached to it on *ABAC Policies* (was blank), and
  its rows on *Policy Matched Columns* (was the catalog policy's `Created`, which
  read as if the object were masked on the target).

### Added
- **`filter_tables` now applies at inventory (source side).** The table allowlist
  that previously only narrowed the import is now also a `01_Inventory` widget and
  is passed to the inventory task of the e2e and airgap-source jobs, so unselected
  tables are never read or exported (no DDL / grants / tags / mask capture). Same
  semantics as before (`catalog.schema.table` or bare name; table-like securables
  only — catalogs, schemas, functions, volumes always come along), shared through
  one matcher (`filters.table_selected`). The report-only table kinds (Lakebase
  tables, vector-search indexes, pipeline event-log tables, online tables) are
  narrowed too, so an unselected one is not still listed as a manual step.

### Changed
- **Import `filter_tables` matches the source *or* the target name.** Previously a
  fully-qualified entry was compared only against the catalog-mapped target name,
  so under `catalog_mapping_json` a source-named entry silently skipped the table.
  Both now select it (bare names are unchanged).
- **An incremental run with `filter_tables` set reports "Deleted in source" only for
  listed objects.** The delta planner compares this run's inventory with the full
  `uc_sync_state` baseline. With the inventory now filtered, every unlisted
  table/view was missing from the run and read as deleted in source. A
  table-like object the filter leaves out is now skipped by that check. A listed
  object that really is gone, and any missing function/volume/policy, are still
  reported. With the filter blank, the check is unchanged.
- Operators who already set `filter_tables` in `00_Install_Jobs` now also get a
  narrower inventory/export. What lands on the target is unchanged; the report just
  no longer lists the unselected tables as `Skipped (filtered)`.
- **A materialized view created this run is dropped fail-closed when its governance
  fails** (with `migrate_materialized_views=true`), like a fresh table. Before, it
  was only marked `PROTECTION_FAILED` and kept, and an MV holds data. A failed
  MV tag, or a failed ABAC policy whose scope covers the MV, now drops it with
  `DROP MATERIALIZED VIEW`. MVs are created after the first drop sweep, so a
  second sweep runs after the view phase. An MV that already existed on the
  target (now probed before create, like tables) is never dropped, only marked.
  The job still fails on any governance failure.

## [1.1.3] - 2026-10-06

Patch release: a function-DDL reconstruction bug fix, found via a real production
migration failure. Backward compatible — no widget, schema, or behavior change
outside the fix itself.

### Fixed
- **A table-valued function (TVF) whose `RETURNS TABLE(...)` column list contains a
  digit-leading column name failed with `PARSE_SYNTAX_ERROR` on import.** Databricks
  SQL functions carry no `SHOW CREATE FUNCTION` support, so their DDL is reassembled
  from `information_schema.routines`/`parameters` (`function_ddl_from_information_schema`
  / `_function_ddl_from_definition` in `rewrite`'s sibling module `sql_ddl.py`). The
  function's own INPUT parameters were already backtick-quoted when not a plain
  identifier, but the `RETURNS TABLE(...)` column list — read verbatim from
  `full_data_type` — had **no quoting at all**. A column name starting with a digit
  (a real pattern in telecom KPI feeds, e.g. `0025_vvm_total_traffic_mb`,
  `0409_ul_interference_power`) is returned by Databricks completely unquoted —
  confirmed live even when the function was originally *created* with the column
  backtick-quoted — and a bare `0025_...` parses as a number literal followed by an
  identifier, a hard syntax error. Added `_requote_table_return_columns()`, which
  backtick-quotes any non-plain field name in the column list, splitting on commas
  only at bracket depth 0 so a `DECIMAL(38,2)` type's own internal comma is never
  mistaken for a field separator. Applied to both reconstruction call sites.
  Verified live: a real TVF created on one Azure Databricks workspace with a
  digit-leading return column, migrated across metastores, and successfully invoked
  on the target with correct results. Audited every other DDL-construction path
  (table synthesis, schema-evolution `ADD COLUMN`, masks, row filters, views) —
  all already quote unconditionally or go through Databricks' own `SHOW CREATE`
  serializer, so this class of bug was unique to the function `RETURNS` path.

## [1.1.2] - 2026-10-05

Patch release: a follow-up fix for the `[1.1.1]` sanitizer tokenizer, caught by live testing
against a real cross-metastore workspace pair. Backward compatible — no widget, schema, or
behavior change outside the fix itself.

### Fixed
- **`strip_managed_storage_clauses` / `strip_inline_collate` / `strip_default_collation` could
  still desynchronize and corrupt captured DDL when a `COMMENT` contained a backslash-escaped
  quote.** The `[1.1.1]` tokenizer only recognized the SQL-standard doubled-quote escape
  (`'it''s'`) inside string literals. Live testing against real `SHOW CREATE TABLE` output showed
  Databricks actually serializes an embedded quote in a comment using a **backslash** escape
  (`'it\'s a test'`), not doubling. The unescaped backslash form let the tokenizer treat the
  escaped quote as the literal's real closing quote one character early, desynchronizing every
  token after it — observed corrupting two unrelated columns' comments and dropping a whole column
  definition. `_tokenize_sql` now also consumes a backslash plus its following character as a unit
  inside single- and double-quoted strings (never treating it as a closing quote), fixing both
  escape styles. Verified live end-to-end: a table with single-quote, double-quote, and
  trailing-`location`-text comments, migrated across two independent Azure Databricks metastores,
  now reproduces every comment byte-for-byte on the target with no `PARSE_SYNTAX_ERROR`.

## [1.1.1] - 2026-10-05

Patch release: a captured-DDL sanitizer bug fix. Backward compatible — no widget, schema, or
behavior change outside the fix itself.

### Fixed
- **A column/table `COMMENT` containing the word "location" (or "collate"/"collation") could
  corrupt captured table DDL, causing `PARSE_SYNTAX_ERROR` on import.** `strip_managed_storage_clauses`
  / `strip_inline_collate` / `strip_default_collation` previously scanned the *entire* captured DDL
  text with plain regexes (e.g. `\s+LOCATION\s+'[^']*'`) to strip the managed-storage/collation
  clauses a target metastore rejects. Those regexes had no notion of "this quote belongs to an
  unrelated string" — a `COMMENT` whose text happened to contain one of the keyword words, followed
  eventually by any quote, could make the regex consume real DDL content between the comment's own
  closing quote and some unrelated later string, corrupting the statement. The three sanitizers now
  tokenize the DDL into code vs. string/comment/`` ` ``-identifier segments first (mirroring the
  quote-aware splitter already used on import, `package_import._split_statements`) and only strip a
  clause when its keyword is found in a code segment and immediately followed by its own value token
  — so a comment's content, whatever it says, is never a candidate for removal.

## [1.1.0] - 2026-09-25

Optimization + observability release: within-stage parallelism, structured logging, per-facet
state, a retry-failed-only import mode, and a table-exclusion filter — plus bug fixes. Backward
compatible: every addition is opt-in or defaults to the prior behavior, and `uc_sync_state` is
upgraded in place (new columns added + backfilled), so existing baselines keep working.

### Added
- **Structured logging across all three stages** — one `uc_sync` app logger with per-module child
  loggers; every line carries `run_id` + stage (INVENTORY/EXPORT/IMPORT), levelled INFO/WARNING/ERROR.
  New **`log_level`** widget (**default `DEBUG`**). Logs stream **live to each stage's job-run cell
  output** (downloadable from the run page — no separate `.log` file) and render on **success and
  failure alike**. Coverage is **per phase** (header + completion tally) and **per object**
  (`TABLE cat.sch.t -> created`; failures at ERROR with the reason), and the **exact SQL each step
  runs is logged at `DEBUG`** — so a run, including one that hangs or fails mid-stage, is diagnosable
  from the cell alone. Set `log_level=INFO` for the per-object summary without the SQL. Registered
  secrets are redacted from every line.
- **Within-stage parallelism** — new single **`parallel_threads`** widget (default `4`; `1` =
  today's exact sequential behavior / kill-switch). Export parallelizes the `SHOW CREATE`
  pre-capture; Inventory parallelizes the per-object grant fan-out; Import parallelizes object
  creation **within each `_type_rank` level** with a barrier between levels (functions before
  tables), deterministic `import_order`, and a single retry pass per object type for transient
  same-rank dependency ordering (e.g. a FK to a sibling table). Keep `parallel_threads` ≤ the
  warehouse's max concurrent queries.
- **`exclude_regex`** table-exclusion widget (Inventory) — comma-separated Python regexes matched
  with `.search()` on `catalog.schema.table`; excluded tables (and their DDL/grants/masks) are never
  captured. Parent catalog/schema are never excluded.
- **Retry-failed-only import mode** — new **`retry_failed_only`** widget (import). Replays only the
  prior run's failed objects (read from `uc_sync_state`'s per-facet status) plus their required
  parents, skipping everything else regardless of deltas. Same `run_id`/bundle — no re-export. Writes
  a timestamped **`reports/import_retry_<UTC>.xlsx`** (preserving the original `import.xlsx`), and marks
  every object the retry didn't touch as **`— (not in retry scope)`** in the report (not a blank cell).
- **Per-facet state tracking** — `uc_sync_state` gains **`ddl_status`**, **`governance_status`**,
  **`grants_status`** (added in place + backfilled from `last_action`/hashes on existing tables). A
  facet's fingerprint advances only when that facet actually applied; the delta planner re-applies a
  facet whose prior status is `failed`. This closes two gaps: a green incremental run no longer masks
  a still-unapplied governance step, and a grant that raised is replayed on the next run (e.g. an
  external object created out-of-band later).
- **Grouped, ordered widgets** — every widget now carries a numbered `label` (Databricks sorts by
  label), clustered by group (Source / Scope / Create / Apply / Warehouse / Cluster / Proxy / Run).
  Widget **names/keys and job params are unchanged**.

### Changed
- **Removed the inert `mapping_file_path` input.** External-storage mapping is `external_locations_path`;
  a legacy location CSV is `location_mapping_csv_path`. No behavior change (the field was already a no-op).

### Fixed
- **"dropped fail-closed" messaging** — a governance failure now reports "dropped" only when a fresh
  shell created this run was actually dropped; a pre-existing / create-disabled table (never dropped)
  reports "marked FAILURE (not dropped — pre-existing)".
- **Parallel same-rank dependency ordering (BUG-QA1)** — under parallel import, a table with a
  FOREIGN KEY to a sibling in the same level could be created before the sibling committed. A single
  sequential retry pass per object type resolves it; whatever still fails is reported honestly.
- **Retry-failed-only replays failed ABAC policies (BUG-QA2)** — the failed-set membership check now
  normalizes the policy name (`…#policy:name`) so a failed ABAC policy is matched to its bundle file
  and re-applied, instead of being silently skipped while the run falsely reported green.

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

[Unreleased]: https://example.com/compare/v1.1.3...HEAD
[1.1.3]: https://example.com/compare/v1.1.2...v1.1.3
[1.1.2]: https://example.com/compare/v1.1.1...v1.1.2
[1.1.1]: https://example.com/compare/v1.1.0...v1.1.1
[1.1.0]: https://example.com/compare/v1.0.0...v1.1.0
[1.0.0]: https://example.com/releases/tag/v1.0.0
