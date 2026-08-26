# Incremental / Delta Sync — Plan (stub)

> Placeholder for the next major workstream: **true incremental (delta) sync**.
> Today the pipeline does full, idempotent re-apply only (see memory
> `ops-tables-and-incremental-sync`). Fill in design/scope in a future session.

## Scope (TBD)
- Detect and replay only changed objects (new/altered/dropped) since the last run,
  using `uc_sync_state` (`source_definition_hash`, `source_last_modified_at`,
  `source_object_id`) + `uc_sync_audit`.
- Handle drops/renames on source (currently create-only, never removes on target).
- Governance deltas (mask/row-filter/tag/ABAC/grant changes) re-applied atomically,
  fail-closed preserved.

## Carry-over polish items (small handling, not blockers)
1. **Tags-sheet rollback nuance** (found in the 2026-08-26 final Mode A audit,
   run_id 302654184972931). When a table is dropped **fail-closed** after a governance
   step fails, its earlier tag operations still show `SUCCESS (APPLY_TAGS)` on the
   **Tags** sheet, even though the tag no longer exists on target (verified absent).
   The object-level rollup (Tables / Issues / Summary) correctly shows FAILURE, so this
   is **cosmetic, not a functional/security bug**. Polish: in `report.py`, mark tag/
   mask/grant ops whose owning object ended in FAILURE (dropped) as
   `ROLLED BACK (object dropped)` instead of `SUCCESS`. Cross-check with
   `_failed_objects` / `_failed_tables` in `package_import.py`.
2. **`run_as` for the airgap SOURCE job.** Job-level `run_as` (service principal) is
   currently applied only to the target/e2e jobs — `install_jobs._TARGET_RUN_AS_JOB_KEYS`
   = `{airgap_import_target, e2e_dry_run, e2e_live}`; the `airgap_source`
   (Inventory+Export) job is deliberately excluded, so it always runs as the deploying
   user. Prod need: let the **source** Inventory+Export job run as a **source-side**
   service principal too. Source and target SPNs differ, so this likely needs a separate
   `source_run_as_spn` widget (don't reuse the target `run_as_spn`), applied to
   `airgap_source` in `install_jobs._build_spec`. The source SPN needs the inventory/
   export reads: USE CATALOG/USE SCHEMA/SELECT on the scoped source catalogs + CAN USE on
   `source_warehouse_id` (SHOW CREATE + tag/ABAC reads). Until then, set the source job's
   run-as manually in the workspace UI after install.
3. **Run-as-SPN residual `ALL_PRIVILEGES` (existing-catalog / Mode B).** Found in the
   2026-08-26 airgap catalog-rename import (`ai27_uc_gov_src` → `ai27_uc_gov_src_tgt`,
   run_id 720047402634705, run as SPN `b7c3f237…`). Ownership transferred correctly to the
   source owner (abhishek.iyer), and every *source* grant replicated (parity holds), BUT
   the run SPN was left with an explicit `ALL_PRIVILEGES` grant on the target catalog and
   all schemas/tables/functions/volumes it created — a grant **not present on source**
   (confirmed: that SPN is not a grantee anywhere on source gov_src). Did NOT occur in the
   Mode A run (where the SPN owned the catalog outright and its access came from ownership,
   cleanly removed on transfer). Not a functional/security defect, but for a clean customer
   hand-off the target grant set should match source exactly. Fix options: after
   `_apply_deferred_ownership`, have the import **revoke the run principal's residual
   `ALL_PRIVILEGES`** on objects whose ownership it just handed off (only where the SPN is
   not a mapped source grantee); or document that the operator revokes the migration SPN's
   grants post-run. Investigate the exact source of the grant (likely the Mode B pre-grant
   + create-as-SPN path in `package_import`). Repro: `testing/audit_rename.py` (grant-parity
   checks show it as `tgt_only` ALL_PRIVILEGES for the SPN).

## Context / references
- Verified-clean full-E2E baseline: memory `governance-failclosed-plan`
  ("FULL MODE A E2E — VERIFIED CLEAN 2026-08-26").
- Reusable audit harnesses: `testing/audit_final.py`, `testing/audit_grants_gov.py`.
- Prior art: `plans/governance-accounting-and-failclosed.md` (Parts 1 & 2, done).
