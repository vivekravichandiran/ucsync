# UC Governance Migration — backlog (improvements & bugfixes)

Running backlog for the UC governance migration utility: features, cleanups, and bugs. One numbered
item per section. (`src/uc_sync/config.py:384` links here for item 1.)

---

## 1. Fully remove `mapping_file_path`

**Status:** widget + wiring removed (2026-09-15); config-level field/resolution left as **inert dead
code**. This item is the remaining full excision.

### What was already removed (done, tests green — 316 passed)
- The `mapping_file_path` **widget** in `notebooks/00_Install_Jobs.py` and `notebooks/02_Export.py`
  (definition + the `values` dict entry).
- The `mapping_file_path` entry from the `_simple` passthrough list in `00_Install_Jobs.py`.
- The `"mapping_file_path": "${mapping_file_path}"` placeholder from `jobs/e2e_live.json`,
  `jobs/e2e_dry_run.json`, `jobs/airgap_source.json`.
- The (unused) input key in `tests/test_install_jobs.py`.

Net effect: there is **no operator surface** for `mapping_file_path` anymore. For any widget/job-param
run it resolves to `""` and is a no-op (unmatched job placeholders substitute to blank via
`install_jobs._substitute`; nothing downstream reads `cfg.mapping_file_path`).

### Why the config field was NOT removed yet (the 1% caution)
The resolution still sits in the **storage-path precedence** logic in `src/uc_sync/config.py`:

- field `mapping_file_path: str = ""` (line ~62)
- resolution block (lines ~383-393): if set and `location_mapping_csv_path` unset, it becomes the
  location CSV and feeds `location_mappings`
- **line ~412**: `if not location_mapping_csv_path and not mapping_file_path:` gates whether
  `external_locations_path` feeds `location_mappings`
- constructor arg (line ~440): `mapping_file_path=mapping_file_path`

A regression here **misplaces storage paths**, so removal needs a dedicated config-resolution +
live test pass, not just unit tests.

### The full-removal change
1. Delete the field, the resolution block, and the constructor arg.
2. Simplify line ~412 to `if not location_mapping_csv_path:` (behavior-identical once the variable is
   always `""`, but confirm with a config-resolution test matrix).
3. Confirm the independent YAML input `location_mapping_csv_path` still covers the legacy-CSV case
   (it does today — separate code path at lines ~339-350), and decide whether to consolidate the two.
4. Grep for any remaining references (`grep -rn mapping_file_path src/ tests/`).

### Acceptance
- Config-resolution unit matrix: (a) external_locations only, (b) location_mapping_csv_path only,
  (c) both — assert `location_mappings` + SC/EL toggles unchanged vs. today.
- One live BYO run + one live 3-column (create SC/EL) run — external paths land identically.
- Full suite green.

---

## 2. Expose `exclude_regex` table-exclusion widget

**Status:** NOT STARTED (filed 2026-09-21). The filtering **engine** already exists and is
honored at inventory; it is simply **not surfaced** as an operator widget in the notebook flow.

### Motivation
Operators want to "replicate the complete catalog but exclude a few tables" (a comma-separated
list, or a wildcard/pattern). Today there is no operator control for this:

- The only table-level widget, `filter_tables` (`00_Install_Jobs.py`, `03_Import.py`), is an
  **allowlist / include-only** (`package_import.py:803` — blank = import everything; when set,
  keep ONLY the listed tables). It is the *opposite* of an exclude, and to express "all but a few"
  you would have to enumerate every table you want.
- `exclude_regex` / `include_regex` exist in `SyncConfig` (`config.py:126-127`) and are applied by
  `filters.allowed()` (`filters.py:68-73`, `.search()` on `catalog.schema.table`), but in the
  notebook flow `from_sources(widgets)` is fed a **fixed key list** (`01_Inventory.py:64-70`) that
  omits `exclude_regex`, so it always defaults to empty and is unreachable.
- The `exclude_regex: ".*_TEMP$"` in `resources/jobs/uc_sync_job.yml` targets a `UC_Sync_Main`
  notebook that does not exist in this repo — a stale/legacy DAB spec, not the operative path.

Apply the filter at **inventory** (`01`): that is the only stage where `filters.allowed()` runs, so
excluded tables (and their DDL/grants/masks) are never captured or exported.

### The change (5 files)
1. **`notebooks/01_Inventory.py`**
   - Add widget: `dbutils.widgets.text("exclude_regex", "")` (next to `catalogs`/`schemas`).
   - Add `"exclude_regex"` to the tuple feeding the `widgets` dict (~line 64) so `from_sources`
     picks it up. No further wiring — `config.from_sources` already CSV-splits it
     (`config.py:331-333`) into `SyncConfig.exclude_regex`, which `filters.allowed()` already applies.
2. **`notebooks/00_Install_Jobs.py`**
   - Add widget: `dbutils.widgets.text("exclude_regex", "")` (near the other scoping widgets).
   - Add `"exclude_regex"` to the `_simple` passthrough tuple (~line 157) so it lands in `values`
     and `${exclude_regex}` is substituted into the installed job specs.
3. **Job specs** — add `"exclude_regex": "${exclude_regex}"` to the `01_Inventory` task's
   `base_parameters` in the three specs that run inventory: `jobs/airgap_source.json`,
   `jobs/e2e_dry_run.json`, `jobs/e2e_live.json`. (Not `jobs/airgap_import_target.json` — it only
   runs `03`; the exclude happens upstream at inventory.)

### Caveats to document for operators
- Values are **Python regexes** matched with `.search()` on `catalog.schema.table` — escape dots
  (`\.`), anchor with `$`, and note that a bare `orders` substring-matches `orders_archive`.
- Comma-separated list is supported (`_split_csv`); each entry is an independent regex.
- Example: `exclude_regex: .*_TEMP$, sales\.public\.orders_raw$`.

### Acceptance
- Unit: `from_sources` with `exclude_regex` set (CSV + single) produces the expected
  `SyncConfig.exclude_regex`; `filters.allowed()` drops matching tables and keeps the rest, incl.
  the parent catalog/schema (`include_parents` unaffected).
- `tests/test_install_jobs.py`: the three inventory-bearing specs carry the substituted
  `exclude_regex` param; `airgap_import_target` does not.
- One live run replicating a whole catalog with 1-2 tables excluded — excluded tables absent from
  the bundle and the target; everything else replicated identically.
- Full suite green.

---

## 3. Thread-pool parallelism *within* each stage (Inventory, Export, Import)

