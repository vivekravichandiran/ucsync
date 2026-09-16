# Plan: governance-aware import — ordering, fail-closed protection, reporting, ABAC

Branch: `feature/abac_refactor`. Reworked so governance is tied to the object it protects.
The change has one central idea — **reorder the import** so protection is applied as early
as possible and a table that can't be fully protected never survives.

---

## The new import order

```
storage credentials → external locations → catalogs → schemas → volumes
  → FUNCTIONS            (moved before tables)
  → TABLES               (column masks + row filters kept INLINE in the CREATE)
  → governed TAGS        (fail → drop the table)
  → ABAC policies        (run on the SQL warehouse; fail → drop the matched table[s])
  → drop sweep           (remove every table a governance step failed on)
  → VIEWS / matviews     (a view on a dropped table simply fails to create)
  → grants → ownership   (ownership last, as today)
```

Two moves make this work:

1. **Functions before tables.** Today `_TYPE_RANK` puts functions (70) after tables (60),
   so a `CREATE TABLE` can't reference its mask function yet — which is the only reason the
   inline masks are stripped out today. Moving functions ahead of tables removes that
   reason.
2. **Views after governance.** Views are created last, after tables are governed and any
   failed table is dropped, so a view built on a dropped table fails naturally — no
   dependency-tracking or cascade code needed.

---

## #A — Classic masks & row filters become part of the table DDL (atomic)

`SHOW CREATE TABLE` already emits them inline (`col … MASK sec.mask_ssn`,
`WITH ROW FILTER sec.dept_filter ON (dept)`). Today the migrate step **strips** them
(`strip_inline_policy_clauses`) and re-applies them in a late phase. With functions created
first, we **keep them inline and drop both the stripping and the separate masks phase.**

Result: if a mask/row-filter function is missing or broken, the **`CREATE TABLE` itself
fails** — the table is never created, so there is nothing to leak and no view can be built
on it. This is the fail-closed behavior, achieved atomically for classic protection.
(Collation clauses are still stripped — that fix stays.)

---

## #B — Governed tags and ABAC fail the table (drop)

Tags aren't in `SHOW CREATE` (they come from `information_schema` as `ALTER … SET TAGS`),
and ABAC is a separate securable-level policy — both are applied **after** the table
exists. For these:

- A **table** is *governed* if it has ≥1 governance feature (mask, row filter, ABAC, tag).
- If **any** governed-tag application or ABAC policy that targets a table **fails**, that
  table is **dropped** (`DROP TABLE IF EXISTS`) and its result set to **`FAILURE`**
  (`error_code=PROTECTION_FAILED`, message names the failed feature).
- This is **always on** — no toggle.
- The table is created only when it does not already exist, so a table reaching this stage
  is an **empty shell this run created** — dropping it loses no data. (Pre-existing tables
  are `SKIP_EXISTING` and never touched, so there is no revoke branch.)

**The FAILURE propagates everywhere.** The rollup mutates the table's
`PackageImportResult` in place *before* the notebook writes audit/state and builds the
report, so the drop shows as `FAILURE` in the **Tables sheet**, the **Issues sheet**, the
**`uc_sync_audit`** row, and the **`uc_sync_state`** row.

Mapping a failed governance op to its table(s): classic tag → the tagged table; ABAC
policy → the matched table(s) via the existing scope matcher (`_table_in_policy_scope`).

---

## #C — ABAC fixes (both real, from this run's failures)

1. **Context/naming bug.** ABAC full_name is `on_securable#policy:name` (governance.py:158)
   → encoded to `…gov_src.policy.<name>` → `_apply_context` runs `USE SCHEMA policy` →
   `SCHEMA_NOT_FOUND`. `CREATE POLICY` is fully qualified, so **skip `_apply_context` for
   `ABAC_POLICY`** (add it to the no-context set with CATALOG/STORAGE_CREDENTIAL/EL).
2. **Compute — ABAC requires the SQL warehouse.** A classic Spark cluster rejects
   `CREATE POLICY` at parse; a SQL warehouse accepts it. Add **`import_warehouse_id`**
   (widgets on 03 + 00). The **ABAC phase always executes on `RestSqlExecutor(import_warehouse_id)`**.
   If the bundle has any ABAC policy and `import_warehouse_id` is unset, the import **fails
   fast** (`error_code=ABAC_WAREHOUSE_REQUIRED`). No ABAC in the bundle → warehouse not
   required.

---

## #D — Counts match + status visible everywhere

