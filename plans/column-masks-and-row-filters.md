# Plan: Migrate Column Masks & Row Filters

**Status:** Implemented & verified (unit tests + live end-to-end on `ai27_uctest`)
**Author:** (drafted with Claude Code)
**Scope:** `src/uc_sync/*`, docs, tests

> **Implementation note:** the policy phase is integrated inside
> `PackageImportEngine.run()` and `ImportEngine.run()`, so the notebook picks it
> up with **no wiring changes**. The `apply_policies` config toggle (§5.8) was
> deferred — policy application is always-on, matching how grants are always
> applied. Audit downgrade of an unresolvable policy to `MANUAL_ACTION_REQUIRED`
> (§5.10) is also deferred; today an unresolved binding surfaces as a `FAILURE`
> row (never a silent skip).

---

## 1. Problem statement

The utility already inventories, exports, and re-creates UC **functions** in the target
catalog/schema (a masking/row-filter function is just a SQL UDF, so it copies over fine).
What it does **not** do is re-apply the *binding* between that function and the table/view
it protects:

- A **column mask** — `ALTER TABLE t ALTER COLUMN c SET MASK <func> [USING COLUMNS (...)]`
- A **row filter** — `ALTER TABLE t SET ROW FILTER <func> ON (col1, col2, ...)`

So after a sync the mask/filter functions exist in the target, but every table lands
**unprotected**. This is a silent data-exposure gap: the migrated tables look complete but
their governance policies are gone.

The user asked to (a) confirm this, and (b) plan the fix, covering **both** column masks
and row filters.

---

## 2. Verification — confirmed, this is a real gap

Searched the entire tree (`src/`, `notebooks/`, `tests/`):

```
grep -rniE "column_mask|row_filter|SET MASK|USING COLUMNS|SET ROW FILTER" src/ notebooks/ tests/
→ (no matches)
```

Evidence per layer:

| Layer | File / symbol | Finding |
|---|---|---|
| **Model** | `src/uc_sync/models.py` — `UCObject` | `definition` dict holds `columns`, `view_definition`, function params, etc. **No mask / row-filter field.** Column dicts carry name/type/nullable/comment/position only. |
| **Inventory** | `src/uc_sync/inventory.py:316-382` `_iter_tables()` | Fetches full table detail (`GET /api/2.1/unity-catalog/tables/{full_name}`) and stores the raw payload in `source_metadata`, but `definition` only copies structural keys — the table-level `row_filter` object is **dropped**, and although each column's nested `mask` object rides along inside `definition["columns"]`, nothing ever reads it. |
| **Export DDL** | `src/uc_sync/sql_ddl.py:197-241` `_table_ddl_from_definition()` | Synthesized `CREATE TABLE` emits columns + `USING` + location + props only. **No `MASK`, no `ROW FILTER`.** |
| **Export DDL** | `src/uc_sync/sql_ddl.py:244-257` `_view_ddl_from_definition()` | View DDL is passthrough of `view_definition`; no policy handling. |
| **Import (direct)** | `src/uc_sync/import_engine.py` `_managed_table_ddl()` | Rebuilds columns only; never inspects masks/filters. |
| **Import (package)** | `src/uc_sync/package_import.py:202-347` | Executes CREATE DDL + grants per object. **No post-create policy step.** |
| **Ordering** | `src/uc_sync/dependency.py:9-28` | `TABLE=60`, `FUNCTION=70` — **functions are created *after* tables**, so any inline mask on a `CREATE TABLE` would reference a not-yet-existing function. |
| **Docs already flag it** | `docs/uc-object-support-matrix.md:39`, `docs/feasibility.md:90` | "Row filter / column mask — PARTIAL … `column_masks` field present … no masks seen in sample — implement adapter, mark MANUAL if incomplete." Known/deferred. |

**Conclusion:** functions copy; the mask/row-filter *bindings* are lost on both import
paths (direct `ImportEngine` and file-based `PackageImportEngine`). Confirmed.

---

## 3. Background — how UC masks & row filters work

Applied **after** the table and the policy function both exist:

```sql
-- column mask (per column)
ALTER TABLE cat.sch.tbl ALTER COLUMN ssn SET MASK cat.sch.mask_ssn [USING COLUMNS (region)];
-- row filter (per table, one filter)
ALTER TABLE cat.sch.tbl SET ROW FILTER cat.sch.rowfilter_region ON (region);
```

### ✅ VERIFIED against a live workspace (`target_ws` → catalog `ai27_uctest`, 2026-08-20)

Verified against real fixtures: `ai27_uctest.hr.employees` (mask on `ssn` + row filter on
`dept`), plus a purpose-built `USING COLUMNS` case. **Exact field names confirmed** —
this is the authoritative source for coding.

**REST — `GET /api/2.1/unity-catalog/tables/{full_name}` (the inventory's existing call):**

Table-level `row_filter` (top-level key):
```json
"row_filter": { "function_name": "ai27_uctest.sec.hr_dept_filter", "input_column_names": ["dept"] }
```

Column-level `mask` (nested in `columns[i]`):
```json
// no extra columns:
"mask": { "function_name": "ai27_uctest.sec.mask_ssn" }
// with USING COLUMNS:
"mask": { "function_name": "ai27_uctest.sec.mask_by_region", "using_column_names": ["region"] }
```

So the exact keys are:
| Binding | Location in payload | Keys |
|---|---|---|
| Row filter | top-level `row_filter` | `function_name`, `input_column_names` |
| Column mask | `columns[i].mask` | `function_name`, `using_column_names` (**absent** when no USING COLUMNS) |

⚠️ **Sibling keys to NOT confuse:** the payload also carries `row_filters` (a wrapper
`{"row_filters":[...]}`), `effective_row_filters`, and per-column `column_masks` /
`effective_masks`. The `effective_*` variants include **inherited** policies. Migrate the
**directly-defined** ones — top-level `row_filter` and `columns[i].mask` — to avoid
double-applying inherited policies.

**SQL fallback — `information_schema` (confirmed populated), different column names:**
- `information_schema.column_masks`: `table_catalog, table_schema, table_name, column_name, mask_name, using_columns`
- `information_schema.row_filters`: `table_catalog, table_schema, table_name, filter_name, target_columns`

### ✅ VERIFIED: `SHOW CREATE TABLE` emits masks/filters **inline** — the strip step (§5.5) is load-bearing

`SHOW CREATE TABLE ai27_uctest.hr.employees` returns:
```sql
CREATE TABLE ai27_uctest.hr.employees (
  ...
  ssn STRING COLLATE UTF8_BINARY MASK `ai27_uctest`.`sec`.`mask_ssn`,
  ...)
USING delta
DEFAULT COLLATION UTF8_BINARY
WITH ROW FILTER `ai27_uctest`.`sec`.`hr_dept_filter` ON (dept)
TBLPROPERTIES (...)
```
and with USING COLUMNS: `val STRING ... MASK `...`.`mask_by_region` USING COLUMNS(region)`.

Since export **prefers** `SHOW CREATE` for tables (`sql_ddl.py:12-19`), the captured
CREATE DDL contains these inline clauses. Combined with the ordering constraint below,
that means the current behavior is **not** "mask silently dropped" — it is that replaying
the CREATE **fails** ("function not found") unless the function pre-exists, OR (when
`SHOW CREATE` fails on collation and falls back to synthesis) the mask is dropped. Either
way the target table lands unprotected. The clauses are backtick-quoted with the catalog
(`` `ai27_uctest`.`sec`.`mask_ssn` ``), so the existing `rewrite_text` catalog remap
already rewrites the function-reference catalog correctly — good for both the strip and
the re-apply.

### The ordering constraint that shapes the design

**The policy function must exist before the binding is applied.** Because functions
currently import at rank 70 (after tables at 60, `dependency.py:17-19`), and a mask can
reference a function in *another* schema, the only robust ordering is to strip inline
clauses from CREATE and apply **all** bindings in a dedicated phase that runs **after
every object is created**.

---

## 4. Design overview

Introduce masks and row filters as **first-class, deferred bindings** — analogous to how
`GRANT`s are handled: captured per object, rewritten during migrate, and replayed in a
late phase once every securable and function exists.

```
INVENTORY → EXPORT → MIGRATE(rewrite) → IMPORT(create) → [NEW] APPLY POLICIES → GRANTS → VALIDATE
                                                              ▲
                                          ALTER TABLE … SET MASK / SET ROW FILTER
```

Two coordinated moves:

1. **Capture + emit** mask/filter bindings as their own artifact (`policies/*.sql`),
   parallel to `grants/*.sql`, so both import engines can replay them.
2. **Strip inline** `MASK` / `ROW FILTER` clauses out of captured `CREATE TABLE` DDL
   (the `SHOW CREATE TABLE` path *does* emit them inline on recent runtimes) so table
   creation never fails on a not-yet-created function — then re-apply them from the
   policy artifact in the late phase. This mirrors the existing
   `strip_managed_storage_clauses` / `strip_inline_collate` pattern in `rewrite.py`.

This "capture as separate statements + replay late" approach is preferred over "reorder
functions before tables" because (a) it also fixes the direct `ImportEngine` path, (b) it
avoids new dependency cycles, and (c) it keeps CREATE DDL replayable in isolation.

---

## 5. Detailed changes

### 5.1 Model — `src/uc_sync/models.py`

Add optional structured policy data to `UCObject.definition` so it survives
export/JSON round-trips. No new top-level fields strictly required, but add typed
accessors for clarity:

- Store on tables:
  - `definition["row_filter"]` = `{"function_name": str, "input_column_names": [str]}` or `None`
  - `definition["column_masks"]` = `[{"column_name": str, "function_name": str, "using_column_names": [str]}]`
- Add helper(s): `UCObject.column_masks() -> list[...]`, `UCObject.row_filter() -> dict | None`
  that read `definition` (and tolerate the raw nested `columns[i]["mask"]` shape).

### 5.2 Inventory — `src/uc_sync/inventory.py` (`_iter_tables`, ~316-382)

Extract policy metadata from the already-fetched `t` payload into `definition`:

- Copy table-level `t.get("row_filter")` into `definition["row_filter"]`.
- Walk `t.get("columns")`; for each column with a `mask`, collect
  `{"column_name", "function_name", "using_column_names"}` into `definition["column_masks"]`.
- (Optional hardening) if the REST payload lacks these on the target runtime, add a
  SQL fallback that queries `information_schema.column_masks` / `row_filters`, gated by
  availability of `self.sql`.

No new list call needed — the per-table `GET` already returns this.

### 5.3 Export — new DDL builders in `src/uc_sync/sql_ddl.py`

Add, alongside `grant_statements_for_object()`:

```python
def mask_statements_for_object(obj) -> list[str]:
    # ALTER TABLE <full> ALTER COLUMN <col> SET MASK <func_full>
    #   [USING COLUMNS (<c1>, <c2>)];   -- one per masked column
def row_filter_statements_for_object(obj) -> list[str]:
    # ALTER TABLE <full> SET ROW FILTER <func_full> ON (<c1>, <c2>);  -- 0 or 1
def policy_statements_for_object(obj) -> list[str]:
    # convenience: masks + row filter, using quote_full_name / quote_identifier
```

Reuse existing `quote_full_name`, `quote_identifier`. Skip cleanly (return `[]`) for
non-table types and objects with no policies.

### 5.4 Export orchestration — `src/uc_sync/export.py` (~150-265)

Mirror the grants file handling:

- After the grants block (~line 182-195), build `policy_statements_for_object(obj)`;
  if non-empty, write `policies/{stem}.sql` via `_write_text`, and append to an
  `all_policy_ddls` accumulator.
- After the per-object loop, write the aggregate `policies/all_policies.sql`
  (parallel to `grants/all_grants.sql`, ~248-265).
- Track `policy_files` count in the manifest and in the returned summary dict.
- Record the artifact path on `ExportItemResult` (add a `policies_path` field alongside
  `grants_path`).

### 5.5 Rewrite / migrate — `src/uc_sync/rewrite.py` + `migrate_export.py`

- **Automatic path rewrite:** `MigrateExportService.run()` walks the whole package via
  `rglob("*")` and rewrites every file through `_rewrite_file`, which routes non-JSON
  files to `rewrite_text` (catalog remap). New `policies/*.sql` files are therefore
  **already** catalog-rewritten with no change — both the table name and the mask
  function name share the catalog mapping and remap consistently. ✅ Verify
  `_parse_artifact_name` handles the `policies/` filename prefix (add a `POLICY` prefix
  mapping or reuse the table's encoded name so `_map_relative_path` renames the file).
- **New strip helper in `rewrite.py`:** `strip_inline_policy_clauses(text)` to remove
  `MASK <func> [USING COLUMNS (...)]` from column definitions and any table-level
  `ROW FILTER ... ON (...)` / `WITH ROW FILTER ...` from captured `CREATE TABLE` DDL.
  Follow the exact pattern of `strip_inline_collate` / `strip_managed_storage_clauses`
  (lines 67-150). Call it from `strip_managed_storage_clauses` (or from `_rewrite_file`
  for table/MV/streaming types) so the CREATE replays without the function dependency.

### 5.6 Import — apply policies in a late phase

**Package path — `src/uc_sync/package_import.py` (`run`, 202-347):**

- After the existing `ddl_files` loop completes (all CREATEs + grants done), add a
  second loop over `sorted((self.root / "policies").glob("*.sql"))` excluding `all_`.
- For each, `_split_statements` → `_normalize_create_statement` (or a lighter
  normalizer), `_apply_context(...)`, execute each `ALTER`. Reuse the
  already-exists / not-found handling and emit `PackageImportResult` rows with a new
  `action="APPLY_POLICY"` so they show up in reports.
- Idempotency: re-applying `SET MASK`/`SET ROW FILTER` is effectively upsert; treat
  "already set" style errors as SKIP.

**Direct path — `src/uc_sync/import_engine.py` (`run`, `_import_one`):**

- After all objects are imported, iterate objects with policies and execute the
  `mask_statements_for_object` / `row_filter_statements_for_object` output (rewritten via
  the mapping resolver / `rewrite_text`). Emit `ImportResult`s with a policy action.

### 5.7 Dependency ordering — `src/uc_sync/dependency.py`

The policy phase runs after *all* creation, so table-vs-function rank is no longer a
correctness issue. Two low-risk touches:

- (Recommended) Keep CREATE ordering as-is; add a documented `POLICY` step that runs
  after rank 101 and before `GRANT` (110). If policies are modeled as pseudo-objects,
  give them rank ~105.
- (Alternative / defensive) Also elevate `FUNCTION` to run before tables (e.g. 55) so
  that even if an inline clause slips through, the function exists. Verify no SQL UDF in
  the corpus depends on a table (would create a cycle); the corpus sample is 7 functions.

### 5.8 Components / config — `src/uc_sync/components.py`, `config.py`

- Treat policies as an attribute of tables, not a selectable component, so any sync that
  includes `TABLE` also carries its masks/filters. (Simplest; no new preset.)
- Add a config toggle `apply_policies: bool = True` (and a notebook widget) so operators
  can skip policy application for staged rollouts or when target functions aren't ready.

### 5.9 Notebook — `notebooks/UC_Sync_Main.py`

- No new stage box needed if policies ride inside IMPORT. If surfaced separately, add a
  `[6b/7] Applying column masks & row filters` print and fold results into
  `stage_rows["IMPORT"]` / a new `stage_rows["POLICIES"]` for reporting.
- Thread the `apply_policies` widget through to `PackageImportEngine` / `ImportEngine`.

### 5.10 Reporting / audit / validation

- **Reporting** (`reporting.py`): include policy rows in the stage report; add
  success/skip/failure counts for masks & filters.
- **Audit** (`audit.py`): per the support-matrix rule, any unresolved policy (e.g. target
  function missing) must emit `MANUAL_ACTION_REQUIRED` / `UNSUPPORTED` — never a silent
  skip.
- **Validation** (`validation.py`): extend the table comparison to diff source vs target
  masks and row filter (function name + columns) and flag mismatches.

### 5.11 Docs

- `docs/uc-object-support-matrix.md:39` — promote "Row filter / column mask" from PARTIAL
  toward FULL for the covered types; describe the `policies/*.sql` artifact.
- `docs/dependency-model.md` — document the post-create POLICY phase.
- `docs/SOP-uc-sync-runbook.md`, `docs/SOP-step-export-ddl-grants.md` — mention the new
  artifact folder and the `apply_policies` toggle.

---

## 6. Edge cases & risks

- **Views:** column masks/row filters attach to tables (and MV/streaming tables); views
  usually encode filtering in their SQL. Scope v1 to `TABLE`/`EXTERNAL_TABLE`; add
  `MATERIALIZED_VIEW`/`STREAMING_TABLE` if the API exposes policies on them. For plain
  views, the `SHOW CREATE VIEW` text already carries any inline logic.
- **Cross-schema / cross-catalog mask functions:** the function's full name is
  catalog-rewritten by the shared mapping — but confirm the *schema* is in migration
  scope; if the referenced function isn't being migrated, emit `MANUAL_ACTION_REQUIRED`.
- **`USING COLUMNS` / `ON (...)` argument columns** must exist on the target table — they
  will, since the table schema is migrated, but validate ordering/casing.
- **Inline vs deferred double-apply:** ensure the strip step (5.5) actually removes inline
  clauses so we don't both inline-create and ALTER-apply (or fail on missing function).
- **Idempotent re-runs:** applying the same mask/filter twice must be a SKIP, not FAILURE.
- **Runtime support:** `SET MASK` / `SET ROW FILTER` require a UC-enabled warehouse/DBR
  version; gate with a clear error if unsupported.
- **Permissions:** applying a mask needs ownership/`MODIFY` on the table and access to the
  function — surface permission errors as `MANUAL_ACTION_REQUIRED`.

---

## 7. Testing plan

Unit (pytest, mirroring existing `tests/test_export_ddl.py`, `test_rewrite_collation.py`,
`test_local_import.py`):

1. `sql_ddl` — `mask_statements_for_object` / `row_filter_statements_for_object` emit
   correct, quoted `ALTER` statements for single/multi masked columns and a filter;
   empty for tables without policies.
2. `inventory` — a mocked table payload with `row_filter` + `columns[].mask` yields
   populated `definition["row_filter"]` / `definition["column_masks"]`.
3. `rewrite` — `strip_inline_policy_clauses` removes inline `MASK`/`ROW FILTER` from a
   `SHOW CREATE TABLE` sample; `rewrite_text` remaps catalog in a policy `ALTER`.
4. `package_import` — a package containing `policies/*.sql` applies them after CREATE,
   records `APPLY_POLICY` results, and is idempotent on re-run.
5. `validation` — source/target mask+filter diff flags a removed mask.

Integration (optional, live workspace): create a table with a mask + row filter, run a
full export→migrate→import, assert the target table's policies match source.

---

## 8. Suggested phasing

1. **Inventory + model** capture (5.1–5.2) — makes the metadata visible in reports first.
2. **Export artifact** (5.3–5.4) + **strip inline** (5.5) — DDL becomes replayable.
3. **Package-import apply phase** (5.6 package path) — the primary import route.
4. **Direct-import apply** (5.6 direct path) + **validation/reporting/audit** (5.10).
5. **Docs + config toggle + notebook wiring** (5.8–5.9, 5.11).

Each phase is independently shippable and testable.

---

## 9. Open questions

- ✅ **RESOLVED** — Exact REST field names for `row_filter` / column `mask`: verified live
  (§3). `function_name` + `input_column_names` (row filter); `function_name` +
  `using_column_names` (mask). Use directly-defined `row_filter` / `columns[i].mask`, not
  the `effective_*` inherited variants.
- ✅ **RESOLVED** — `SHOW CREATE TABLE` **does** emit masks/filters inline (§3), so the
  §5.5 strip step is required, not optional.
- ⬜ Should `apply_policies` default **on** (governance-safe) or **off** (staged rollout)?
  Recommendation: **on**, with the toggle for opt-out.
- ⬜ Confirm target runtime supports `SET MASK` / `SET ROW FILTER` on all covered table
  types (managed verified; check external tables / MV / streaming tables if in scope).