**Status:** NOT STARTED (filed 2026-09-21; scope extended to all three stages 2026-09-24). All three
stages are **sequential** today and all three benefit. Goal: parallelize the per-object work **inside**
a stage/phase with a bounded `ThreadPoolExecutor`, keeping dependency **hierarchy** intact. The single
`parallel_threads` knob (below) applies to all three stages.

### Scope: all three stages (not just import)
- **02 Export (biggest, easiest win):** `SHOW CREATE` per object (e.g. ~1,667 statements) is
  **embarrassingly parallel** — each is an independent, idempotent read with no ordering. Just a
  bounded pool over the object list + a thread-safe result collection. **LOW effort, highest payoff**
  (this is the slow stage today, and the re-export cost the governance-only flows keep hitting).
- **01 Inventory (low effort):** per-object **grant reads (REST)** and **tag/ABAC reads (SQL)** are
  independent per object — parallelize the `_attach_grants` / `_attach_governance` fan-out over a
  bounded pool with thread-safe accumulation. No ordering constraints. **LOW effort.**
- **03 Import (hardest — the detail below):** phased, dependency-ordered creation with the drop-sweep
  + per-facet state coupling. Parallelize *within* a `_type_rank` level, barrier across levels.
  **HIGH effort** (the shared-state work below). Do this one **after Item 8** so the per-object
  result/state aggregation is settled.

For Inventory/Export the "thread-safety work" reduces to: a thread-safe result list (or per-worker
lists merged deterministically) and confirming the shared `WorkspaceClient`/executor is safe to share
(or one per worker). No drop-sweep / ordering hazards. The import detail below is the complex case.

The import (`03` / `package_import.PackageImporter.run`) is a phase-ordered set of plain `for` loops
(`package_import.py:895-963`).

### Requirement (as specified)
- Parallelize *within* a level, sequential *across* levels. E.g. in the FUNCTIONS level, many
  functions run across threads; in the TABLES level, many tables run across threads — but all
  functions must complete before tables start (a table's inline mask/row-filter references a
  function).
- A bounded worker pool (configurable size, sensible default) — never unbounded.
- Each worker handles its own API resilience: retries + exponential backoff. **See "Retry/backoff
  already exists" below — this requirement is essentially already met; no new retry code needed.**

### How the current ordering maps to "levels"
The structural phase is a single list sorted by `_type_rank` then filename
(`package_import.py:863-873, 889`): creds → external locations → catalogs → schemas → volumes →
**functions → tables** (views/matviews/streaming tables are a separate later phase, after the
governance drop sweep). So the natural implementation is:

1. **Group** the structural files by `_type_rank` (the dependency level).
2. Process rank-groups **in order** (a barrier between levels).
3. **Within** a rank-group, submit each file to a `ThreadPoolExecutor` (same-rank objects — e.g.
   two tables, two functions — are assumed mutually independent).
4. Apply the same pattern to the later **views** phase (independent of each other once base tables
   exist) and to the governance phases (`_apply_governance_dir` for tags/ABAC).

### Retry/backoff already exists (checked — the answer is YES, at two layers)
Every worker thread inherits full API resilience for free, because retries live *inside* the calls a
thread makes, and each call is stateless:

- **REST layer — `WorkspaceClient` (`workspace_client.py`):** retries HTTP 429/500/502/503/504 with
  exponential backoff `time.sleep(min(2**attempt, 20))`, `max_retries=5` default. Covers UC REST
  calls and the Files API (volume up/download).
- **SQL layer — `RestSqlExecutor.execute()` (`import_engine.py:209-221`):** loop
  `for attempt in range(max_retries+1)`, `delay = min(retry_base * 2**attempt, 30.0)`,
  `time.sleep(delay + random.uniform(0, delay*0.25))` — exponential backoff **with jitter**.
  Retryable vs fail-fast is classified (`_RETRYABLE_HTTP_STATUSES = {403,408,429,500,502,503,504}`
  + transient statement hints like "warehouse is starting"/"capacity"; deterministic
  syntax/permission/not-found fail fast). Defaults `max_retries=4` (governance) / `6` (DDL capture);
  polling also backs off up to a cap.

Because the retry loop is **per-`execute()` call and stateless**, it is inherently thread-safe: no
per-thread retry code needs to be written. The only related work is ensuring each worker's executor
is safe to use concurrently (share vs. per-worker — see thread-safety below), and possibly a global
concurrency cap so N parallel workers don't inflate 429 pressure faster than backoff absorbs it.

### Thread-safety work this actually requires (the real cost)
The parallelism is the easy part; these shared-state hazards are the work:

1. **Result ordinals** — `results.append(...)` with `len(results)+1` as `import_order`
   (`:909-911`, `:958-963`) races under threads and makes the ordinal nondeterministic. Collect each
   worker's result and **merge in a deterministic order** (assign `import_order` after the group
   completes, by rank+name), so the Tables sheet / `uc_sync_audit` / `uc_sync_state` stay stable.
2. **Shared mutable maps on `self`** — `_created_tables`, `_failed_tables`, `_created_objects`,
   `_failed_objects`, `_absent_objects` (initialized `:875-879`, mutated in `_import_ddl_file`) are
   written during processing. Guard with a lock or accumulate per-worker and merge at the barrier.
3. **Executor / client sharing** — confirm `WorkspaceClient` (and any wrapped `requests.Session` /
   SDK client) is safe to share across threads, or give each worker its own executor/client.
4. **Intra-rank independence assumption** — same-rank objects are assumed independent. If a function
   calls another function (same rank), a one-shot parallel pass could fail; consider a
   failed-set retry pass within the level, or a finer dependency sort.
5. **Fail-closed semantics preserved** — a governance failure must still record its table for the
   Phase 4 drop sweep; ensure the drop-sweep set is populated correctly under concurrency.

### Design knobs
- **One widget: `parallel_threads`** (install-jobs + `03`), default small (e.g. 4). It is the single
  knob — it sets the `ThreadPoolExecutor` worker count for *both* the DDL work and the per-worker REST
  calls; there is no separate "API concurrency" value. "Respect warehouse concurrency" is guidance,
  not a second parameter: choose `parallel_threads ≤ the target warehouse's max concurrent queries`
  (documented), since every worker's DDL/ABAC runs through that warehouse. `parallel_threads=1`
  reproduces today's sequential behavior exactly (safe fallback / kill-switch).
