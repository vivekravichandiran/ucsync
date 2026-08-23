# Architecture

## Stages
```
01 Inventory  →  02 Export  →  03 Import
```
- **Inventory** (read-only, source): enumerate securables in scope over REST; attach
  grants (permissions API), governed tags + ABAC policies (via SQL:
  `information_schema.*_tags`, `abac_policy_definitions` + `DESCRIBE POLICY`). Writes
  `bundle/inventory.json`.
- **Export** (source): capture full-fidelity DDL (prefer `SHOW CREATE`, else
  synthesize) + grants + tags + ABAC + classic-policy artifacts; then path-rewrite to
  the target ADLS roots (`migrated/`). Writes `manifest.json` + `checksums/`.
- **Import** (target): replay `migrated/` in dependency order, honoring
  `create_*`/`apply_*`; idempotent and additive.

## Connectivity modes
- **direct** — read the source over REST from the current/target workspace. No bundle
  hand-off. (High-fidelity `SHOW CREATE` requires SQL access to the source; use airgap
  when the source is remote.)
- **airgap** — run 01+02 on the source, move `run_<id>/`, run 03 on the target.

## Run directory
```
run_<id>/
  bundle/inventory.json          # self-describing inventory
  export/run_<id>/               # ddl/ grants/ tags/ abac/ policies/ metadata/ checksums/
  migrated/                      # path-rewritten copy replayed by Import
  reports/  inventory.xlsx export.xlsx import.xlsx
  manifest.json  checksums/
```

## Dependency order
storage credentials → external locations → catalogs → schemas → volumes → functions
→ tables → views → **governed tags → ABAC policies → classic masks/row filters** →
grants (last, so a securable is fully governed before access is granted).

## Modules (`src/uc_sync/`)
`config` (widget contract), `inventory`, `governance` (tags + ABAC reads/DDL),
`sql_ddl` + `rewrite` (DDL synthesis + replay sanitizers, path-only rewrite),
`export`, `migrate_export`, `package_import` (+ `import_engine`), `dependency`,
`mapping`, `location_mapping`, `audit` + `sync_state`, `reporting`, `auth` +
`workspace_client` + `security`.
