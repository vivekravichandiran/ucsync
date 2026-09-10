# UC Sync — Bugfix & Production-Readiness Tracker

Living backlog of defects, robustness gaps, and feature work found during live
validation of the governance-migration utility. Grows as we test more. Each item:
status, severity, evidence, and proposed fix.

Legend: **OPEN** / **IN PROGRESS** / **FIXED** / **WONTFIX** · sev = data-safety >
correctness > robustness > cosmetic.

---

## Fixed (shipped on `feature/abac_refactor`)

- ✅ **BUG #1 — pre-existing table dropped fail-closed on incremental** (data-safety).
  `CREATE TABLE IF NOT EXISTS` silently no-ops on a pre-existing table, so it was
  wrongly recorded droppable and dropped when a later governance step failed. Fix =
  pre-create existence probe in `package_import._import_ddl_file` (records
  `SKIP_EXISTING`, never adds to `_created_tables`). Commit `30013bc`. Live-verified
  incremental run `445348744393566` + force_full `629050016875215`
  (pre-existing `employees` survives; genuinely-fresh `employees_secure` still drops).
- ✅ **BUG #2 — CHANGED function not replaced** (correctness). `sql_ddl` emitted
  `CREATE FUNCTION IF NOT EXISTS` → no-op on re-run. Fix = emit `CREATE OR REPLACE
  FUNCTION` in both builders. Commit `30013bc`. Verified: target `mask_phone` updated.
- ✅ **Delta sheet mislabels report-only assets** (cosmetic). Report-only types
  (streaming tables, Tier-A AI assets, MVs unless `migrate_materialized_views`) showed
  `CREATED_NEW`. Fix = `delta_rows()` relabels to `REPORT_ONLY`. Commit `729ca55`.
  Verified on force_full: `fraud_scoring` (MODEL) + `emp_mv` (MV) show `REPORT_ONLY`.

Full RCA for the two bugs: `plans/bugfix-preexisting-table-dropped-on-incremental.md`.

---

## Open

### 1. force_full re-creates existing masked external tables → spurious failure — **OPEN** (robustness)
**Found:** force_full run `629050016875215` (2026-09-10). `ai27_uc_finance.ap.invoices_ext`
and `gl.accounts_ext` failed with `EXTERNAL_CREATE_PERMISSION_DENIED`; the real
exception is `PERMISSION_DENIED: Path-based access to table … with row filter or column
mask not supported`. Both are **masked external tables** (invoices_ext: classic mask on
`vendor`; accounts_ext: schema-level ABAC policy `ai27_uc_fin_mask_acct`).

**Root cause:** UC blocks path-based `CREATE EXTERNAL TABLE` against an
**already-existing masked** external table. It works on first create; force_full
**re-creates** every object, so it fails on the second pass. Only surfaces on force_full
(incremental skips these as `UNCHANGED`). **Not data-loss** — both tables remain on
target. **Not a regression** — the create was always attempted on force_full.

**Proposed fix (a):** skip the `CREATE` when the BUG #1 pre-create probe shows the
droppable table already exists (the probe already computes `pre_exists`). Tables are
never re-altered anyway (`CHANGED` is report-only), so skipping the create of an existing
table loses nothing and makes force_full idempotent-safe; governance/grants still apply,
and fail-closed for genuinely-fresh tables is unaffected.

**Proposed fix (b, minor):** `classify_external_create_failure` mislabels the
path-based-mask error as `EXTERNAL_CREATE_PERMISSION_DENIED` with a misleading "lacks
CREATE EXTERNAL TABLE" hint — add a distinct code/hint for the masked-path case.

### 2. Transient cold-warehouse SHOW CREATE failures → partial bundle silently flows to import — **OPEN** (robustness, HIGH for prod)
**Found:** real-source e2e run `824507703579050` (2026-09-10). Export reported
`export_status: ERROR 9 / SUCCESS 58`. 9 of 14 tables failed `SHOW CREATE` on the
**cold source SQL warehouse** with a bare `statement FAILED:` (no underlying message).
Because the design is hard-fail / no-synth (never drop masks), those 9 tables were
**excluded from the bundle** → import couldn't create them → cascade: 4 analytics views
+ `v_staff` + `order_summary` failed `TABLE_OR_VIEW_NOT_FOUND` on their missing base
tables. Confirmed transient: `SHOW CREATE` for all 9 succeeds once the warehouse is warm.

**Sub-issues:**
- (a) **Export task returns SUCCESS despite 9 ERRORs**, and the import then runs on a
  **partial bundle** — producing a confusing half-migrated target. For prod, either fail
  the export loudly when any inventoried object can't be captured, or have the import
  refuse/flag a bundle that is missing inventoried objects (don't half-apply).
- (b) **Retry/robustness:** SHOW CREATE should survive a cold serverless warehouse —
  pre-warm the warehouse (issue a `SELECT 1` / wait for RUNNING before the capture loop),
  and/or increase retry count/backoff. Operational mitigation today: pre-warm the source
  warehouse (or set a longer auto-stop) before running.
- (c) **Error surfacing:** the failure message is empty (`statement FAILED:`), so the
  real cause is invisible. Capture and surface the underlying statement error/state.

**Not a data-safety bug** and **not the import** — the import faithfully applied the
(partial) bundle. Operational fix for now: warm the source warehouse, re-run.

### 3. Test-fixture pollution on source `hr.employees` — **cleanup item**
During BUG #1 live validation I seeded a permanent failing negative on source
`ai27_uc_gov_src.hr.employees` (`ai27_uc_neg_emp` COLUMN MASK via un-migrated
`ai_27.sec.mask_ext`) + tagged `employees.email`=EMAIL. On a full seed `employees` is
now created fresh → the negative fails → `employees` is dropped fail-closed → the 4
analytics views that depend on it also fail. This is **my test residue**, not a product
issue; remove the `ai27_uc_neg_emp` policy (and optionally the email tag) from source to
stop `employees` failing on every run. (The other negatives — `employees_secure`/
`ai27_uc_neg2`, `abac_ext`/`ai27_uc_mask_ext_neg`, `ext_masked` — are the intended
by-design fail-closed fixtures.)

---

## Features to discuss (placeholder — user has 2 in mind)

- TBD (feature 1)
- TBD (feature 2)

---

## Notes / environment

- Target test env (2026-09-10): catalogs `ai27_uc_gov_src` / `ai27_uc_finance` /
  `ai27_uc_sales`; ops `ai27_ucsync_ops.ops`; e2e live job `365623687820213`; git folder
  `286371791957190` on branch `feature/abac_refactor`. Warehouses: source
  `d17c387e12f4f010`, target `84bef830cd844b1d`. Permanent fail-closed negative fixtures
  (reference un-migrated `ai_27`) make any full/e2e run RED **by design**.
