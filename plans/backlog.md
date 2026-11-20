# UC Governance Migration — open backlog

Running list of **open** work for the UC governance migration utility: bugs, gaps and follow-ups.
One numbered item per section. When an item ships, remove it from this file (the CHANGELOG is the record
of what shipped). Inline `(backlog item N)` comments in `src/` refer to an older numbering whose items
have all shipped.

**State as of 2026-11-19:**
- Code under test: v1.2.2 on branch `fix/v1.2.2-dryrun-state-volume-copy` @ `9228af4` (not yet merged to
  `main`).
- Shipped and removed from this file:
  - the former items 1–10: `mapping_file_path` removal, `exclude_regex`, stage parallelism,
    retry-failed-only, permission docs, per-facet state (closing the old #6/#7), structured logging,
    widget grouping;
  - BUG-QA1 (parallel FK race) and BUG-QA2 (retry-failed-only skipped ABAC policies, fixed at
    `package_import.py:2472`);
  - the v1.2.2 fixes: dry-run state, dry-run volume copy, volume-copy URL encoding, create-toggle flip,
    per-grant Grants sheet.
- The items below were found in the v1.2.2 live QA (2026-11-18) on the `testcat` bed. The user triaged
  them as **not release-blocking** (cosmetic or corner cases); they stay here as the open backlog.

Severity: **M** = medium (wrong outcome an operator could act on), **L** = low (report fidelity only).

---

## 1. Ownership not migrated when the owner also holds explicit grants (was BUG-QA3) — M

**Code:** `04bb935`. **Run:** job `778661300035503` (End-to-end Live, run-as target SPN `e3341381…`), run
`233040313561268`, testcat bed → BYO target `ai27_ucsync_testcatalog_tgt` (catalog_mapping_json rename).
**Repro:** source view `core_tables.emp_summary` is OWNED by group `wsmig_acc_group_3`, which also holds an
explicit `SELECT` on it (fixture `65_testcat_acl.sql`). Run a full import.
**Result:**
- On target `emp_summary` is owned by the **run-as SPN**, not `wsmig_acc_group_3`.
- The exported `migrated/grants/VIEW_…__emp_summary.sql` has only the `GRANT SELECT`, **no
  `ALTER VIEW … OWNER TO`**. A control view with a plain owner (`emp_dynamic`) does get its `OWNER TO`.
- The report is silent: no OWNER row for the object, so nothing reads "NOT APPLIED".
- All other ownership comparisons matched (176 tables, 14 schemas, 15 routines, 3 volumes).

**Cause:** `inventory.py:628` adds the synthetic `{"privileges": ["OWNER"]}` entry ONLY when the owner has
no explicit grant row, so `sql_ddl.grant_statements_for_object` never emits `ALTER … OWNER TO`. This is a
common production shape (owner groups also hold SELECT/MODIFY). It predates v1.2.2.
**Fix direction:** always emit the owner (append `OWNER` to the owner's privilege list, or a separate
OWNER entry); unit test with an owner that also holds a grant.
**Workaround:** list affected objects on source (owner ∈ grantees of the same object) and run
`ALTER … OWNER TO` on target.

## 2. Volatile `spark.sql.statistics.*` properties in the DDL fingerprint → false "Updated" (was BUG-QA9) — M

**Run:** job `778661300035503`, run `743342065916493` vs run `987377591443883` ~40 min earlier, with **no
source DDL change**.
**Result:**
- `core_tables.departments`, `core_tables.employees` and `advanced.emp_docs` read **`Updated`** (state
  `last_action=updated`).
- Their masks were re-applied and their ownership re-transferred.
- Diffing the two runs' `migrated/inventory/objects.json`, the only changed keys are
  `definition.properties` / `properties` / `last_modified_at` / `source_definition_hash`, all of them
  `spark.sql.statistics.*` (`auxiliaryInfo` AUTO_STATS → PREDICTIVE_ANALYZE, `colStats.*.createdAt`, new
  `historyStats.*`). That is Predictive Optimization's automatic ANALYZE on the source.

**Cause:** `fingerprints.ddl_fingerprint` hashes the whole `definition`, including `properties`. With
Predictive Optimization on, tables read "Updated" on many incremental runs and their governance, grants
and ownership get re-issued. This is likely behind the unexplained "Updated" counts in the field (e.g.
135 in one BU). No data impact: CHANGED tables are never ALTERed or recreated.
**Fix direction:**
- Fingerprint only user-settable TBLPROPERTIES. Exclude `spark.sql.statistics.*`, and audit
  `delta.lastCommitTimestamp`, `delta.lastUpdateVersion` and clustering stats.
- Unit test: a stats-only property change yields UNCHANGED.
- CHANGELOG note: the upgrade changes every stored `ddl_hash` once (a one-time "Updated" wave).

## 3. Re-attempting a pre-existing MASKED external table fails "permission denied" → recorded failed forever (was BUG-QA10) — M

**Run:** End-to-end Live `create_tables=true` right after a `create_tables=false` run: job
`778661300035503`, run `305848580758031`. Every `skipped_create_disabled` object is re-attempted.
**Result:**
- 161 tables and 2 external tables were adopted correctly.
- `external_store.invoices_ext` (EXTERNAL, mask on `vendor`, present and intact on target) reads **FAILED**:
  `EXTERNAL_CREATE_PERMISSION_DENIED`, hint "run principal lacks CREATE EXTERNAL TABLE", raw error
  `PERMISSION_DENIED: Path-based access to table … with row filter or column mask not supported`.
- The SPN has ALL PRIVILEGES on the EL; unmasked siblings at sibling paths were adopted.
- State says `failed`, so the table re-fails on every run: a permanent false Outstanding entry with the
  wrong hint.

**Cause:** for an existing object the replay still issues `CREATE EXTERNAL TABLE IF NOT EXISTS … LOCATION`.
UC rejects path-based access to a masked or row-filtered table instead of no-op'ing. The except branch of
`_import_ddl_file` only treats already-exists / location-conflict errors as SKIP_EXISTING. Any re-attempt
hits it: toggle flip, a prior failed facet, or retry-failed-only.
**Fix direction:**
- When the pre-create probe finds the object present, skip the CREATE and record SKIP_EXISTING.
- And/or treat that error as already-exists when `_object_exists` is true.
- Unit test: an existing masked external table re-attempted → `adopted`.

## 4. Governance failure on a create-disabled table is visible only in `uc_sync_audit` (was BUG-QA8) — M

**Run:** End-to-end Live with `create_tables=false` (run-now `notebook_params`), job `778661300035503`, run
`743342065916493`. Before the run, `governed.abac_ext` was absent on target (dropped fail-closed earlier).
**Result:**
- The job hard-fails with 2 governance failures.
- `uc_sync_audit` records `abac_ext` = `FAILURE / PROTECTION_FAILED` ("marked FAILURE (not dropped —
  pre-existing) … TABLE_OR_VIEW_NOT_FOUND") plus its ABAC policy `GOVERNANCE_FAILED`.
- Everywhere else the table looks clean:
  - the Tables sheet shows `Skipped (create disabled)`;
  - the Summary shows `Failures (1)` (only the policy), and so does Outstanding;
  - `uc_sync_state` has `skipped_create_disabled`, a CLEAN action.

**Likely cause:** the create-disabled branch of `_import_ddl_file` returns before registering the object in
`_created_objects`, so `_mark_ungoverned_objects` can't flip the object's own result to FAILURE. This is
matrix rows #5/#7/#10 under BYO.
**Fix direction:** register create-disabled results in `_created_objects` (the flag-not-drop path
applies); unit test with create_tables=false plus a failing ABAC policy.

## 5. Dry-run Grants sheet reads APPLIED for create-disabled securables (was BUG-QA5) — L

**Run:** End-to-end Dry Run job `363482019492908`, run `265696774992054`.
**Result:**
- 18 rows `APPLIED` (9 CATALOG + 9 SCHEMA grants) and 16 OWNER rows `— (no ownership transfer this run)`.
- Every other dry-run grant reads `DRY RUN (validated, not applied)`.
- Nothing was applied: the create-disabled branch skips `_apply_grants_file` on a dry run, but leaves
  `status=SUCCESS` with no dry-run marker, so `_grant_gov_status` falls through to APPLIED.

This is a gap in the v1.2.2 Grants-sheet rework.
**Fix direction:** stamp a dry-run marker on that path (or render DRY RUN for every grant of a dry-run
import); add a dry-run case to `test_grants_sheet_per_grant_statuses`.

## 6. Dry-run report mislabels outcomes (was BUG-QA6) — L

**Run:** same dry run.
**Result:**
- The Governed Tags sheet shows all 6 tags `Created`. `_create_governed_tags` sets `message="dry run"`
  but keeps `status=SUCCESS, action=CREATE_OR_SKIP`.
- The Summary roll-up reads `Created 9 / Skipped (unchanged) 204 / …` (the incremental plan's verdict),
  while every per-type sheet reads `DRY RUN (validated, not applied)`.
- Nothing was written (state and volume control were verified byte-identical before and after).

**Fix direction:** governed-tag dry-run results get `status=PENDING, action=DRY_RUN`; on a dry run the
Summary uses one "Dry run (validated, not applied)" bucket, or labels the roll-up "planned".

## 7. Unchanged ABAC policies read blank on the ABAC Policies + Policy Matched Columns sheets (was BUG-QA7) — L

**Run:** unchanged incremental re-run, job `778661300035503`, run `926863813754883`.
**Result:**
- The 7 unchanged policies have an empty `import_status`, and so do their 9 Policy Matched Columns rows.
- The Summary TOTAL drops to 232 (239 objects − 7).
- Every other sheet labels the skip.

**Cause:** the ABAC phase `continue`s past an unchanged policy without emitting a result.
**Fix direction:** emit an UNCHANGED result for a delta-skipped policy (as the object phase does).

## 8. Grants sheet OWNER rows blank for inventory-only report-only objects (was BUG-QA4) — L

**Run:** job `778661300035503`, run `233040313561268`.
**Result:**
- Blank `import_status` on the OWNER rows of `advanced.st_events` (STREAMING_TABLE),
  `advanced.employees_drift_metrics` and `advanced.employees_profile_metrics` (MONITOR_METRIC_TABLE).
- `mv_emp_by_dept` correctly reads `Skipped (object not migrated)`.

**Cause:** these types have no import result (no DDL file).
**Fix direction:** with no import result and an inventory row that is out of scope for migration, render
`Skipped (object not migrated)`.

## 9. UC (schema-level) secrets are not inventoried — L (coverage gap)

`inventory.py:503` `_iter_uc_secrets` is a stub that returns `[]`, so UC secrets never appear in the report,
not even as report-only. Workspace secret scopes are a different, non-UC feature.
**Fix direction:** implement the hook once the schema-level UC secrets API is confirmed GA; report-only,
best-effort; add to the testcat bed.

## 10. Investigation (not reproduced): account-group grant missing from the import Grants sheet

**Field report (2026-10-26):** a table whose only grant was SELECT to an account group (not imported into
the target workspace) showed only its OWNER row on the import Grants sheet.
**Internal repro:** run `50562196062359` did **not** reproduce it. The grant was captured, applied to
target (even with the group unassigned from the target workspace) and reported APPLIED.
**Hypotheses ruled out in the field:**
- the grant being inherited (it is direct);
- the source_client_id ≠ the job run-as SPN (one SPN does everything).

**Still open:** a grant added after the inventory ran (check the audit log), or a transient/filtered
permissions-API read. If it recurs, start from the customer's source SPN grants and the "inherited from"
column.

## 11. A revoke-only change reads "Updated" although nothing is applied on target — L

**Run:** job `778661300035503`, run `123050815223903` (seed `tc_incremental_v122`, #10:
`REVOKE SELECT ON stage_.orders FROM wsmig_acc_group` on source).
**Result:**
- The Grants sheet correctly reads `Removed in source (not revoked)`, and the target grant is intact.
- The Tables sheet shows `stage_.orders` as **`Updated`**, the Summary counts it in `Updated`, and state
  records `last_action=updated`. Nothing was written to the table on target.

**Cause:** `delta.py` maps `grants_added or grants_removed` to `GRANTS_UPDATED`. A removal-only diff takes
that path even though removals are never actioned.
**Fix direction:** when only `grants_removed` is non-empty, classify the object UNCHANGED (the removal is
still surfaced on the Grants sheet); unit test a revoke-only diff → `Skipped (unchanged)`.

## 12. A "copy volumes only" run (all create_*/apply_* off) records grants + governance as done — M

**Repro (local engine simulation, 2026-11-20, v1.2.2 @ `9228af4`; `/tmp/ai27_sim/sim.py`):**
- Run 1: every `create_*` and `apply_*` toggle off (the "only copy volume data" configuration). Zero
  statements are issued, which is correct.
- But state stores each object with `last_action=skipped_create_disabled` plus the **current**
  `governance_hash` and `grants_json`. `_facet_statuses` even reads `governance_status=applied` when the
  object has tags, although `apply_tags` was off.
- Run 2: `apply_*` on, create toggles still off (BYO, where catalogs/schemas are always forced off). The
  table reads `UNCHANGED` and **no GRANT is issued**. Its grants, tags and masks are never applied on any
  later run.
- With `create_tables` ON in run 2, the v1.2.2 toggle-flip re-attempt applies the grants. So the
  permanent loss hits catalogs and schemas under BYO, and any type whose create toggle stays off.

**Cause:** `package_import.py:1577` stamps `governance_hash`/`grants_json` on every result before the
toggle branches. The planner only replays a facet whose status is `failed`, not one that was `skipped`
because its apply toggle was off.
**Also masks prior failures:** an object recorded `failed` by the earlier full run is re-attempted by the
copy-only run, lands in the create-disabled branch, and is rewritten as `skipped_create_disabled` with
`governance_status=applied` (simulated, `/tmp/ai27_sim/sim2.py`). It drops off Outstanding and is
never retried.
**Harmless when:** the copy-only run comes AFTER a full migration (governance already on target, so the
stored hashes are true) AND that migration left nothing on Outstanding.
**Mitigation available today:** set `filter_tables` to a value that matches no table (e.g.
`__volumes_only__`). Inventory/export then skip every table and view, so the copy-only run never
touches their state rows: no result row, and `in_run_scope` prevents a false "Deleted in source".
**Verified live** 2026-11-20, run `153531417351955`:
- Only the 2 new files were copied; the target is otherwise unchanged.
- Every TABLE/VIEW/ABAC state row is byte-identical, and the failed ABAC policy stayed `failed`, because
  the ABAC phase is gated by `create_abac_policies`.
- Export took 6.4 min vs 29.3 min. See also #13.
**Fix direction:**
- When `apply_grants` is off, persist the prior `grants_json` (or none) instead of the current one, and
  likewise `governance_hash` when `apply_tags`/`apply_masks_row_filters` are off.
- Or: the planner treats a prior `grants_status=skipped` with note "apply_grants off" as replay-needed,
  when apply is now on.
- `_facet_statuses`: never read `applied` for a facet whose toggle was off.
- Unit test: copy-only run, then a full BYO run, issues the grants.

**Workaround:** run copy-only only after the full migration, or run the full migration with
`copy_volume_data=true` in one go. If a copy-only run was already the first run, delete its state rows
(`DELETE FROM uc_sync_state WHERE run_id = '<copy-only run>'`) before the full run.

## 13. Copy-only run with a no-match `filter_tables` reports governed tags as "Deleted in source" — L

**Run:** job `778661300035503`, run `153531417351955`. Every toggle off, `copy_volume_data=true`,
`filter_tables=__volumes_only__`.
**Result:**
- The Summary lists 5 "Deleted in source — review" rows: the governed tags `ai27_uc_pii`,
  `ai27_uc_row_access`, `abac_colmask_ai27_ucsync_bsfd_double`, `abac_colmask_ai27_ucsync_spdi` and
  `abac_rls_ai27_ucsync_region`.
- They exist on source. They were not inventoried because only the filtered-out tables' columns use them.
- Nothing is dropped and their state rows are unchanged. This is report noise only.

**Cause:** `DeltaPlan`'s `in_run_scope` guard protects only table-like types from SOURCE_ABSENT. Governed
tags are discovered through the objects that use them.
**Fix direction:** when `filter_tables` is set, don't flag GOVERNED_TAG (or any usage-discovered type)
absent from source; unit test.

---

## Testing strategy

Every change is validated by a human-style, CLI-driven E2E pass on real Databricks. The QA agent's operating
contract (allowed actions, stop-and-ask rules) is **`plans/qa-testing-agent.md`**. Mode under test: **direct,
BYO** (catalogs + schemas pre-created on source and target).

### Fixtures — one cumulative bed
Use and extend the single-catalog bed `ai27_ucsync_testcatalog` (`python3 fixtures/recreate.py testcat`, stages
`tc_*`). Never fork a new fixture set. Add each new feature's fixture as a `tc_*` stage, and **always keep the
governance-failure cases** (`tc_negative`).

What the bed contains:
- plain tables;
- classic masks, row filters, and both together;
- tag-driven catalog-level ABAC;
- one of every object type (managed + external table, view, MV, streaming table, event log, Lakebase
  synced table, monitor, function, managed + external volume, metric view, model, vector index);
- the 120-table parallelism bed (`tc_parallel`);
- the v1.2.0/1.2.1 bug beds (`tc_bugfix`, `tc_abac_proc`);
- the v1.2.2 URL-hostile volume paths (`tc_volume_paths`).

The target BYO shell (`tc_target_byo`) creates the renamed target catalog `{{TESTCAT_TGT_CATALOG}}` (set in
`config.env`) plus the ops catalog.

### Scenario coverage
DDL × governance outcome rows (what the per-facet state must record):

| # | DDL facet | Governance facet | Expected |
|---|---|---|---|
| 1 | fresh | applied | created |
| 2 | fresh | fails | dropped, `ddl_status=failed`, next run re-creates |
| 4 | pre-existing | applied | adopted / updated |
| 5 | pre-existing | fails | flagged, never dropped |
| 6 | create-disabled | applied | — |
| 7 | create-disabled | fails | — |
| 9 | DDL fails | — | governance not attempted |
| 10 | create-disabled, object truly absent | — | — |

Plus:
- dry run then live;
- unchanged incremental re-run;
- seeded incremental (grant added + new volume file);
- create-toggle off → on;
- catalog rename + volume copy;
- source SPN without `system` access (tags/ABAC empty, grants present).

### Per-run validation
For each report sheet, cross-check against the live target via CLI/API: objects exist with the expected
properties; grants, tags, masks and ABAC match source; Outstanding lists exactly the expected failures;
`uc_sync_state` carries the expected per-facet `*_status` and `last_action`; `uc_sync_volume_files`
matches the files on target.
**Note:** the ops tables are owned by the run-as SPN, so query them as the SPN.

### Bugs identified (populated during testing)
_(The QA agent appends confirmed bugs here, one subsection each with repro + evidence. After triage, promote
each one to a numbered item above.)_
