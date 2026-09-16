# Report & Metadata Cleanup Plan (2026-09-14)

Driven by live findings on run `238768419583181`. Two buckets: (A) governance
propagation bugs + their report messages; (B) report-structure cleanup; (C) metadata
tables. Each item states the current behavior, the confirmed root cause (file:line),
and the proposed fix.

North star: prod-ready governance migration. A governance change that does not land
on target must NEVER read as success.

---

## STATUS (2026-09-15) — ✅ CERTIFIED PROD-READY (unit-tested 316 green + 4 live E2E runs)

All six steps implemented + **live-certified** on `feature/abac_refactor` @ `0851cc6`
(job `917270847282133`, target_ws). `PYTHONPATH=src python3 -m pytest -q` = **316 passed**.

**Live validation (Runs 1–4):** A1/A2 masks/filters apply on pre-existing columns
(`dept_lookup` masked + row-filtered on target); A3/A4 read "Skipped — column deleted/type
changed" and target is non-destructive; Outstanding sheet lists the 2 ai_27 ABAC negatives;
no Issues/Delta sheets; Governed Tags + Tags Applied sheets; report-only → Manual step;
SOURCE_ABSENT (`cert_new`) → "Deleted in source" on the Tables sheet; `uc_sync_volume_files`
gained status/message/created_at/updated_at via live ALTER; `last_action` fully populated
(backfill self-heals); `first_seen`/`connectivity_mode`/`failure_category` populated. Import
RED-with-only-the-2-negatives is the CORRECT certified outcome (fail-closed by design).

**4 upgrade-path bugs found + fixed during live testing:** backfill matched "name type"
strings not names (`12d00ed`); backfill not self-healing (`ec3f005`); `first_seen` NULL for
legacy rows → coalesce MERGE (`690b7ae`); B3 label lost on incremental unchanged objects
(`690b7ae`). **B3 label finalized as "Skipped (create disabled)" — dropped "BYO" as
customer-confusing (`0851cc6`)**; "Adopted (pre-existing)" stays for create-ON-but-exists.

Summary of what landed:

- **NEW `src/uc_sync/vocab.py`** — single source of truth for the status vocabulary
  (wsmig `LAST_ACTIONS`), `status_key()`, `STATUS_STYLE`, `SUMMARY_ORDER`,
  `OUTSTANDING_ACTIONS`, `CLEAN_ACTIONS` + `is_clean_action()` (legacy-aware).
- **Step 1 (vocab unify + rename):** `uc_sync_state.last_sync_status` → **`last_action`**
  storing wsmig vocab verbatim; `report.py` + `sync_state.py` + `delta.py` all use
  `vocab`. In-place ADD COLUMNS + backfill on upgrade. Bug-#18 gate → `is_clean_action`.
- **Step 2 (A1/A2 correctness):** `PackageImportEngine._apply_policies` (Phase 1c) applies
  the exported `policies/` dir (classic SET MASK / SET ROW FILTER) to CHANGED pre-existing
  tables, idempotent + **fail-closed** (never a false "Updated"). A3/A4: a CHANGED table
  whose only diff is a column drop / type change reads `Skipped — <reason>` (CHANGED_SKIPPED).
- **Step 3 (vocab assignment + structure):** removed **Issues** (B1) and **Delta** (B2)
  sheets; SOURCE_ABSENT → `Deleted in source` on the object's own per-type sheet + Summary
  "Deleted in source — review"; report-only types → `manual` (B4); BYO create-disabled →
  `skipped_create_disabled` "Skipped (create disabled — BYO)" (B3); removed grants → Grants
  sheet note; UNCHANGED tally → Summary stat.
- **Step 4:** **Outstanding** sheet (Part E) from `SyncStateService.outstanding_rows()`
  (cumulative `last_action=failed`), wired in `03_Import.py` after the state upsert;
  **Tags Applied** rename + gap text + reworded rolled-back (B6); first-class **Governed
  Tags** sheet (B5).