- **Counts (#1 earlier):** the Summary's `import_status` tally is counted **per object**
  (the 39), with each object's status folding in its governance outcome. Governance work is
  shown in a **separate labeled block** `governance operations — APPLIED / FAILED /
  SKIPPED`. So export objects and import objects match; governance is visible but not
  conflated.
- **Status columns:** add `import_status` (+ `message`) to **Column Masks & Row Filters**,
  **Policy Matched Columns**, **ABAC Policies**, **Tags**, **Grants**.
- **Issues sheet** (right after Summary): every result whose status ∉ {SUCCESS,
  SKIP_EXISTING, PENDING}, columns `status | object_type | object | action | error_code |
  message`, FAILURE first. Built from raw results so nothing hides; dropped tables show as
  `PROTECTION_FAILED`, failed views as their own creation error.

---

## Known limitations (noted, not built)

- **ABAC stays post-table** — `CREATE POLICY … ON TABLE` needs the table, and ABAC masks by
  tag-match, so it cannot precede tables. It runs in one post-tags phase.
- **A function that reads a table** would fail when created before tables (rare — mask
  functions are scalar). If hit, that function's table fails its inline mask and is dropped
  (fail-closed). A two-pass function create is a future mitigation, not in scope.

---

## Files

| File | Change |
|---|---|
| `src/uc_sync/dependency.py` | `_TYPE_RANK`: functions before tables; keep view-like types last. |
| `src/uc_sync/rewrite.py` | stop stripping inline column-mask / row-filter clauses (keep collation strip). |
| `src/uc_sync/package_import.py` | new run() order (functions → tables(inline) → tags → ABAC → drop sweep → views → grants → ownership); drop governed table on tag/ABAC failure + set result FAILURE in place; skip `_apply_context` for `ABAC_POLICY`; ABAC phase via `RestSqlExecutor(import_warehouse_id)` with fail-fast `ABAC_WAREHOUSE_REQUIRED`; remove the separate classic-masks phase. |
| `src/uc_sync/report.py` | per-object vs governance-ops Summary split; Issues sheet; status columns on governance + grants sheets. |
| `notebooks/03_Import.py`, `notebooks/00_Install_Jobs.py` | `import_warehouse_id` widget + wire a warehouse executor for ABAC. |
| `src/uc_sync/config.py` | `import_warehouse_id`. |
| `docs/*` | new order, fail-closed drop, ABAC warehouse requirement, report changes. |

---

## Tests

1. **Order:** functions execute before tables; views execute after the governance/drop
   phases.
2. **Inline classic protection:** a table whose DDL carries `MASK`/`WITH ROW FILTER` for an
   **existing** function is created SUCCESS with the clauses intact (not stripped).
3. **Fail-closed demo (the requested negative test):** one bundle with —
   - a table `t_extern_mask` whose inline column `MASK other_cat.sec.mask_x` references a
     **function in a catalog we are not migrating** → `CREATE TABLE` raises
     (function/schema not found) → result `FAILURE`, table **not created**;
   - a table `t_bad_tag` tagged with a **governed tag absent on target** → the
     `ALTER … SET TAGS` raises (unknown tag policy) → table **dropped** (`DROP TABLE`
     issued) and result `FAILURE`/`PROTECTION_FAILED`;
   - a view `v_on_bad_tag` selecting from `t_bad_tag`, created in the post-governance view
     phase → **fails naturally** (its table is gone) → `FAILURE`.
   Asserts, via a fake executor that models a known-functions set, an allowed-tags set, and
   an existing-tables set: the two tables end `FAILURE`, `DROP TABLE t_bad_tag` was issued,
   `t_extern_mask` was never created, and the view is `FAILURE` — and these statuses are
   what the notebook would hand to audit/state (not just the report).
4. **ABAC context:** `ABAC_POLICY` import issues no `USE SCHEMA`; the catalog-level policy
   no longer hits `SCHEMA_NOT_FOUND`.
5. **ABAC warehouse enforcement:** ABAC + no `import_warehouse_id` → fail fast
   `ABAC_WAREHOUSE_REQUIRED`; with a fake warehouse executor → `CREATE POLICY` routed to it;
   no ABAC in bundle → no warehouse required.
6. **Report:** Issues sheet lists every non-success op; governance/grants sheets carry
   `import_status`; Summary object count == export object count.
7. **Regression:** full suite green.

---

## Verification (live: finance + gov_src + sales, 03 with `import_warehouse_id`)

- Order visible in logs: functions → tables → tags → ABAC → views.
- ABAC policies apply (0 ABAC failures); catalog-level policy no `SCHEMA_NOT_FOUND`.
- Summary: export objects == import objects; governance-ops block populated.
- Break one mask function at source → its table fails at `CREATE TABLE`; a view on it fails
  too — both `FAILURE` in the Tables/Views sheet, Issues sheet, and `uc_sync_audit`.

---
---

# PART 2 — post-live-test hardening (warehouse-only capture, view-on-warehouse, report fixes)

**Status: Part 1 above is implemented + committed on `feature/abac_refactor`. Part 2 is the
NEXT build — approved by the user, decisions final, do not re-litigate.** Part 2 came out of
the first live end-to-end run (existing-catalog / Mode B, run_id `696875403899687`, 3 catalogs
`ai27_uc_gov_src` / `ai27_uc_finance` / `ai27_uc_sales`) which surfaced three real problems.
Test-env identities are in memory: [[target-catalog-recreation-reference]] (target SP
`b7c3f237-7cce-4c5d-981d-78f57c0d36e9`; **target serverless warehouse `4032a3c63316a01d`** for
ABAC + views; ops `catalog_3_w0n0af.operations`; run bundles under
`/Volumes/catalog_3_w0n0af/operations/uc_refactored/run_<id>/`), [[uc-refactor-progress]]
(**source warehouse `55d6cbb90a275ed4`**, profile `source_ws`; fixture spec), and
[[source-test-catalogs-structure]]. Volume reads need the `dbfs:` prefix on this workspace.

## RCA (what the live run proved)

1. **Views over masked/row-filtered tables fail on classic Spark.** All 4 failing views
   (`analytics.emp_fn_masked`, `emp_summary`, `emp_dynamic`, `emp_secure_dynamic`) select from
   `hr.employees`, which carries inline classic masks + a row filter; the one view over an
   unmasked table succeeded. Error: `[UC_SERVER_UNCAUGHT_EXCEPTION] … INVALID_PARAMETER_VALUE`.
   **Verified: the identical `CREATE VIEW` SUCCEEDS on the serverless SQL warehouse.** So it is
   compute-specific — the import created views on the classic job cluster (`SparkSqlExecutor`).
   It surfaced now because Part 1 made masks **inline** (present at table-create) and moved
   views **last**, so views are always created over already-masked tables.

2. **Synthesized-DDL fallback silently drops masks / row filters / constraints.** `finance.accounts`
   migrated DDL header read `-- source=SYNTHESIZED` and its columns were plain (no `MASK`), while
   `employees_secure` read `-- source=SHOW_CREATE` and kept its masks. Root defect:
   `export.py::_capture_object_ddl` wraps SHOW CREATE in a blanket `try/except` and, on ANY
   failure, silently rebuilds the table from REST metadata via `sql_ddl._table_ddl_from_definition`,
   which renders columns as `name type nullable comment` — **no `MASK`, no `WITH ROW FILTER`,
   no constraints/generated/identity/partition/clustering**. Part 1 removed the separate
   classic-masks ALTER phase (which used to re-apply masks even for synthesized tables), so the
   protection is now lost entirely. In the run 38/48 objects synthesized; among tables it was a
   MIX (finance.accounts, orders, invoices, 2 external tables SYNTHESIZED; employees, employees_secure,
   customers, ledger, finance.gl.accounts SHOW_CREATE) — the signature of **transient** SHOW CREATE
   failures with silent fallback. **Verified: `SHOW CREATE TABLE ai27_uc_gov_src.finance.accounts`
   succeeds on the source warehouse now** → the export-time failure was transient (warehouse
   warming / 30s wait), NOT a real limitation → retries + a warehouse close it.

3. **Tags / Grants report status showed the create-skip, not the governance outcome.** In
   existing-catalog mode the catalog create is `SKIP_CREATE_DISABLED` → rendered
   "SKIPPED — not created by utility"; catalog/schema-level **tags and grants inherited that
   label** even though the tag/grant WAS applied (governance + grants run regardless of the create
   toggle). The ABAC sheet was already correct (keys off the governance-op result).

## Final decisions (do not re-litigate)

### P2-A. Warehouse-only DDL capture, with retries, NO silent downgrade
- **Export requires a SQL warehouse for all DDL capture, in BOTH connectivity modes.** Direct =
  the remote source warehouse; airgap = a warehouse on the source workspace (build a local
  `WorkspaceClient` + `RestSqlExecutor(source_warehouse_id)` instead of `SparkSqlExecutor(spark)`).
  `source_warehouse_id` becomes **required** for export; drop the classic-Spark capture path
  (that is where the masked-table flakiness lived). Rationale: the SQL warehouse runs SHOW CREATE
  reliably on governed tables; the classic cluster does not.
- **SHOW CREATE is the ONLY full-fidelity source** for the table/view family — TABLE,
  EXTERNAL_TABLE, VIEW, DYNAMIC_VIEW, MATERIALIZED_VIEW, STREAMING_TABLE, METRIC_VIEW. Capture
  these via SHOW CREATE on the warehouse with **hard retries + exponential backoff + jitter**
  (extend `RestSqlExecutor` knobs: higher `max_retries`, longer `max_wait_seconds`; SHOW CREATE
  is an idempotent read and is already retried on transient statement states). A few extra
  minutes of runtime is acceptable.
- **No silent fidelity downgrade — hard fail.** If SHOW CREATE for any of those table/view types
  still fails after all retries, the object is a **hard `FAILURE`** flagged in the report / Issues
  sheet (`error_code` e.g. `DDL_CAPTURE_FAILED`). It must NOT fall back to synthesized DDL and must
  NOT proceed. The user re-runs. (Confirmed choice: option (a), strictest.)
- **Functions: capture from `information_schema.routines` + `information_schema.parameters` over
  the warehouse** — NOT `DESCRIBE FUNCTION EXTENDED` (works but dumps ~130 lines of session
  `Configs` and needs fragile multi-row parsing; `Input`/`Returns` carry `COLLATE` noise), and
  NOT the REST catalog API (keeps it warehouse-only). `information_schema` gives clean structured
  columns (`routine_definition` = body, `data_type` = return, parameters table = args + mode,
  `is_deterministic`, `routine_body`, comment) → reassemble into `CREATE FUNCTION`. `SHOW CREATE
  FUNCTION` is not supported in Databricks SQL. Functions carry no masks, so this is lossless.
- **REST/API stays ONLY for storage credentials** (`CREATE STORAGE CREDENTIAL` has no SQL form).
  Catalogs / schemas / volumes / external locations remain metadata-based (complete, no SHOW
  CREATE needed). This is the accepted "SQL can't produce it" set — everything else is SQL/warehouse.

### P2-B. Views created on the warehouse at import
- Route the **view-creation phase through the warehouse executor** (the same `import_warehouse_id`
  used for ABAC) when one is supplied; the classic cluster errors on `CREATE VIEW` over a masked
  table. Thread an `executor` parameter through `_import_ddl_file` → `_apply_context` /
  `_object_exists` / `_apply_grants_file` so a view's DDL, session context, existence probe, and
  ordinary grants all run on that executor. Fall back to the main executor only when no warehouse
  is supplied. `_apply_context` on a non-default (warehouse) executor should set `USE CATALOG` /
  `USE SCHEMA` on THAT executor without using the shared `self._context` cache (separate session).
- Preserve fail-closed: a view over a dropped table still fails naturally on the warehouse
  (TABLE_NOT_FOUND) → `FAILURE`.

### P2-C. Report status reflects the governance operation, never the create-skip
- **Tags sheet** `import_status` ← the actual `APPLY_TAGS` op result for that securable (build a
  governance-op index keyed by `target_full_name`/`full_name` from results whose `policies_path`
  is set / action is a governance label), NOT the object create result. Applied →
  "SUCCESS (APPLY_TAGS)"; failed → FAILED; never "not created by utility".
- **Grants sheet** `import_status` ← "APPLIED" whenever the securable exists (create status
  SUCCESS / SKIP_EXISTING / SKIP_CREATE_DISABLED — grants run inline even when create is skipped),
  "FAILED (object not created)" only if the securable create failed. Never the create-skip label.
- **ABAC Policies / Policy Matched Columns** already correct (governance-op keyed) — keep.
- **Column Masks & Row Filters** stays keyed to the TABLE create result (masks are inline → a
  mask failure = table failure). Correct as-is.
- The "SKIPPED — not created by utility" rendering stays ONLY on the per-object-type sheets
  (Catalogs / Schemas / …), where it is accurate.

### P2-D. Defense-in-depth: synthesizer emits inline masks/row filters
- Make `sql_ddl._table_ddl_from_definition` emit inline column `MASK` and table-level
  `WITH ROW FILTER … ON (…)` from `obj.column_masks()` / `obj.row_filter()`. With P2-A this path
  is not hit for tables in normal operation (hard-fail instead), but it removes the possibility of
  a rebuild silently stripping protection. Cheap, strictly-better correctness.

### P2-E. Live fail-closed negative test (requested)
- On the **source** (`source_ws`): create a UDF in a separate catalog **`ai_27`** (any schema,
  e.g. `ai_27.sec.mask_ext`) — `ai_27` is NOT in the migration scope — then a NEW table in
  `ai27_uc_gov_src` whose column uses `MASK ai_27.sec.mask_ext`. On the target, that table's
  `CREATE TABLE` (inline mask referencing the un-migrated function) fails → the table is never
  created / dropped fail-closed → `FAILURE` in Tables + Issues + audit. This proves fail-closed
  live (Part 1's synthetic unit test already covers it in-code).

## Why views must run on the warehouse (mask/view behavior, for reference)
A UC column mask / row filter is bound to the **base table** and applied whenever that data is
read — **including through a view** (creating a view does not strip or bypass it). The mask/filter
**function is evaluated against the querying (invoker) user's identity**, not the view owner's, so
a view cannot launder PII to users who could not otherwise see it. The view uses the **owner's**
privileges to *access* the base table (definer's rights for the access check), but the
mask/filter predicate runs as the **invoker**. Verified live: mask body is
`CASE WHEN is_account_group_member('admins') THEN v ELSE <masked> END`; a probe view over
`hr.employees` returned identically to the base table for a non-`admins` user. Consequence for the
utility: `CREATE VIEW` over a masked table must run on the SQL warehouse (classic Spark errors).

## Files (Part 2)
| File | Change |
|---|---|
| `src/uc_sync/export.py` | Warehouse-only capture; SHOW CREATE for table/view family with retries; **remove silent synth fallback → hard `FAILURE`** for those types; functions from `information_schema`; REST only for storage credentials. |
| `src/uc_sync/import_engine.py` | `RestSqlExecutor`: stronger retry/backoff/timeout knobs for capture. Possibly a helper to read `information_schema` routines/parameters. |
| `src/uc_sync/package_import.py` | Thread `executor` through `_import_ddl_file` / `_apply_context` / `_object_exists` / `_apply_grants_file`; run the view phase on the warehouse executor (`self.warehouse_sql`, reuse the ABAC one). |
| `src/uc_sync/sql_ddl.py` | Synthesizer emits inline `MASK` / `WITH ROW FILTER` (P2-D). Function-DDL builder sources from `information_schema` fields. |
| `src/uc_sync/report.py` | Tags status ← `APPLY_TAGS` op; Grants status ← applied-unless-object-failed; never the create-skip. |
| `notebooks/02_Export.py`, `jobs/*.json` | Require `source_warehouse_id`; always use the warehouse capture executor. |
| `notebooks/03_Import.py` | (already wires `import_warehouse_id`); ensure the warehouse executor is passed for views too. |
| `docs/*` | Warehouse required for export; hard-fail on SHOW CREATE; function capture via information_schema; view-on-warehouse. |

## Tests (Part 2)
1. **Synthesizer emits masks**: `_table_ddl_from_definition` on an object with `column_masks` +
   `row_filter` renders inline `MASK` + `WITH ROW FILTER`.
2. **Hard-fail on capture**: export where SHOW CREATE raises for a TABLE (after retries) →
   that object's export result is `FAILURE` (no synthesized DDL written, run does not silently
   proceed).
3. **Function capture from information_schema**: a fake warehouse executor returning routines/
   parameters rows → correct `CREATE FUNCTION` reassembled (params, return, body, deterministic).
4. **View-on-warehouse routing**: import with a warehouse executor → `CREATE VIEW` statements run
   on the warehouse executor, not the Spark one; context (`USE`) also on the warehouse; a view over
   a dropped table still `FAILURE`.
5. **Report status**: Tags/Grants on a `SKIP_CREATE_DISABLED` catalog show APPLIED/SUCCESS, never
   "not created by utility"; a failed tag on a table shows FAILED.
6. **Regression**: full suite green (Part 1 left it at 197).

## Live verification (existing-catalog + from-scratch, both must pass clean)
- Recreate the 3 target catalogs empty first (see [[target-catalog-recreation-reference]]); set
  `import_warehouse_id=4032a3c63316a01d`.
- Existing-catalog run: SC/EL/catalog SKIP_CREATE_DISABLED; all tables incl. `finance.accounts`
  keep their masks on target (SHOW CREATE via warehouse); all 4 `analytics` views + dynamic views
  CREATED (on the warehouse); Tags/Grants never show "not created by utility"; ABAC 0 failures.
- Add the `ai_27` negative-test table → it (and only it) is `FAILURE`/`PROTECTION_FAILED`.
- Then a from-scratch run (drop catalogs, all `create_*=true`) → same, plus SC/EL/catalog created.