- Barrier between `_type_rank` levels; optional per-phase opt-out.
- No change to retry/backoff: each worker inherits the existing per-call retry+backoff (see above), so
  the pool size only bounds concurrency — it does not need its own rate-limit knob (the per-call 429
  backoff absorbs bursts; keep the pool small enough that it doesn't fight the warehouse).

### Acceptance
- Unit: level-grouping preserves `_type_rank` ordering; a simulated slow object in a level does not
  reorder results; `import_order` is deterministic regardless of completion order.
- Concurrency: shared-state maps and the drop-sweep set are correct under a forced-parallel test
  (e.g. many small tables); no lost/duplicated results.
- Fault injection: a retryable 429/5xx on one worker is ridden out by the existing backoff without
  failing the phase; a deterministic error (syntax/permission) still fails only its own object.
- Live: a large catalog imports faster than sequential with identical final state (same objects,
  grants, masks, ABAC) and identical report/audit/state contents (order-independent).
- `parallel_threads=1` reproduces today's sequential behavior exactly.
- Full suite green.

---

## 4. Explicit "retry-failed-only" import mode

**Status:** NOT STARTED (filed 2026-09-21). Mirrors the workspace-migration utility's dedicated
retry mode (`github.com/abhishekiyer-databricks/wsmig_utility`).

### What exists today (implicit, not a mode)
The incremental engine **already re-attempts prior failures automatically** (bug #18). On a re-run
with a seeded baseline, `DeltaPlan._decide()` (`delta.py:178-190`) re-attempts any object whose prior
`last_action` is **not** in `CLEAN_ACTIONS` (`vocab.py:50` = created / created_with_warning / updated /
adopted / skipped / skipped_create_disabled), even when the source fingerprint is unchanged; clean +
unchanged objects are skipped with zero writes. So a plain import re-run against the same bundle
effectively retries the failures **plus** applies any deltas.

### The gap vs. wsmig
- It is **implicit** (state-driven), not an operator-selected mode.
- It is **broader than failed-only**: it also re-applies changed objects and governance/grant deltas,
  so there is no fast, narrow "process ONLY the previously-failed set" pass.
- It depends on the prior run having **seeded `uc_sync_state`** (a run that died before seeding leaves
  nothing to drive the retry).

### The feature
**Operator model (confirmed with the user 2026-09-24): this is an IMPORT-job-only mode.** The operator
runs *only the import job* and passes the **`run_id`** of the bundle to replay (exactly as import is
run today) plus `retry_failed_only=true`. No re-export, no e2e job — the bundle already exists at
`run_{run_id}`. An explicit `retry_failed_only` toggle (import job only) that:
1. Reads the prior run's non-clean rows from `uc_sync_state` — with per-facet state (Item 8), the set
   is "any object with a facet whose `*_status = failed`" (falls back to `last_action`-based selection
   for legacy rows).
2. Restricts import to **only** those objects **plus their required parents** (catalog / schema, and
   any mask/row-filter function or base table a failed object depends on), skipping everything else
   regardless of deltas. Retry is **per-facet** (Item 8): a #5/#7 object re-governs only; a #2 object
   (dropped after a fresh-create governance fail) re-creates then re-governs.
3. Reads DDL from the bundle at `run_{run_id}` (the failed set's definitions live there). Since the
   operator passes `run_id` anyway, no new input is needed beyond the toggle — but see the run-id note.
4. Preserves fail-closed semantics: a still-failing object stays FAILED; a now-succeeding one flips to
   Created/Updated in state + report + audit.

### Related UX gap surfaced while filing this (candidate sub-feature)
Standalone import does **not** auto-resolve the latest bundle — `03_Import.py:139-147` requires
`run_id` and raises `ValueError("run_id from the Export stage is required")` if blank, then reads
`{export_volume_path}/run_{run_id}`. There is no "use latest run" resolution. Consider an
`auto-latest-run` option (pick the newest `run_*` folder under `export_volume_path`, or the latest
`run_id` in `uc_sync_audit`) so a retry does not require the operator to copy the bundle id by hand.
Decide whether to fold this into `retry_failed_only` or ship it independently.

### Acceptance
- Unit: given a baseline/audit with a mix of clean + failed rows, the selected set is exactly the
  non-clean objects + their required parents; clean/unchanged objects are excluded even when their
  source fingerprint changed.
- A retry-failed-only run over a bundle where "almost everything failed" re-applies the failed set and
  leaves the (few) prior successes untouched (zero writes), with honest report/audit/state.
- `retry_failed_only=false` reproduces today's incremental behavior exactly.
- Full suite green.

---

## 5. Docs: SPN `system` access + governance-only re-apply (DOC UPDATE)

**Timing (decided 2026-09-24):** track doc *requirements* here now (each feature/bug item notes its
doc impact), but do the actual doc *writing* in **one consolidated pass after the code items are
implemented and tested** — testing will refine behaviour and surface new caveats, and docs should
describe the final shipped state, not the in-flight design. This item is the running checklist for
that pass; add a "doc impact" line to any new item so nothing is missed.

**Status:** NOT STARTED (filed 2026-09-22). Prompted by a customer run
(`656570941208899`): the source SPN could read grants (REST) but **not** tags/ABAC
(`information_schema`), because that environment requires `system` access to read a catalog's
`information_schema` — the report came back with full Grants but zero Tags/ABAC, and nothing in the
docs explains the cause or the recovery. Update the doc set in these places:

### 5a. `docs/PERMISSIONS_GUIDE.md` — SPN must have `system` access for tags/ABAC
- State plainly (not as a "verify live" aside): **in some customer environments catalog access does
  NOT imply `information_schema` access** — the source read SPN needs `system` access to read a
  catalog's `information_schema`, which is where governed **tags** and **ABAC policies**
  (`abac_policy_definitions`) are read from. Without it, **grants still migrate (REST permissions API)
  but tags + ABAC silently come back empty** and the run still reports SUCCESS.
- Give the exact grants (from `uc-sync-spn-permissions.md`): `GRANT USE CATALOG ON CATALOG system`,
  `GRANT USE SCHEMA ON SCHEMA system.information_schema`, `GRANT SELECT ON <needed views>` — issued by
  the `system` owner; does NOT make the SPN a metastore admin.
- Add the verification step: run `SELECT * FROM <cat>.information_schema.abac_policy_definitions` and
  `.column_tags` **as the SPN** before trusting a run.

### 5b. `docs/RUNBOOK.md` — troubleshooting signature + governance-only re-apply
- New troubleshooting entry: **"Report shows grants but zero tags / ABAC / policy-matched-columns."**
  Root cause = SPN missing `system` access at Inventory (5a); confirm via the stage-01 log
  (`[governance] … read failed` / `[inventory] WARNING: no source_warehouse_id`) and the two queries.
- New procedure: **re-apply ONLY tags + ABAC without recreating tables** (mirror
  `governance-only-reapply.md`): (1) fix SPN `system` access; (2) re-run Inventory+Export → new bundle
  (no governance-only export mode exists — full `SHOW CREATE` re-capture); (3) Import that new bundle
  with `create_tables/create_views/create_functions/create_volumes = false`,
  `create_abac_policies + apply_tags = true`, `apply_grants + apply_masks_row_filters = false`,
  `import_warehouse_id` set. Note the fail-closed drop is safe on pre-existing tables
  (`_drop_failed_tables` only drops shells created in the same run).

### 5c. `docs/CONFIGURATION_GUIDE.md` — toggles are install-time, not run-time
- Document the `create_*` / `apply_*` toggle families and the **governance-only combo**.
- **Call out the operational gotcha:** the toggles are `${…}` placeholders substituted by
  `00_Install_Jobs` at install time and baked into each job spec's task `base_parameters`; only
  `run_id` is a true job parameter (`airgap_import_target.json`). So to change toggles you either
  **re-run `00_Install_Jobs`** with the new values or **run the `03_Import` notebook directly** with
  its widgets set — "Run now with different parameters" reliably overrides only `run_id`.

### 5d. `docs/PERMISSIONS_GUIDE.md` — target SPN needs `ASSIGN` on account-level governed tags
- New rule: governed tags are **account-level** securables. When the governed tag was created by a
  DIFFERENT principal, the target run SPN needs **`ASSIGN`** on that governed tag to apply it
  (`ALTER … SET TAGS`), or the tag phase fails `GOVERNANCE_PREREQ_MISSING` / `PROTECTION_FAILED`.
  `APPLY TAG` on the catalog is not sufficient for a governed tag owned elsewhere. Grant is issued by
  the tag owner / someone with `MANAGE` on the tag; it does not make the SPN a metastore admin.
  (Confirmed in the field 2026-09-22: tag created by another SPN, target SPN lacked `ASSIGN`.)

### Acceptance
- All docs updated; PERMISSIONS_GUIDE states the `system`-access requirement AND the governed-tag
  `ASSIGN` requirement as first-class rules; RUNBOOK has the troubleshooting signature + the
  governance-only procedure; CONFIGURATION_GUIDE documents the toggles and the install-time gotcha.
- Cross-linked (README points to the troubleshooting entry).

---

## 6. BUG: "protected table dropped fail-closed" reported for tables never dropped

**Status:** NOT STARTED (filed 2026-09-22, CONFIRMED in code). On a governance failure the result
message asserts the table was **dropped** even when it was not. `_record_governance_failure`
(`package_import.py:1458-1462`) returns `dropped=True` for any `_DROPPABLE_TABLE_TYPES` object, and the
caller (`:2054-2065`) immediately sets `result.message = "governance failed; protected table(s) dropped
fail-closed: …"`. But the actual sweep `_drop_failed_tables` (`:1479-1481`) **skips** any table not in
`self._created_tables` (pre-existing / create-disabled → `continue`, no drop). So for a governance-only
or incremental run where the table was NOT created this run, the report/log says "dropped" while the
table is untouched on the catalog. Reproduced in the field: governance-only run failed on tags, logs
said tables dropped, tables were still present.

### Fix
- Decide drop-vs-mark-in-place BEFORE composing the message: only say "dropped" when the target is in
  `_created_tables` (a shell this run created); otherwise say "marked FAILURE (not dropped —
  pre-existing)". Cleanest: have `_record_governance_failure` return the actual disposition (dropped /
  marked-in-place), or defer the message until after `_drop_failed_tables` runs and set it from the
  real outcome. Mirror in the `ABAC_WAREHOUSE_REQUIRED` message (`:2029-2034`), which has the same
  "Matched table(s) are dropped" wording.

### Acceptance
- Unit: governance failure on a pre-existing / create-disabled table → result message says NOT dropped
  and the table still exists; on a shell created this run → says dropped and it is gone.

---

## 7. BUG: incremental re-run silently no-ops governance after a failed governance run

**Status:** NOT STARTED (filed 2026-09-22, CONFIRMED in code). After a governance run where the tag
apply FAILED, a subsequent identical run applies **nothing** ("no-op, no tags"). Cause: tag ops are
attributes excluded from the state upsert (`package_import.py:2009-2010`), so a tag-apply **failure
never marks the owning table's state row non-clean** — the table keeps `last_action` clean
(e.g. skipped_create_disabled) and its `governance_hash` gets seeded to the **bundle** value (as if
applied). On the next run, `DeltaPlan.governance_changed` (`delta.py:241-249`) sees
`governance_hash` unchanged + a clean `last_action` → returns False → `_apply_governance_dir`
(`:1978`) `continue`s and skips the tags. Net: the incremental baseline masks the still-unapplied
governance. Reproduced in the field (run 2 failed on `ASSIGN`; run 3 was a silent no-op).

### Fix
**Implemented by Item 8 (per-facet state tracking)** — the finalized design. In short: a governed-tag
apply failure must propagate to the owning object's state (non-clean facet status), and an object's
`governance_hash` must NOT advance unless its governance actually applied. See Item 8 for the full
column design, roll-up rules, and the DDL/governance scenario matrix. Also feeds Item 4
(retry-failed-only): both need governance failures to be first-class in state.

### Acceptance
- Unit: tag apply fails → owning object state is non-clean / governance_hash NOT advanced → next
  incremental run re-attempts the tag (not skipped). On success, hash advances and a further run
  correctly skips.
- Regression: a clean governance run still no-ops on a truly unchanged re-run.

### Operational workaround (today, no code change) — see reply
Fix the blocking privilege, then **reset the `uc_sync_state` baseline** (delete rows for the affected
objects, or the whole table — it is only the incremental tracker, never target data) so the next
governance-only run is treated as full and re-applies tags + ABAC.

---

## 8. Per-facet state tracking (the fix for #6 + #7)

**Status:** DESIGN FINALIZED 2026-09-24 (with the user). Root problem: `uc_sync_state` fingerprints an
object in three facets (`ddl_hash` = structure + inline masks, `governance_hash` = tags, `grants_json`
= grants) but records a **single `last_action`** and **advances all hashes from the source regardless
of whether each facet's apply succeeded**. A partial failure (structure OK, tags failed) is recorded
as fully clean → the incremental baseline masks the unapplied governance (#7), and failure messaging
is decoupled from reality (#6).

### The invariant
A facet recorded `applied`/clean MUST mean that state actually exists on target right now. The
fail-closed drop **undoes** a fresh table's DDL, so it must roll `ddl_status` back to `failed`.

### Columns to add (3)
```
ddl_status         STRING   -- outcome of the structure/DDL apply   (pairs with ddl_hash)
governance_status  STRING   -- outcome of the tag apply             (pairs with governance_hash)
grants_status      STRING   -- outcome of the grant apply           (pairs with grants_json)
```
Vocab: reuse the shared `uc_sync.vocab` `last_action` set, minimal subset in practice —
`created`/`updated` (applied), `skipped` (idempotent no-op / unchanged), `skipped_create_disabled`
(ddl facet only), `manual`, `failed`, and `not_selected` for a facet whose toggle was off
(`apply_grants=false` → `grants_status=not_selected`).

### Backward compatibility (BUs that already migrated + have populated state/audit)
Confirmed approach (matches the user's proposal and the utility's existing upgrade pattern): the
utility already runs an idempotent `ensure_table()` on every run (`sync_state.py:166`,
`ensure_state_table_sql`) that `CREATE TABLE IF NOT EXISTS` + in-place upgrades. Extend it to:
1. **Detect + add the missing columns** with `ALTER TABLE … ADD COLUMNS (ddl_status, governance_status,
   grants_status STRING)` when absent — a no-op when present, so it is safe to run on every existing
   `uc_sync_state` (including populated ones). This is exactly the pattern that already added the
   incremental fingerprint columns, so pre-existing baselines keep working.
2. **One-time backfill** of the new columns for legacy rows (status IS NULL), derived from the existing
   single `last_action` + hash presence + `failure_category`:
   - `last_action` clean (`created`/`updated`/`adopted`/`skipped`) → each facet with a non-empty hash →
     `applied` (or `skipped`); a facet with an empty hash → `not_selected`/`not_attempted`.
   - `last_action = failed` → route the failed facet from `failure_category`
     (`GOVERNANCE` → `governance_status=failed`; `STORAGE`/`SCHEMA_EVOLUTION` → `ddl_status=failed`;
     etc.); other facets inferred from hash presence. When category is ambiguous, be conservative
     (mark the object's facets so the next run re-verifies rather than silently skips).
3. **No target-data or object impact.** This is purely `uc_sync_state` schema/metadata; it never
   touches migrated tables or their data. A BU with already-populated target tables is unaffected —
   the drop-coupling (#2) only ever applies to tables *freshly created within a run*, never pre-existing
   populated ones.
4. **`uc_sync_audit` needs no schema change** — it is append-only, one row per operation per run, so it
   is *already* per-facet at the row level; the per-facet concept only affects the per-object
   `uc_sync_state` baseline.
5. **Vocab:** no `last_action` vocabulary change is needed — the facet statuses reuse existing values,
   and legacy `last_sync_status` values are already folded by `vocab.is_clean_action`
   (`_LEGACY_STATUS_TO_ACTION`). If any *new* value were ever added later, extend that legacy map, not
   a data migration.

Better-way note: the alternative to a backfill is to treat NULL facet status as "unknown → re-verify"
(never "clean"), which self-heals on the next run without a migration pass. Backfill is preferred so
the first post-upgrade run doesn't re-touch every clean object; the NULL=re-verify rule is the safe
fallback for rows the backfill can't classify.

### "Just drop `uc_sync_state` and re-run" — is that 100% safe? (decided 2026-09-24)
**Yes, it is safe, but it is NOT equivalent to incremental, and it is not the productized default.**
Dropping `uc_sync_state` on an already-migrated BU and running the new version:
- Is **100% safe for the target's data**: no baseline → a **full** run; every pre-existing table is
  caught by the up-front existence probe → `SKIP_EXISTING`, never added to `_created_tables`, so the
  fail-closed drop can **never** drop it (drops only shells created *in that run*). Tables are never
  auto-ALTERed; views are `CREATE OR REPLACE` (idempotent); grants/tags/ABAC re-apply idempotently.
- **Correctly re-seeds a clean baseline** and, in that full pass, **re-applies governance/grants to
  everything** — which fixes any previously-failed governance and creates genuinely-missing objects.
  After that one pass, subsequent runs are properly incremental again.
- **Caveat — it is a FULL pass, not "only incremental + failed."** It re-runs governance/grant SQL
  across *all* objects (skip-existing on structure), so for a large catalog it is heavier/slower than
  an incremental run, and it **loses the cumulative state history** (`first_seen`, the Outstanding
  roll-up). `uc_sync_audit` is separate and preserved.
- **Recommendation:** ship the ALTER+backfill above as the **default** (seamless, no operator action,
  preserves history, keeps the first post-upgrade run cheap/incremental). Document **drop + one full
  run** as a **sanctioned, safe manual fallback** for BUs that don't care about state history or when
  the backfill isn't available yet. So the drop can serve as an interim answer if we defer the
  backfill — it just costs one full re-apply pass + history.

### Rules
1. **Hash advancement gated on success.** Facet `applied`/`skipped` → write `<facet>_hash = source
   fingerprint`, set status. Facet `failed` → do NOT overwrite `<facet>_hash` (preserve prior), set
   status `failed`. `not_selected`/`not_attempted` → preserve hash.
2. **Delta re-applies a facet when stale OR last-failed:**
   `facet_needs_apply = (source_hash != state_hash) OR (state_status == 'failed')`. The `OR failed`
   clause is essential — it catches "source unchanged but the apply failed" (the field no-op).
   This replaces today's hash-only `DeltaPlan.governance_changed` (`delta.py:241-249`).
3. **Roll-up `last_action`** (highest wins): any *attempted* facet `failed` → `failed` (fail-closed,
   drives Outstanding + bug-#18 re-attempt); else any `manual` → `manual`; else something
   applied/changed → `created` (fresh) / `updated` (pre-existing facet change); else all unchanged →
   `skipped` (or `skipped_create_disabled`). Retry is **surgical** — only the failed facet(s) re-run,
   so `failed` is honest AND cheap.
4. **Derive facet statuses from the FINAL (post-Phase-4) results**, and aggregate per object: the
   create/skip result → `ddl_status` + hashes; the tag/ABAC apply result(s) → `governance_status`;
   the grant apply → `grants_status`. Because `_drop_failed_tables` mutates the create result to
   FAILURE in place *before* the state upsert, a dropped fresh table naturally records
   `ddl_status=failed` (the drop-coupling below). The MERGE gates each hash:
   `CASE WHEN source.<facet>_status IN ('applied','skipped') THEN source.<hash> ELSE target.<hash> END`
   (extends the existing `_PRESERVE_ON_UPDATE` pattern).
5. **Drop-coupling:** when the fail-closed sweep drops a freshly-created table, `ddl_status=failed`
   and `ddl_hash` is not advanced → next run re-creates AND re-governs (never governance-only on a
   dropped table). ABAC follows the same rule (its policy object row self-tracks).

### DDL × governance scenario matrix (the test targets)
| # | DDL facet | Governance facet | Dropped? | ddl_status | governance_status | last_action | Next-run retry |
|---|---|---|---|---|---|---|---|
| 1 | created (fresh) | applied | no | created | applied | created | none |
| 2 | created (fresh) | **failed** | **YES** | **failed** (drop rolls back) | failed | failed | re-create + re-govern |
| 3 | created (fresh) | not attempted (toggle off) | no | created | not_selected | created | none |
| 4 | pre-existing (skip) | applied | no | skipped/adopted | applied | updated | none |
| 5 | pre-existing (skip) | **failed** | no | skipped | failed | failed | governance only (the field case) |
| 6 | create-disabled | applied | no | skipped_create_disabled | applied | updated | none |
| 7 | create-disabled | failed | no | skipped_create_disabled | failed | failed | governance only |
| 8 | unchanged (incremental) | unchanged | no | skipped | skipped | skipped | none |
| 9 | **failed** | *cannot attempt* (in `_absent_objects`) | no | failed | not_attempted | failed | re-create DDL, then govern |
| 10 | create-disabled | failed (table truly absent) | no | skipped_create_disabled | failed | failed | governance-only, keeps failing (operator config error → surface clearly) |

`(ddl=failed, governance=applied)` is impossible — a create-failed object goes into `_absent_objects`
(`package_import.py:1964`) and the governance phases skip it.

### Grants replay for an object created later (external volume / external table) — a gap this closes
Scenario raised 2026-09-24: external volume/table creation was OFF (BYO) or FAILED (e.g. external
location not yet present); the ops team creates the object on target **later**; a subsequent run should
then replay its **grants** (and, for volumes, copy files). Behaviour:
- **`copy_volume_data` (FEAT-4)** is independent and works in a later run once the volume exists — it
  copies files via the Files API with its own incremental control table; not gated by object state.
- **Grants replay has a gap TODAY** and this item closes it. If run 1 recorded the object clean
  (`skipped_create_disabled`, "assumed pre-existing") but the object did **not** actually exist, the
  grant apply silently warned, yet the object was recorded clean+unchanged → the next incremental run
  `should_skip_object` → **grants never re-applied** to the now-created object. Under per-facet state,
  the run-1 grant failure must set **`grants_status=failed`** (not a soft warning), so rule #2
  (`re-apply when status==failed`) re-plays grants on the next run once the object exists. **Required
  refinement:** `_apply_grants_file` currently swallows a grant error into a returned warning
  (`package_import.py:2111-2131`) — for the facet model it must surface as `grants_status=failed` when
  a grant statement raised, so the retry fires. Same logic covers external tables.
- Net: with this item, once the ops team creates the external volume/table, the next run replays its
  grants (grants facet was failed) and — if `copy_volume_data=true` — copies its files. Add both as
  test cases (below).

### Acceptance
- Unit per matrix row: correct `ddl_status`/`governance_status`/`last_action`, hashes advanced only on
  success, and the modelled next-run retry (surgical for #5/#7, full for #2, DDL-first for #9).
- Unit: an object recorded with `grants_status=failed` (grant apply raised) is re-attempted for grants
  next run even when its ddl/governance facets are clean+unchanged.
- Regression: a fully-clean object still no-ops on a truly unchanged re-run.
- Ties into Item 4 (retry-failed-only reads per-facet status) and closes #6 + #7.

---

## 9. Structured logging across the notebooks (observability)

**Status:** NOT STARTED (filed 2026-09-24). Today the run output is sparse/ad-hoc `print`s, so a
customer-side failure can't be diagnosed from the logs alone. Add real, levelled logging (INFO /
WARNING / ERROR, DEBUG for verbose detail) everywhere it makes sense, so an operator can hand over the
notebook output and it is enough to **pinpoint the issue** without a live repro.

### Logging helper (user-provided, validated 2026-09-24)
Adopt the user-supplied helper as the basis (`get_app_level_logger` / `get_log`): a named app logger
(`automigrate`-style; use `uc_sync`), per-module **child loggers** via `getChild(module_name)` so
`%(name)s` gives module context, a stdout `StreamHandler`, and an **optional `StringIO` buffer handler**
— the buffer is valuable: it captures the whole run's log into a string we can write alongside the
report to the volume, so the handed-over artifact already includes the logs.

**Validation of the "better output / real stack trace" claim:** it is the Python standard `logging`
module (not a different library). The full-stack-trace benefit is real but comes from **how it's
called** — `log.exception(msg)` or `log.error(msg, exc_info=True)` inside an `except` block prints the
complete traceback (vs a bare `print(str(exc))`), not from the helper itself. So the helper is a good
foundation; the win is codifying `log.exception(...)` in every `except`.

**Three tweaks before adopting:**
1. **`logger.propagate = False`** — in Databricks notebooks the root logger often already has handlers,
   so without this every line is printed twice. (Not set in the provided code.)
2. **Remove the `print("DEBUG: get_log:...")`** line — a debug artifact, not for production.
3. **Carry `run_id` + stage** — the fixed formatter has no field for them; add via a `LoggerAdapter`
   (or a small `extra=` + a formatter field) so every line includes `run_id` and stage
   (INVENTORY/EXPORT/IMPORT), not just module name.

### Requirements
- The helper lives in `src/uc_sync/` (e.g. `logging_util.py`) and is used by `01/02/03` and every
  `src/uc_sync/*` module via `get_log(__name__)`. Consistent format including timestamp, level, logger
  name, `run_id`, stage, and message — greppable, `run_id` + stage on every line.
- **INFO** at every meaningful step: stage start/end, phase start/end with counts (e.g. "Phase 1
  structural: 1667 objects, 1667 created, 0 skipped"), per-object create/skip with target name,
  governance apply per object, grant apply, drop-sweep actions, state/audit upsert counts, delta
  decision per object on incremental (applied/skipped + why).
- **WARNING** for recoverable/degraded conditions: governance read returned empty, retry/backoff
  fired, grant/owner-transfer warning, best-effort ops-table write skipped, SC/EL discovery empty.
- **ERROR** for every failure with the FULL context needed to diagnose: object, statement (or its
  head), the raw exception, the classified error_code, and the fail-closed consequence (dropped /
  marked-in-place). No silent `except: pass` — every caught exception logs at WARNING or ERROR.
- Emit the exact governance-gap signatures as first-class log lines (so #5/#7-class issues are obvious
  in the log): "governance read EMPTY for <catalog> — check source SPN system access", "tag apply
  FAILED for <obj>: <err> — object marked failed (not dropped, pre-existing)", etc.
- Make the level configurable via a widget/param (`log_level`, default INFO; DEBUG opt-in).
- Keep secrets/credentials out of logs (redact client secrets, tokens).

### Acceptance
- A deliberately broken run (missing warehouse; missing `system` access; missing `ASSIGN`; missing
  `import_warehouse_id`) produces logs from which the root cause is identifiable **without** the
  workspace — verified by handing the log to a fresh reader.
- Every `except` in `src/uc_sync/` and the notebooks logs (no silent swallow).
- Log format is consistent and greppable; `run_id` + stage on every line.

---

## 10. Group + order widgets via numbered labels (UX, LOW effort)

**Status:** NOT STARTED (filed 2026-09-24). Operators want related widgets clustered and in a sensible
order (source connection together, scope together, all `create_*` toggles together, all `apply_*`
together, cluster/proxy together, etc.).

**Effort: LOW — verified mechanism.** Databricks sorts widgets **alphabetically by their displayed
`label`** (the label defaults to the widget name when not set). So we set a **numbered `label`** (the
3rd arg of `dbutils.widgets.*`) and DO NOT touch the widget **name/key**. That means:
- `dbutils.widgets.get("source_workspace_url")` — unchanged (key is unchanged).
- Job-spec `base_parameters` keys + `${...}` placeholders — unchanged.
- `_simple` / toggle passthrough lists — unchanged.
Purely additive: add a label arg to each widget definition in `00/01/02/03`. No renames, no spec
churn, no regression surface. A mechanical pass over the widget blocks (~an hour).

**Scheme.** Group prefix + intra-group order, e.g. `"1a. Source · Workspace URL"`,
`"1b. Source · Client ID"`, …, `"3a. Create · Catalogs"`, `"3b. Create · Schemas"`, …,
`"4a. Apply · Grants"`, …. Groups: 1 Source connection, 2 Scope (catalogs/schemas/exclude_regex),
3 Create toggles, 4 Apply toggles, 5 Warehouses, 6 Cluster, 7 Proxy, 8 Run controls. Keep the numbering
scheme documented so new widgets slot into the right group.

**Caveat.** If an operator has ever saved a *custom* drag-reordered widget layout on a notebook,
Databricks stops auto-sorting for them (their saved layout wins). The labels still improve the default
and every fresh notebook; note this in the docs.

### Acceptance
- Widgets render grouped + in the intended order on a fresh notebook (no custom layout saved).
- Every `dbutils.widgets.get(...)` key and every job-spec `base_parameters` key is unchanged (grep
  diff shows only added `label` args) — a run produces identical behavior to before.

---

## Testing strategy

Every item above is validated by a human-style, CLI-driven E2E pass on real Databricks. The QA agent
that performs it — its exact operating contract, allowed actions, and stop-and-ask rules — lives in
**`plans/qa-testing-agent.md`** (referenced so future plans can point at it directly). Mode under
test: **direct mode, BYO** (catalogs + schemas pre-created on source and target).

### Fixtures — build on the single comprehensive bed (one catalog, not three)
Use and **extend the existing single-catalog test bed** `ai27_ucsync_testcatalog`
(`fixtures/recreate.py testcat`, stages `tc_*`) — **not** the 3-catalog build (gov_src/finance/sales).
Everything under test lives in **one catalog**. Never fork a new fixture set; always grow this one bed,
and **always keep the governance-failure cases** (`tc_negative`) in it. When we add a feature, add its
fixture to this bed (a `tc_*` stage) so the bed is the cumulative superset for every future test.

The bed must contain **all previously-existing test cases** plus these, in one catalog:
- **Plain tables** (simple, no governance).
- **Tables with a column masking function** (classic `SET MASK`).
- **Tables with a row filter** (classic `SET ROW FILTER`).
- **Tables with both** a column mask and a row filter.
- **Tables with column tags whose masking/row-filtering is enforced by a catalog-level ABAC policy**
  (the tag-driven ABAC way — tag on columns + one policy at the catalog, no per-table `SET MASK`).
- **One of every object type**: managed table, external table, view, materialized view, streaming
  table, event-log table, Lakebase synced table, monitor, function, managed + external volume, metric
  view — plus report-only types (streaming table / MV / event log) so their report-only handling is
  exercised.
- **Multiple governance combinations** across the above (tags-only, ABAC-only, masks-only, mixed;
  object-level + column-level tags).
- **Governance-failure cases, always present** (e.g. an object whose governed tag needs `ASSIGN` the
  run SPN lacks; an object referencing an un-migrated dependency) to drive matrix rows #2 / #5 / #7.
Confirm/extend the bed for any object types not yet covered (Lakebase synced table, monitor, event-log
table in particular) before the first QA pass.

- **Parallelism stress bed (Item 3), added 2026-09-25** — `fixtures/69_testcat_parallel.sql`
  (stage `tc_parallel`, in `TC_ORDER`) creates **120 plain tables across 3 schemas**
  (`parallel_a`/`parallel_b`/`parallel_c`, 40 each), **5-10 rows each**, all tagged
  `TBLPROPERTIES('ai27_uc.parallel'='true')`. The matching target BYO schemas are in
  `32_testcat_target_byo.sql`. Purpose: exercise the export SHOW CREATE pre-capture pool and
  the import within-`_type_rank`-level pool under real load. QA runs the e2e job at
  `parallel_threads=4` (or higher, ≤ the warehouse's max concurrent queries) and confirms all
  120 tables + rows replicate, the report/audit/state are identical to a `parallel_threads=1`
  run (order-independent), and no object is lost/duplicated. Unit coverage already proves
  `parallel_threads=1` vs `=4` produce identical results/ordinals (`tests/test_import_parallelism_feat3.py`,
  `tests/test_parallel_stage_feat3.py`).

### Scenario coverage (map test cases to Item 8's matrix + the bugs)
The bed must exercise every row of Item 8's DDL × governance matrix, plus the
governance-capture and permissions paths:
- **#1 / #4 / #6** (happy paths): fresh + pre-existing + create-disabled objects, all with governed
  tags + one ABAC policy, all applying cleanly.
- **#2** (fresh create + governance fail → drop → `ddl_status=failed`): force a governance failure on a
  freshly-created table (e.g. missing `ASSIGN` on its governed tag) and confirm the drop + that the
  next run re-creates AND re-governs.
- **#5 / #7** (pre-existing/create-disabled + governance fail → surgical governance retry): the
  field case — governance-only run, tables pre-existing, tag apply fails, table NOT dropped, and the
  next run (after fixing the grant) re-applies governance only.
- **#9** (DDL fails → governance not attempted): e.g. external table with no external location.
- **#10** (create-disabled but table truly absent): operator misconfig — persistent `failed`.
- **Governance-capture gap:** run with the source SPN lacking `system` access → tags/ABAC empty but
  grants present (Item 5); then grant `system` access and confirm capture.
- **Incremental correctness (Item 8):** after each governance failure, re-seed and confirm the delta
  plan re-attempts only the failed facet (per-facet status), not the whole object, and does not no-op.
- **External object created later (grants replay + file copy):** run 1 with an external volume/table
  create OFF or failed (external location absent); create the object on target out-of-band; run 2 →
  confirm its **grants are replayed** (grants facet was `failed`) and, with `copy_volume_data=true`,
  its **files are copied**. Repeat for an external table (grants replay).
- **`exclude_regex` (Item 2)** once implemented: whole catalog minus a few tables.

### Per-run validation (what "validated" means)
For each report Sheet, cross-check the report against the live target via CLI/API (random sampling +
anything suspicious): Catalogs/Schemas/Tables/Views/Functions/Volumes exist with expected
props; Grants match; **Tags Applied** and **ABAC Policies** are non-empty and match source; Outstanding
lists exactly the expected failures; and `uc_sync_state` rows carry the expected per-facet
`*_status` + `last_action`. Incremental cases stop before re-seeding and ask the user to confirm the
seed plan (see the QA contract).

### Bugs identified (populated during testing)
_(QA agent appends confirmed bugs here — one subsection per bug, with repro + evidence — and reports
them in its stop-and-ask summary after each run.)_

#### BUG-QA1: parallel import breaks same-rank FK / intra-rank dependency ordering (CONFIRMED 2026-09-25, round 1)
**Repro:** e2e LIVE, commit `bf0c069`, `parallel_threads=4`, testcat bed. `core_tables.employees`
has `CONSTRAINT emp_dept_fk FOREIGN KEY(dept_id) REFERENCES core_tables.departments`. Both are
same `_type_rank` (TABLE), so `_process_ddl_level` submits them to the pool concurrently. `employees`
was attempted before `departments` committed → `CREATE TABLE employees` FAILED with
`[TABLE_OR_VIEW_NOT_FOUND] … departments cannot be found`. **Evidence:** on target, `departments`
EXISTS but `employees` is absent; import.log shows the FK error. Cascaded to 4 dependents of
`employees` (`emp_summary`, `emp_over_masked`, `emp_dynamic`, `emp_metrics`) → 5 spurious FAILUREs
+ a red run. At `parallel_threads=1` the filename sort (departments < employees) avoids it, and the
**120 independent `tc_parallel` tables replicated cleanly at `threads=4`** (so the pool itself is
correct — the gap is only intra-rank *dependencies*). The plan's Item-3 "intra-rank independence
assumption" caveat. **Fix:** a bounded failed-set retry pass within each rank level (re-run the
still-FAILED objects sequentially after the parallel pass; each pass resolves one dependency layer).
**Status: FIXED same session** — `_process_ddl_level` retry pass + `tests/test_import_parallelism_feat3.py`.

#### Env note (NOT a code bug): orphaned ops audit/state tables after the 2-week Azure wipe
`uc_sync_audit`/`uc_sync_state` metadata survived the wipe but their gov-account storage was
recreated empty → `ensure_table`'s CREATE-IF-NOT-EXISTS sees the metadata and skips, so append hits
`DELTA_TABLE_NOT_FOUND` and the best-effort ops write is skipped (no state/audit persisted). Fix in
the rebuild recipe: DROP the orphaned `ai27_ucsync_ops.ops.uc_sync_{audit,state,volume_files}` tables
so the run recreates them fresh. (Recorded in the env-blocker memory.)

---

## Implementation sequencing (proposed 2026-09-24)

Ordered to front-load the foundations that make everything else safe to build and test. Dependencies:
#4 needs #8; #6 + #7 are *closed by* #8; #3-**import** should follow #8 (its thread-safety/result
aggregation overlaps #8's per-object aggregation) — but #3-**export/inventory** are independent and can
land early; #9 is independent and foundational; #1/#2/#10 are independent quick wins; #5 is the
consolidated post-testing pass.

1. **#9 Structured logging** — do FIRST. Independent, low-risk, high-leverage: real logs make every
   later change (and the QA passes) debuggable, and give us the "hand me the log" capability now.
2. **#3-export/inventory parallelism + #10 widget grouping + #2 `exclude_regex`** — LOW-effort,
   independent quick wins; can go in parallel early. #3-export especially cuts the re-export time the
   governance flows keep paying.
3. **#8 Per-facet state tracking** — the correctness backbone. Closes #6 and #7, and is a prerequisite
   for #4. Land the backward-compat (ALTER+backfill) with it. Fold in #6's messaging fix here.
4. **#6 verify** — confirm the "dropped" messaging is correct once #8 lands (or apply the tiny
   standalone message fix if #8 slips).
5. **#4 Retry-failed-only** — builds directly on #8's per-facet `*_status`. Import-job-only + `run_id`.
6. **#1 Remove `mapping_file_path`** — independent cleanup; needs the config-resolution test pass.
7. **#3-import parallelism** — biggest/riskiest; do AFTER #8 so the per-object result/state aggregation
   is settled (parallelism reuses it). Gate behind `parallel_threads` (default keeps sequential).
8. **#5 Docs** — consolidated pass after the code items are implemented and the E2E testing is done.

**Testing throughout:** each item ships with its own acceptance (per-item). Run the full human-style
E2E pass (per `plans/qa-testing-agent.md`, on the single `testcat` bed) after #8 + #4 land — that's
when the matrix (#1–#10), governance-failure retry, and grants-replay behaviours are all exercisable —
and again before declaring #3 done.