- **Step 5:** state parity columns `first_seen` (preserved via MERGE UPDATE-set-minus),
  `connectivity_mode`, `failure_category`, `last_error_raw` (Part D); `uc_sync_volume_files`
  gains `status` / `message` / `created_at` / `updated_at`, records failures + too-large,
  never advances the watermark on a non-COPIED outcome (C1); hash column COMMENTs + docs (C2).
- **Step 6 (visual, last):** frozen header row on every data sheet. Heavier dashboard
  polish intentionally deferred.
- **Tests:** new `tests/test_vocab.py`, `tests/test_policy_reapply.py`; updated
  `test_report.py`, `test_report_wsmig_feat5.py`, `test_incremental_delta.py`,
  `test_migrate_import_state.py`, `test_failclosed_governance.py`, `test_volume_copy_feat4.py`.

Original plan detail below (unchanged) for reference.

---

## Part A — Governance propagation bugs (behavior + report message)

Facts (confirmed in code):
- Masks/row filters ride **inline** in `SHOW CREATE TABLE` → they land in `ddl_hash`
  (`fingerprints.ddl_fingerprint`), NOT `governance_hash` (tags only).
- The active engine `PackageImportEngine` (`package_import.py`, used by `notebooks/03_Import.py`)
  applies masks/filters **only** two ways: inline in `CREATE TABLE` (Phase 1, fresh
  tables) and in Phase 1b `_replay_schema_evolution` **for newly-added columns only**.
- The export writes a `policies/` dir of standalone `ALTER … SET MASK` / `SET ROW FILTER`
  statements (`export.py:190`), but `PackageImportEngine` **never applies it**
  (`_apply_governance_dir` is only ever called with `tags`, `abac`, `tags`). The older
  `import_engine.py` had `_apply_policies`; the active engine dropped it.

### A1 (bug 3a) — column mask on a pre-existing column is silently dropped
- **Now:** delta = `CHANGED`; table is `SKIP_EXISTING`; mask never applied; report reads
  `Updated` / `column_mask=yes`. Live proof: `cert_upd.note` unmasked on target.
- **Fix (behavior):** add a **policies phase** to `PackageImportEngine.run()` that applies
  the exported `policies/` dir (`SET MASK` / `SET ROW FILTER`) on the main executor,
  idempotently, for pre-existing tables too. Gate it to run when the table's `ddl_hash`
  changed (delta action `CHANGED`) OR always (SET MASK is idempotent). Fail-closed: if a
  mask/filter can't be applied, mark the table `FAILURE`/`PROTECTION_FAILED` (drop-sweep
  eligible), never a silent success.
- **Fix (message):** the table reads `Updated` ONLY when the mask actually applied; if the
  apply failed it reads `FAILED` in the Tables/Issues sheets. Add a per-mask row status in
  the "Column Masks & Row Filters" sheet (APPLIED / FAILED / rolled back), like tags/grants.
- **Removal case (sub-item):** a mask removed on source also changes `ddl_hash` → `CHANGED`,
  but `policies/` has only SET statements. Detect target-has-mask & source-doesn't → emit
  `ALTER … DROP MASK` / `DROP ROW FILTER`. (Confirm we want removal in scope.)

### A2 (bug 5) — row filter on a pre-existing table is silently dropped
- Same root cause and same fix as A1 (the policies phase applies row filters too; the
  removal sub-item covers `DROP ROW FILTER`).

### A3 (item 2) — column dropped on source
- **Now:** delta = `CHANGED`; Phase 1b adds nothing; target keeps the column; report reads
  `Updated` (misleading — nothing changed). By-design non-destructive.
- **Fix:** keep the non-destructive behavior, fix the label. When a `CHANGED` table had
  **no actionable change applied** and the diff is a column drop, render
  **`Skipped — column deleted on source; not dropped on target`** (a Skipped variant with a
  comment), not `Updated`. Requires distinguishing "CHANGED + something applied" from
  "CHANGED + nothing applied".

