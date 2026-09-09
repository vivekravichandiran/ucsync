# Bugfix — pre-existing table dropped fail-closed on an incremental run

**Severity:** High (data-safety — violates the Task-10 guarantee that a pre-existing,
possibly data-bearing table is **never dropped**).
**Found:** Stage-3 incremental validation, run `475610304623796` (2026-09-09).
**Status:** ✅ **FIXED 2026-09-09** — pre-create existence probe added to
`package_import._import_ddl_file` (droppable table recorded `SKIP_EXISTING` when it
pre-exists, so it is never added to `_created_tables`); regression test
`test_failclosed_preexisting_table_via_silent_if_not_exists_not_dropped` added
(248 green). Awaiting live re-validation on an incremental run.

## Symptom
On the incremental run, the pre-existing table `ai27_uc_gov_src.hr.employees_secure`
(created + populated in the Stage-2 run) was **dropped** on the target
(`TABLE_OR_VIEW_NOT_FOUND`). The report shows it `FAILURE / DROP_PROTECTION_FAILED /
PROTECTION_FAILED` — "table dropped (fail-closed)". Blast radius = exactly the one
table that had a **failing** governance op (the seeded negative policy
`ai27_uc_neg2`, which references the un-migrated `ai_27.sec.mask_ext`). All other
CHANGED tables (ledger, customers, orders, employees) survived.

Task 10 requires: a **new** table shell that fails governance is dropped; a
**pre-existing** table that fails governance is **flagged, never dropped**. Here a
pre-existing table was dropped → guarantee broken.

## Root cause
`PackageImportEngine._import_ddl_file` records a table in `self._created_tables`
(the "created THIS run → droppable" set) whenever the create result action is
`CREATE_OR_SKIP`. The `CREATE_OR_SKIP` vs `SKIP_EXISTING` distinction is derived from
whether a CREATE statement raised an **"already exists"** error (`skipped_existing`).

But every CREATE is normalized to `CREATE TABLE IF NOT EXISTS …`, and **`IF NOT
EXISTS` succeeds silently (no error) on a pre-existing table**. So on a re-run where
the table already exists on target:
- no "already exists" error is raised → `skipped_existing` stays `False`
- → `result.action = "CREATE_OR_SKIP"` (looks freshly created)
- → the pre-existing table is wrongly added to `_created_tables` (droppable).

When a later governance step fails on that table (the new ABAC policy), the
fail-closed drop sweep (`_drop_failed_tables` via `_abac_matched_tables`) finds it in
`_created_tables` and **drops it**.

Why it only bit now: it requires a **pre-existing** table (on target) to have a
**failing** governance op on a **re-run**. Full/first runs create tables genuinely
fresh (correct to drop), and unchanged tables are skipped in incremental (never
reach the create). The Stage-3 negative (a new failing policy on a pre-existing
table) is the first case to hit all three conditions. `employees_secure` also read
as `CHANGED` (its `ddl_hash` shifted when the ABAC policy attached to its tagged
`email` column), so it was not delta-skipped and went through the create path.

## Fix (recommended: pre-create existence probe)
A table is "fresh / droppable" **only if it was absent on target immediately before
this run created it** — not merely because its `CREATE IF NOT EXISTS` returned
without error.

In `_import_ddl_file`, before executing the CREATE for a droppable table type
(`TABLE`/`EXTERNAL_TABLE`), probe existence:

```python
pre_exists = self._object_exists(object_type, target_full_name, executor=executor)
# ... run the CREATE statements ...
# record as droppable ONLY when it did NOT pre-exist (we truly created it fresh):
if (object_type in _DROPPABLE_TABLE_TYPES
        and result.status == "SUCCESS"
        and result.action == "CREATE_OR_SKIP"
        and not pre_exists):
    self._created_tables[target_full_name] = result
```
When `pre_exists` is true, set `result.action = "SKIP_EXISTING"` so the report/audit
reflect reality, and the table is never added to `_created_tables` → the fail-closed
drop sweep can never drop it. Its governance failure still routes through
`_mark_ungoverned_objects` → `FAILURE`/`GOVERNANCE_FAILED` (flagged, **not dropped**),
and the job still hard-fails (Task 10). This is ground-truth and fixes both full and
incremental modes.

