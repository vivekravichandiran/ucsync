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