### A4 (item 4-columns) — column data type changed on source
- **Now:** delta = `CHANGED`; not actioned (rename/type out of scope; ALTER TYPE largely
  unsupported in Delta); report reads `Updated`.
- **Fix:** same as A3 — render **`Skipped — column type changed on source; not altered on
  target (out of scope)`**.

> A3/A4 both need the renderer to know a `CHANGED` table produced **no applied sub-op**.
> Plan: have `_replay_schema_evolution` (and the new policies phase) emit an explicit
> per-object "no-op reason" (column_dropped / type_changed / unsupported) that the report
> maps to the Skipped-with-comment label instead of the blanket `updated` for `CHANGED`.

---

## Part B — Report structure cleanup (`report.py`)

### B1 (item 1) — REMOVE the Issues sheet entirely
- **Now:** a separate "Issues" sheet (`report.py:670`) lists non-success import ops, and it
  leaks `UNCHANGED` rows ("unchanged since last run (incremental: skipped)") because it only
  excludes `SUCCESS`/`SKIP_EXISTING`/`PENDING`.
- **Fix (operator decision 2026-09-14):** **delete the Issues sheet.** It is redundant with
  the two wsmig failure surfaces: current-run failures live in the **Summary "Failures (N)"**
  section (+ "Manual steps" + "Deleted in source" sections), and cumulative failures live in
  the new **Outstanding** sheet (Part E). No third sheet.

### B2 (item 2) — remove the Delta sheet; fold changes into per-type sheets
- **Now:** `report.py:694` builds a separate "Delta" sheet (the only place `SOURCE_ABSENT`
  and `GOVERNANCE_FAILED` roll-ups appear together).
- **Fix:** delete the Delta sheet. Re-home its unique content:
  - `SOURCE_ABSENT` → a row in the object's **own per-type sheet** with status
    **`Deleted in source`** (`_STATUS_STYLE["deleted_in_source"]` already exists) — this
    ALSO fixes the SOURCE_ABSENT visibility gap (it was Delta-only).
  - `GOVERNANCE_FAILED` → already shown in Issues + as the object's own `FAILED` row.
  - grant/gov/ddl changes → already reflected as `Updated` in per-type sheets.
  - the UNCHANGED count line → move to the Summary sheet as a stat.
- Requires: pass the source-absent objects into the per-type rendering (they aren't in the
  current inventory), rendered as a trailing section per sheet.

### B3 (item 3) — mode-skipped Catalogs/Schemas/SC/EL mislabeled "Skipped (unchanged)"
- **Now:** in BYO mode (create toggles OFF) these read `Skipped (unchanged)`, conflating
  "mode says don't create" with "nothing changed".
- **Fix:** when the relevant create toggle is OFF, render **`Skipped (create disabled — BYO)`**
  (or `Adopted (pre-existing)`); only when create is ON and the object was unchanged render
  `Skipped (unchanged)`. Pass the toggle state into the renderer per object type.

### B4 (item 4) — out-of-scope / report-only objects vocabulary
- **Now:** Lakebase/Pipeline/Models/Monitors etc. all read `Skipped (no target object)`.
- **Fix:** give report-only-by-type assets a distinct, accurate label, e.g.
  **`Report only (not migrated)`** and keep `Skipped (no target object)` for
  in-scope-type objects that genuinely had no target. **OPEN Q:** confirm the exact wsmig
  term to mirror (see Open Questions).

### B5 (item 5) — "Other Objects" sheet
- **Now:** catch-all for any type not in `_TYPE_SHEETS`/`_INVENTORY_ONLY`/ABAC. Need to
  confirm what actually lands here (suspected: `GOVERNED_TAG`).
- **Fix:** if it is only governed tags, add a first-class **"Governed Tags"** sheet and
  drop the generic "Other Objects" (or keep Other Objects strictly as a safety net that
  only appears when a truly-unmodeled type shows up).