*Cost:* one `DESCRIBE` per droppable table before create. Acceptable for a
data-safety guarantee; can be limited to droppable types only.

*Alternative / belt-and-suspenders:* also gate on the delta plan —
`_created_tables` only when `delta_plan.action(name) == CREATED_NEW` — so a table
present in the baseline is never considered fresh. Combine with the probe for full
(no-baseline) runs.

## Tests to add
1. Unit (`tests/test_failclosed_governance.py`): a **pre-existing** droppable table
   (create probe returns exists) whose governance later fails → status `FAILURE`/
   `GOVERNANCE_FAILED`, **no `DROP TABLE` issued**, table still present. (Extends the
   existing `test_failclosed_preexisting_table_marked_failed_not_dropped`, which used
   an explicit "already exists" error and so didn't catch the `IF NOT EXISTS` path.)
2. Unit: a genuinely fresh table (probe returns absent) whose governance fails → is
   dropped (regression — fresh shells still fail-closed).
3. Re-run Stage-3 negative live: the pre-existing `employees_secure` with a failing
   new policy → flagged `GOVERNANCE_FAILED`, **not dropped**, job red.

## Secondary (minor, reporting only — separate)
On the Delta sheet, report-only types (materialized views, streaming tables,
registered models) show `CREATED_NEW` (the fingerprint-based delta-plan action)
instead of `REPORT_ONLY`. The engine correctly does **not** create them (verified:
`emp_mv` and the `fraud_scoring` model are absent on target), but the Delta sheet is
misleading. Fix: derive the Delta action for these from the per-object result's
`delta_action` (`REPORT_ONLY`) rather than the raw plan action.

---

# Bug #2 — CHANGED function not replaced on target (found same run, via API verify)

**Severity:** Medium (correctness — an incremental `REPLACED` function silently no-ops;
the Delta sheet says REPLACED but the target keeps the OLD body). **Not a data-safety
issue.** Found by independent SQL verification (target `mask_phone` still had the
original `CASE WHEN is_account_group_member('admins')…` body + old comment, not the
new `'***-REDACTED'`).

**Status:** ✅ **FIXED 2026-09-09** — both `sql_ddl` function builders
(`function_ddl_from_information_schema` and the `_function_ddl_from_definition`
synthesis fallback) now emit `CREATE OR REPLACE FUNCTION`. Direct-mode
`import_engine._rewrite_ddl` already forced OR REPLACE (never affected). Test
`test_function_captured_from_information_schema` updated to assert
`CREATE OR REPLACE FUNCTION` + no `IF NOT EXISTS` (248 green). Awaiting live
re-validation on an incremental run.

## Root cause
`sql_ddl.function_ddl_from_information_schema` emits **`CREATE FUNCTION IF NOT EXISTS …`**
(verified in the bundle DDL). `package_import._normalize_create_statement` converts
views/functions to `CREATE OR REPLACE` **only when the statement does not already
contain `IF NOT EXISTS`** — that guard fires first, so the function DDL stays
`IF NOT EXISTS`. On a re-run the function already exists → `IF NOT EXISTS` no-ops →
the changed body is never applied, even though the delta plan correctly marks it
`REPLACED`. (Views are captured via `SHOW CREATE VIEW` as `CREATE VIEW` → converted to
`OR REPLACE` → they replace correctly, confirmed live.)

## Fix (recommended)
Emit functions as **`CREATE OR REPLACE FUNCTION`** in `function_ddl_from_information_schema`
(functions carry no data; replace is safe + idempotent). Then a REPLACED (or first-time)
function always applies. Add a test asserting the generated function DDL is
`CREATE OR REPLACE FUNCTION` (not `IF NOT EXISTS`), and an import test that a changed
function body is applied on a re-run.

Alternative: make `_normalize_create_statement` rewrite `CREATE FUNCTION IF NOT EXISTS`
→ `CREATE OR REPLACE FUNCTION` for the view/function family. The builder-side fix is
cleaner.
