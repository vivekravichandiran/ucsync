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

## Context / references
- Verified-clean full-E2E baseline: memory `governance-failclosed-plan`
  ("FULL MODE A E2E — VERIFIED CLEAN 2026-08-26").
- Reusable audit harnesses: `testing/audit_final.py`, `testing/audit_grants_gov.py`.
- Prior art: `plans/governance-accounting-and-failclosed.md` (Parts 1 & 2, done).