### B6 (item 6) — Tags sheet naming + gaps + ROLLED BACK
- **Now:** sheet is "Tags"; per-row `import_status` can be blank (no tag op recorded) or
  `ROLLED BACK (object dropped)` when the owning table was dropped fail-closed.
- **Fix:** rename to **"Tags Applied"** (or "Governed Tags Applied"). Replace blank gaps
  with an explicit `— (no tag op this run)`; keep ROLLED BACK but reword to
  `Rolled back (table dropped fail-closed)` and ensure it only shows for genuine
  casualties (already the case via `_object_rolled_back`).

---

## Part C — Metadata tables

### C1 (item 1) — `uc_sync_volume_files` needs created_at / updated_at / status
- **Now (`volume_copy.py`):** schema is `(volume, path, source_mtime, copied_at)`. Only
  **successfully copied** files are recorded; failures are absent; no created/updated split;
  no status. Two persistent impls (Spark control + `WarehouseVolumeCopyControl`) + an
  in-memory one.
- **Fix:** new schema `(volume, path, source_mtime, status, message, created_at, updated_at)`:
  - `created_at` set on first insert, preserved on update.
  - `updated_at` set every merge (== created_at on first write).
  - `status` in {`COPIED`, `FAILED`, `SKIPPED_UNCHANGED`}; record **failures** too.
  - update both persistent implementations + the `IF NOT EXISTS` upgrade path (ALTER ADD
    COLUMNS for existing tables) so a pre-existing control table gains the new columns.

### C2 (item 2) — document the three hashes
- Add column comments on `uc_sync_state` (`source_definition_hash` / `ddl_hash` /
  `governance_hash`) capturing the definitions above, and a short note in the docs. (The
  explanation itself already delivered to the user.)

---

## Testing
- Unit: extend `tests/test_report.py` (single `last_action` vocab, **no Issues sheet**, no
  Delta sheet, source-absent in per-type sheet, mode-skip label, Tags Applied, Outstanding
  sheet from state), `tests/test_incremental_delta.py` (mask/filter on existing column →
  applied), volume-copy control schema tests, state-parity column tests.
- **Live (REQUIRED — full QA-tester protocol):** validate on the real `source_ws`/`target_ws`
  per the **"Tester role (system prompt)"** in `plans/bugfix-and-prod-readiness.md`
  (§ lines ~200-218): create the git folder → run `notebooks/00_Install_Jobs.py` with widget
  values → run the created job → monitor → **verify the target with real API calls and compare
  against the generated report** → seed incremental changes on source → re-run → re-validate.
  Only setup + source seeding are allowed as direct actions; everything else is observed. Stop
  and ask if blocked. Direct mode + BYO mode, external tables + external volumes included.
- Targeted seeds for THIS work (add to the run-2 incremental seed set): a **mask on an existing
  column**, a **row filter on an existing table**, a **column drop**, a **column type change**,
  a **source-object deletion** (SOURCE_ABSENT), and a **prior-run failure left unfixed** (to
  populate the Outstanding sheet). Verify on target: masks/filters actually land (or read
  FAILED, never a false "Updated"); drops/type-changes read Skipped-with-comment; state
  `last_action` matches the report label 1:1; no Issues/Delta sheets; Outstanding sheet lists
  the cumulative failures; `uc_sync_volume_files` has `status`/`created_at`/`updated_at`.

---

## CONFIRMED DECISIONS (2026-09-14) — supersede the open questions

Goal restated by the operator: **functional parity with wsmig, not visual.** Same
status **vocabulary** and the **same information** in BOTH the report and the state
table. Visual polish is LAST priority. wsmig already does vocab+info parity well
(verified): one closed vocab stored in state `last_action` and rendered verbatim; a rich
state table; a two-surface failure model. All gaps are on our side.

