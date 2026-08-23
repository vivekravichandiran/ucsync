# UC Governance Migration Utility — Refactor Plan

**Status:** Draft for review
**Date:** 2026-08-22
**Design doc:** [`uc-governance-migration-design.md`](./uc-governance-migration-design.md)
**Codebase:** `src/uc_sync/` (current `UCSync`) → target end-state in the design doc
**Starting branch:** `feature/masks-row-filters`

Each phase is **independently shippable and testable** (`PYTHONPATH=src pytest -q`), with live
checks where marked.

---

## 1. Guiding principles

1. **Preserve full table fidelity — never less than the current version.** The utility reproduces the
   **complete table definition — everything except data**: columns/types/nullability, column + table
   comments, user `TBLPROPERTIES`, partitioning, clustering, constraints, generated/identity columns
   (via the existing `SHOW CREATE`-preferred + synthesis engine and its replay sanitizers), classic
   masks/row filters, grants/ownership — **plus** the new governed **tags** and **ABAC** policies.
   Only **data** is out of scope (written by the data-migration utility's Deep Clone).
   - The internal `delta.*` protocol/feature properties and low-level `COLLATION` cannot be set on a
     `CREATE TABLE` (they throw `DELTA_UNKNOWN_CONFIGURATION` / parse errors) — they are
     storage-engine-managed and carried by the data layer. Today's `rewrite.py` sanitizers strip them
     for exactly this reason; that behavior is **kept** (it is not a fidelity reduction — current code
     does the same).
2. **The refactor is additive-first.** The DDL engine stays. Work = (a) **ADD** governed-tag
   application, ABAC policy create, target storage-credential wiring; (b) **structural cleanup**
   (collapse the 6 modes → `stage`, `LOCAL/CROSS_WORKSPACE` → `connectivity_mode`, clean widgets,
   catalog/schema scoping, 3-column report); (c) **simplify `rewrite.py` to path-only** — drop
   catalog-name rewriting (names never mapped) and external-location renaming (names kept same),
   keep the ADLS path rewrite + the DDL replay sanitizers.
3. **Create + govern; no callbacks; incremental additive.** The utility applies structure + full
   table definitions + governance and stops. The data-migration utility clones data as a principal
   the source ABAC policies already `EXCEPT` (recreated verbatim). Re-runs are additive (§incremental)
   to pick up new grants/tags/policies; existing objects' DDL is not revisited.
4. **Two-layer toggles.** `create_*` gates creation; `apply_*` gates governance and always runs
   against existing objects.
5. **Additive-only.** Never REVOKE/UNSET/DROP; removals are reported, not applied.
6. **Keep the plumbing that works** (REST inventory, DDL engine + sanitizers, audit/state, mapping
   resolver, dependency ranks, reporting shell, auth); refactor the flow, add governance.

---

## 2. Current → target module disposition

| File | Disposition | Action |
|---|---|---|
| `inventory.py` | **Keep + extend** | Reuse REST discovery + policy extraction + full table detail. Add governed-tag reads (`information_schema.*_tags`), ABAC reads (`abac_policy_definitions` + `DESCRIBE POLICY`), connection/share **inventory-only** capture. |
| `models.py` | **Keep + extend** | Keep `UCObject` + all current fields. Add typed `tags`, `abac_policies`; add `GOVERNED_TAG_ASSIGNMENT`, `ABAC_POLICY` object types. Mark MODEL/VECTOR_INDEX inventory-only. |
| `sql_ddl.py` | **Keep engine + add builders** | **KEEP**: quoting, `comment_clause`, SHOW-CREATE type sets + `show_create_command`, `_table_ddl_from_definition` (full table synth), `_view_ddl_from_definition`, `_metric_view_ddl_from_definition`, `_function_ddl_from_definition`, catalog/schema/volume/external-location creators, `grant_statements_for_object`, `mask_/row_filter_/policy_statements_for_object`. **EXTEND**: `_storage_credential_ddl` to take the **target** access-connector id from the mapping (today it only emits when source was MI-backed). **ADD**: `tag_statements_for_object` (`SET TAGS`), `abac_policy_statements_for_object` (`CREATE POLICY … EXCEPT` verbatim). **DROP**: only MV *creation* (materialized views out of scope for create; still inventoried/reported). |
| `rewrite.py` | **Simplify to path-only** | **KEEP** the DDL replay sanitizers (`strip_managed_storage_clauses`, `strip_inline_collate`, `strip_inline_policy_clauses`, `strip_reserved_table_properties`) — they make full SHOW-CREATE DDL replayable — and `_rewrite_storage_urls` (ADLS path rewrite) + `rewrite_json_*`. **DROP** catalog-name rewriting in `rewrite_text` (names never mapped) and `rewrite_external_location_identifiers` (names kept same). |
| `dependency.py` | **Keep + adjust ranks** | functions before table governance; tables/shells; views; governed tags; ABAC; classic masks; grants last. |
| `mapping.py` | **Keep + simplify** | Keep longest-prefix ADLS path rewrite + credential/location resolution. **Drop** catalog-name mapping and principal mapping. |
| `location_mapping.py` | **Replace** | Swap the 6-col CSV for the single storage-cred + location mapping file (design §4.1). |
| `audit.py`, `sync_state.py` | **Keep** | Audit + `source_definition_hash` state for additive incremental. |
| `reporting.py` | **Refactor** | Three per-stage Excel reports (`inventory.xlsx`/`export.xlsx`/`import.xlsx`); Import is the 3-column spine + governance sheets; strip bulky columns; add change report. **HTML generator kept but gated behind a `WRITE_HTML` flag (default off).** |
| `auth.py`, `workspace_client.py`, `security.py` | **Keep** | OAuth/context clients + redaction; add `connectivity_mode`. |
| `config.py`, `job_wrapper.py` | **Refactor** | New widget set (design §4.2): `stage`, `connectivity_mode`, `catalogs`, `schemas`, source SP (direct), `create_*`/`apply_*`, single `mapping_file_path`. Remove `execution_mode`/6-mode sprawl, `catalog_mapping_path`. |
| `components.py` | **Simplify** | Replace preset zoo with an object-family selector aligned to the toggles. |
| `export.py` | **Keep + extend** | Keep bundle writer + manifest/checksums + DDL/grants/policies artifacts. Add tags + ABAC artifacts. |
| `migrate_export.py` | **Simplify** | Keep package copy + path rewrite + the replay sanitizers on table DDL; drop catalog-name rewrite. |
| `import_engine.py` + `package_import.py` | **Merge → one applier** | Single applier: create structure + full table definitions (toggle-gated) + apply governance (tags/ABAC/masks/grants) + grants-last. Keep idempotency/skip-if-exists. **No callback entry points.** |
| `validation.py` | **Fold into report** | Reuse drift statuses (`EXTRA_TARGET`, `DIFFERENT`) for the incremental change report. |
| `notebooks/UC_Sync_Main.py` | **Retire** | Replaced by three thin stage notebooks (below) — no monolithic orchestrator. |
| `notebooks/01_Inventory`, `02_Export`, `03_Import` | **New (thin)** | Widgets only; all logic in `src/`. Mirror the workspace-migration utility. |
| `notebooks/00_Install_Jobs` + `jobs/*.json` | **New** | Job installer + packaged jobs: `direct_end_to_end` (01→02→03 in target) and `airgap_source` (01→02 in source); config set once, projected into job params. Replaces `UC_Sync_Create_Job*`. |
| `notebooks/UC_Sync_Create_Test_Fixtures.py` | **Extend** | Add governed-tag + ABAC (with `EXCEPT`) + tags + partitioned/constrained-table fixtures. |

---

## 3. Phased milestones

### Phase 0 — Branch, baseline, fixtures
- New branch off `feature/masks-row-filters`.
- Rebuild fixtures: a table with partitioning/constraints/user-properties/column-comments (to prove
  full fidelity), governed tags (allowed values), mask/row-filter UDFs, classic mask + row filter,
  a schema-level ABAC policy **with `EXCEPT <data_mig_sp>`**, catalog/schema/volume with tags + grants.
- Snapshot the current suite as regression baseline.

### Phase 1 — Structural cleanup (DDL engine kept intact)
- Collapse `mode` (6) → `stage` (INVENTORY/EXPORT/IMPORT); `execution_mode` → `connectivity_mode`.
- New clean widget set + single `mapping_file_path` (design §4.2); remove `catalog_mapping_path`.
- Simplify `rewrite.py` to path-only: drop catalog-name rewrite + external-location rename; keep the
  replay sanitizers + path rewrite.
- **Verify full table DDL is still reproduced** (partitioning/constraints/props/comments) — regression
  tests on the existing DDL engine must stay green.
- **Exit:** inventory + export + import run under the new stage/connectivity model; suite green.

### Phase 2 — Structure + full table definitions + scoping
- New mapping-file loader (design §4.1) replacing `location_mapping.py`.
- Wire `_storage_credential_ddl` to the **target** access-connector id from the mapping;
  `create_storage_credentials`/`create_external_locations` toggles.
- Create catalogs/schemas/volumes (same name + mapped location) and **full table definitions**
  (all current fidelity, no data) under `create_*` toggles; idempotent skip-if-exists.
- **Catalog/schema scoping + auto-create-deps** (design §4.3): scope to one catalog/schema; if the
  catalog is absent, create it (same name + mapped location) and auto-create its external location +
  storage credential if missing.
- **Exit + live check:** scoped run against a fresh catalog → structure + full table defs
  (partitioning/constraints/props/comments intact) with same-as-source names + mapped paths;
  `create_*=false` skips create but still governs the existing object.

### Phase 3 — Governance: tags (new), ABAC (new), classic masks, grants + infosec gate
- `apply_tags`: `SET TAGS` for catalog/schema/table/column/volume (NEW — today inventoried only).
- `create_abac_policies`: `CREATE [OR REPLACE] POLICY … TO … EXCEPT … MATCH COLUMNS …` recreated
  **verbatim from source** (incl. source `EXCEPT`), catalog references unchanged (names not mapped).
- `apply_masks_row_filters`: classic `SET MASK`/`SET ROW FILTER` as found (existing).
- `apply_grants`: replay grants + ownership as-is; **grants last**.
- Infosec preflight gate (design §7): verify governed-tag definitions + referenced functions exist on
  target; FAIL the object with a specific reason if missing.
- **Exit + live check:** full governance lands on the fixture — tags applied, ABAC policy recreated
  with its `EXCEPT`, classic masks applied, grants last; missing governed tag → object FAILED with reason.

### Phase 4 — Connectivity modes + stages
- (ABAC row-filter exemption behavior — **already verified this session**; no work.)
- `connectivity_mode`: `direct` (target reads source via source-admin SP) + `airgap` (source writes
  bundle → operator moves → target imports). Role derived from stage + mode; bundle dir is the airgap unit.
- **Exit:** both modes run the fixture end-to-end.

### Phase 5 — Incremental + reports
- Additive incremental: hash-skip unchanged; live set-diff for grants/tags/policies; **new ACLs on
  existing objects applied**; removals reported (`REMOVED-AT-SOURCE`), never applied.
- Reshape `reporting.py` to the 3-column report (design §9); strip bulky columns; add the change report.
- **Exit + live check:** re-run after adding + removing a grant at source → added applied, removed only
  reported; report readable.

### Phase 6 — Notebooks, jobs, docs, cleanup
- Retire `UC_Sync_Main`; add three thin stage notebooks `01_Inventory` / `02_Export` / `03_Import`
  (widgets only), mirroring the workspace-migration utility.
- Add `00_Install_Jobs` + `jobs/*.json` (`direct_end_to_end`, `airgap_source`); config set once and
  projected into job params. `connectivity_mode=direct` with no source creds = the "local" case;
  catalog/schema filter is scope only.
- Enforce the clean run-dir layout (`reports/` = 3 Excel + gated HTML; `bundle/inventory.json`;
  `manifest.json` + `checksums/`).
- **Full doc rewrite per §5** — produce the lean doc set (README, runbook, configuration,
  manual-actions, object-support-matrix, architecture, troubleshooting); retire the stale files.
- **Exit:** full suite green + one live end-to-end on the fixture in both modes; the **runbook is
  ≤ ~8 pages, covers all 7 scenarios (§5.2) with exact widget values, every manual step has a
  concrete how-to (§5.3), and contains no `LOCAL`/`CROSS_WORKSPACE`/6-mode/location-CSV references.**

---

## 4. New work items not present today

| Item | Where | Verified basis |
|---|---|---|
| Governed-tag **assignment** (`SET TAGS`) — today inventoried, not applied | `sql_ddl.py` + applier | design §2.5 |
| ABAC policy inventory + create, **verbatim incl. source `EXCEPT`** | inventory + `sql_ddl.py` + applier | design §2.1/§2.3 |
| Target storage-credential wiring (target access-connector id from mapping) | `sql_ddl.py` (`_storage_credential_ddl`) + applier | design §2.4 (partial code exists) |
| Catalog/schema **scoping** + auto-create-deps (external location + storage credential) | `config.py`, inventory, applier | design §4.3 |
| Infosec preflight gate (governed tag + function existence) | inventory + applier | design §7 |
| 3-column report + change report | `reporting.py` | design §9 |
| Connection/share **inventory-only** + MANUAL flag | inventory + report | design §3.2 |

**Explicitly NOT doing** (removed from earlier drafts): `prepare_for_clone`/`apply_governance`
callbacks; `EXCEPT` injection / a `data_migration_principal` widget; catalog-name mapping; reducing
table DDL to a "minimal shell."

---

## 5. Documentation deliverables (full rewrite)

The operating model changes end-to-end, so **all docs are rewritten, not patched** — and the current
~15-file `docs/` set is collapsed to a lean, task-oriented set. The runbook must be followable in one
sitting (**≤ ~8 pages**), tables over prose, every scenario copy-paste-able.

### 5.1 Target doc set (replaces today's docs)
- `README.md` — what it is, the 3-utility context, 5-line quick start.
- `docs/runbook.md` — **the operator guide** (§5.2); the star.
- `docs/configuration.md` — widget reference + the single mapping-file schema.
- `docs/manual-actions.md` — every step the utility can't do, each with a concrete how-to (§5.3).
- `docs/object-support-matrix.md` — updated in/out + create-vs-govern per object.
- `docs/architecture.md` — slim: stages, connectivity modes, run-dir layout, dependency order.
- `docs/troubleshooting.md` — top errors → fix.
- **Retire:** `SOP-uc-sync-runbook.md`, `SOP-step-*`, `SOP-local-mode-quickstart.md`,
  `local-mode-and-reports.md`, `feasibility.md`, `api-mapping.md`, `dependency-model.md` (fold into
  architecture), `job-deployment.md` (fold into runbook), and the old `configuration.md` content.

### 5.2 Runbook spec (short + scenario-driven)
A one-screen **decision tree up front** ("what have you already created?") routing to the right
scenario, then a **widget table per scenario** — no prose walls. Scenarios, each with exact widget
values and a "what success looks like" (exit JSON + which report to read):
1. **From scratch** (new metastore, nothing created) — mapping file provided; all `create_*=true`.
2. **Catalog already exists on target** — `create_catalogs=false` (± schemas/volumes); governance still applied.
3. **Creds / external locations pre-created manually** — `create_storage_credentials=false`,
   `create_external_locations=false`, omit mapping file.
4. **Single-catalog scope** — `catalogs=<one>` (± `schemas=<one>`); auto-creates the catalog + its deps if missing.
5. **Airgap** — 01+02 on source → move `run_<id>/` → 03 on target; what to move + how to verify (manifest/checksums).
6. **Direct / "local" (same workspace)** — no source SP + `connectivity_mode=direct`.
7. **Incremental re-run** — same widgets; what the change report shows; removals are report-only.

### 5.3 Manual-actions guide (every manual step gets a concrete how-to)
Each entry states **who does it, where, the exact command/UI path, and how to verify**:
- **Target metastore** — account console / account API.
- **ADLS containers + access connectors (1-1)** — Azure portal / `az` CLI, plus the two role
  assignments (Storage Blob Data Contributor on the storage account; Reader on the access connector).
- **Governed tag definitions** (account-level) — Tag Policy API `POST /api/2.1/tag-policies`
  (or Catalog Explorer › Governed tags), with the allowed-values shape.
- **Non-MI storage-credential secrets** — manual creation (secrets are never exported).
- **Connections / Delta shares / recipients / providers** — inventory-only; recreate-by-hand pointers.
- (If classic-masked tables must be data-cloned) **convert them to ABAC on source** — a data-migration
  prerequisite, noted as such (not this utility's action).

### 5.4 Style guardrails
- Runbook ≤ ~8 pages; tables over paragraphs; every scenario is copy-paste widget values.
- No dangling references to `LOCAL`/`CROSS_WORKSPACE`, the 6 modes, or the location CSV.

---

## 6. Testing strategy

- **Offline unit tests** (pytest, no workspace): full table DDL fidelity (partitioning/constraints/
  props/comments), tag/ABAC/classic-mask/grant builders, mapping-file parsing, scoping + auto-create-deps,
  path-only rewrite, incremental classification, report shaping. Keep runnable anywhere.
- **Live harnesses** (explicit, fixture catalog on `target_ws`): structure + full table defs, governance
  (tags/ABAC/masks/grants), infosec gate, both connectivity modes, incremental re-run.
- **Regression:** carry forward ALL current DDL-engine + mask/row-filter tests (fidelity must not regress).
- Reuse the live SQL runner pattern from design verification (statement-execution API).
- Note: masks/row filters require Standard/serverless compute — validate on real job compute, not a
  single-user cluster.

---

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Refactor accidentally reduces table fidelity | Principle 1 + carry-forward regression tests on the full DDL engine; Phase 1 exit verifies partitioning/constraints/props/comments still reproduced. |
| Data-migration principal not exempt → clone fails | Not this utility's job — ABAC policy is recreated verbatim with the source `EXCEPT`; data utility owns clone sequencing. |
| Governed tag missing on target | Infosec gate fails the object with a clear reason instead of partial governance. |
| Tag/ABAC SQL varies by DBR | Pin required DBR (SET TAGS 13.3+); prefer `ALTER … SET TAGS`; clear errors. |
| Scope creep into data migration | Hard rule: this utility never issues Deep Clone / moves data. |

---

## 8. Definition of done

- Full table definitions (columns/types/nullability, comments, user properties, partitioning,
  clustering, constraints, generated/identity columns) reproduced on target — everything except data —
  never less than the current version.
- Structure (creds/locations/catalogs/schemas/volumes/functions/views) created with same-as-source
  names + mapped paths, toggle-gated, idempotent, with catalog/schema scoping + auto-create-deps.
- Governance: governed tags applied (new), ABAC policies recreated verbatim incl. `EXCEPT` (new),
  classic masks/filters applied, grants + ownership last, with the infosec preflight gate.
- Additive incremental with a clean 3-column report + change report; removals reported only.
- Both connectivity modes green end-to-end on the fixture; offline suite green (no fidelity regression).
- **Docs fully rewritten to the lean set (§5):** a scenario-driven runbook (≤ ~8 pages) covering all 7
  scenarios with exact widget values and a "what success looks like"; a manual-actions guide where
  every out-of-utility step has a concrete how-to (who/where/command/verify); stale docs retired.
