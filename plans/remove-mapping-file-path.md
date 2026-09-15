# Backlog — fully remove `mapping_file_path`

**Status:** widget + wiring removed (2026-09-15); config-level field/resolution left as **inert dead
code**. This plan is the remaining full excision.

## What was already removed (done, tests green — 316 passed)
- The `mapping_file_path` **widget** in `notebooks/00_Install_Jobs.py` and `notebooks/02_Export.py`
  (definition + the `values` dict entry).
- The `mapping_file_path` entry from the `_simple` passthrough list in `00_Install_Jobs.py`.
- The `"mapping_file_path": "${mapping_file_path}"` placeholder from `jobs/e2e_live.json`,
  `jobs/e2e_dry_run.json`, `jobs/airgap_source.json`.
- The (unused) input key in `tests/test_install_jobs.py`.

Net effect: there is **no operator surface** for `mapping_file_path` anymore. For any widget/job-param
run it resolves to `""` and is a no-op (unmatched job placeholders substitute to blank via
`install_jobs._substitute`; nothing downstream reads `cfg.mapping_file_path`).

## Why the config field was NOT removed yet (the 1% caution)
The resolution still sits in the **storage-path precedence** logic in `src/uc_sync/config.py`:

- field `mapping_file_path: str = ""` (line ~62)
- resolution block (lines ~383-393): if set and `location_mapping_csv_path` unset, it becomes the
  location CSV and feeds `location_mappings`
- **line ~412**: `if not location_mapping_csv_path and not mapping_file_path:` gates whether
  `external_locations_path` feeds `location_mappings`
- constructor arg (line ~440): `mapping_file_path=mapping_file_path`

A regression here **misplaces storage paths**, so removal needs a dedicated config-resolution +
live test pass, not just unit tests.

## The full-removal change
1. Delete the field, the resolution block, and the constructor arg.
2. Simplify line ~412 to `if not location_mapping_csv_path:` (behavior-identical once the variable is
   always `""`, but confirm with a config-resolution test matrix).
3. Confirm the independent YAML input `location_mapping_csv_path` still covers the legacy-CSV case
   (it does today — separate code path at lines ~339-350), and decide whether to consolidate the two.
4. Grep for any remaining references (`grep -rn mapping_file_path src/ tests/`).

## Acceptance
- Config-resolution unit matrix: (a) external_locations only, (b) location_mapping_csv_path only,
  (c) both — assert `location_mappings` + SC/EL toggles unchanged vs. today.
- One live BYO run + one live 3-column (create SC/EL) run — external paths land identically.
- Full suite green.