1. **Vocab (Q1) — resolved from the wsmig source:**
   - `skipped_no_object` ("Skipped (no target object)") is reserved for **ACLs/grants whose
     target object isn't present** — NOT for report-only asset TYPES.
   - Apps / Lakebase and other never-migrated assets are **inventory-only**, action
     **`manual`** ("Manual step") in wsmig. Re-map our report-only assets (Lakebase /
     pipeline tables / models / monitors) off `skipped_no_object` accordingly.
   - The label SET already matches; the **assignment logic** must be corrected.
2. **Mask/filter removal (Q2) — REPORT ONLY, never remove.** Treat a removed mask/row
   filter like any delete (same as SOURCE_ABSENT): report it, never emit
   `DROP MASK`/`DROP ROW FILTER` on target.
3. **B3 label (Q3) — use a Skipped variant:** `Skipped (create disabled — BYO)`.
4. **Constant naming + vocab (state table):** rename `uc_sync_state.last_sync_status`
   → **`last_action`** (in-place `ADD COLUMNS` upgrade + backfill), and store the **wsmig
   vocab values directly** (drop the separate `_sync_status_for` vocabulary; reuse the same
   mapping the report uses). One vocab in state AND report → kills the ADOPTED-vs-Updated
   mismatch. The #18 re-attempt gate switches to the new vocab: re-attempt when
   `last_action ∈ {failed, manual, …}` (the non-clean / outstanding set), skip when
   `∈ {created, updated, adopted, skipped, created_with_warning}`.
5. **Skip the identity-map table** — UC principals are account-level, resolve by name.
6. **HTML** inventory app: OUT of scope for now. Visual dashboard polish: LAST.

## Part D — State-table parity with wsmig `wsmig_migration_state`

Add the columns wsmig has that we lack (all `ADD COLUMNS`, backfilled):
- `first_seen` (when the object was FIRST migrated; today we only track last/updated).
- `connectivity_mode` (direct vs bundle/BYO — we keep it in audit, not state; also feeds B3).
- `failure_category` (coarse taxonomy: NOT_SUPPORTED / DEPENDENCY_UNRESOLVED / API_ERROR / …
  vs our specific `error_code`).
- `last_error_raw` (full untruncated error; today we truncate into `error_message`).
- Align `last_action` per decision #4.
- KEEP our richer bits (three fingerprints, grants_json, ddl/grants paths).
- Explicitly NOT adding: `target_object_id` (UC updates are by name), identity-map table.

## Part E — Failures model (two surfaces, like wsmig)

- **Summary "Failures (N)"** = CURRENT-run failures (from this run's results).
- **NEW "Outstanding" sheet** = CUMULATIVE failures from `uc_sync_state`
  (`last_action = failed`) across ALL runs, independent of this run's scope. Requires
  passing the loaded state baseline into `build_report` (the notebook already loads it for
  delta). Failures-only, matching wsmig's `OUTSTANDING_ACTIONS`.
- Note: our #18 re-attempt means outstanding failures also reappear in the current run;
  the state-sourced Outstanding sheet is still the authoritative "all still-broken" view.

## Revised implementation order (functional parity first)
1. Unify vocabulary + rename `last_sync_status`→`last_action` (Parts D#4).
2. Fix correctness 3a/5 (A1/A2) so masks/filters apply or fail-closed (never false "Updated").
3. Fix vocab assignment: SOURCE_ABSENT→`deleted_in_source` in per-type sheets; drop/type
   change→Skipped-with-comment (A3/A4); report-only types→`manual`/inventory (B4);
   **remove Issues sheet (B1)**; mode-skip label (B3); remove Delta sheet (B2).
4. Add the Outstanding sheet (Part E) + Governed Tags / Tags Applied naming (B5/B6).
5. State-table parity columns (Part D) + metadata `uc_sync_volume_files` (Part C1).
6. Visual polish (Summary dashboard, sections, styling) — LAST.
