# Plan: Replicate into an existing catalog (simple mode)

Branch: `feature/abac_refactor`. When the **catalog already exists on the target**, treat
the catalog + its storage credential (SC) + external location (EL) as **prerequisites the
user created**, and just replicate everything *inside*: schemas and all objects. A single
optional config file gives explicit target locations for schemas, external volumes, and
external tables. **No path reparenting, no guessing** — if the user has a location, they
put it in the file; if not, it's the catalog root (schemas) or skipped with guidance
(external objects).

---

## Two modes (one auto-detected rule)

Decided by whether the mapped target catalog already exists — no new toggle:

- **Mode A — from scratch (existing behavior, unchanged).** Target catalog does **not**
  exist → utility creates SC → EL → catalog → schemas → objects from the mapping CSV.
- **Mode B — into an existing catalog (this plan).** Target catalog **already exists**
  (via `catalog_mapping_json`) → utility **does not** create the catalog, SC, or EL; it
  replicates schemas + all objects. Locations come from the config file below. **No
  mapping CSV required.**

Detection: resolve the target catalog name through `catalog_mapping_json`, probe
`DESCRIBE CATALOG`. Exists → Mode B (force `create_catalogs/credentials/external_locations
=false`). Not → Mode A.

---

## Catalog mapping — rename must not break

`catalog_mapping_json` decides the target catalog and **may rename**, e.g.
`{"mobility-prd":"mobility-prd-tgt"}`. Everything (DDL, grants, tags, ABAC, masks,
`USE CATALOG`) is already routed through `_CatalogRewritingExecutor` /
`rewrite_catalog_references`, so the rename applies uniformly. Two things to guarantee with
tests:
- **Hyphenated / special names** are backtick-quoted correctly on both sides.
- **Substring-prefix targets** (`mobility-prd` → `mobility-prd-tgt`, where the source is a
  prefix of the target) are **not** double-rewritten. (The current sequential replace is
  safe here — backtick lookbehind + `.`-lookahead prevent re-match — but pin it with a
  test.)

---

## Mode B — exactly what happens

| Object | Behavior |
|---|---|
| Storage credential / External location / Catalog | **Skipped** — prerequisites (must already exist). |
| **Schema** | Row `schema,,,location` in the config → `CREATE SCHEMA … MANAGED LOCATION '<location>'`. Not in config → bare `CREATE SCHEMA` → **catalog root**. Already exists → **skip** (`IF NOT EXISTS`). |
| Managed table / view / function | Created under its schema; inherits storage. No location. |
| **External volume** | Row `schema,volume,,location` → `CREATE EXTERNAL VOLUME … LOCATION '<location>'`. **Not in config → `MANUAL_ACTION_REQUIRED`** (`EXTERNAL_LOCATION_MISSING`: "add its location to the config"). Never reparented, never left pointing at source. |
| **External table** | Row `schema,,table,location` → external table created with `LOCATION '<location>'`. **Not in config → `MANUAL_ACTION_REQUIRED`.** |
| Grants / tags / ABAC / masks & row filters / ownership | Applied as today (ownership deferred to the final phase). |

The location the user supplies must be covered by an **existing EL** (prerequisite); the
utility validates and reports `EXTERNAL_LOCATION_MISSING` if not, rather than crashing.

---

## The one config file: `object_locations.csv`

Columns: **`schema, volume, table, location`**. Which columns are filled says what the row
is for:

```
schema,volume,table,location
crm,,,abfss://data@acct.dfs.core.windows.net/crm            # schema managed location
orders,,,abfss://data@acct.dfs.core.windows.net/orders      # schema managed location
orders,archive,,abfss://data@acct.dfs.core.windows.net/orders/archive   # external VOLUME
sales,,raw_events,abfss://data@acct.dfs.core.windows.net/raw_events     # external TABLE
```

Row interpretation:
- **`schema` + `location`** (volume & table blank) → schema `MANAGED LOCATION`.
- **`schema` + `volume` + `location`** → that external volume's `LOCATION`.
- **`schema` + `table` + `location`** → that external table's `LOCATION`.

Notes:
- Optional. Absent → all schemas at catalog root; any external object present in the bundle
  → `MANUAL_ACTION_REQUIRED` (needs a location).
- Names are **source** names (`schema`/`volume`/`table` don't change source→target; only
  the catalog is remapped). So the file is independent of the catalog rename.
- New widget `object_locations_path` on `03_Import` + `00_Install_Jobs` (optional volume
  path).

---

## Gaps / edge cases (called out honestly)

1. **External objects require a config entry** — no reparenting, by design. Missing entry =
   clean `MANUAL_ACTION_REQUIRED`, not a failure; managed objects are unaffected.
2. **The supplied location needs a covering EL** (prerequisite). Utility validates and
   reports `EXTERNAL_LOCATION_MISSING` if uncovered or the principal lacks
   `CREATE MANAGED STORAGE` / `CREATE EXTERNAL …` on it.
3. **Permission is a prerequisite.** Run principal needs `USE CATALOG` + `CREATE SCHEMA`
   (+ create rights) on the existing catalog, or ownership — exactly the last run's blocker
   (ran as SP `b7c3f237-…`, not the owner of `ai27_uctest_target`). Fix:
   `GRANT USE CATALOG, CREATE SCHEMA ON CATALOG <catalog> TO \`<principal>\``, or run as owner.
4. **Location fixed at creation** — no `ALTER SCHEMA … SET MANAGED LOCATION`; an existing
   schema/object stays a skip on re-run. The file only affects first creation.
5. **`object_locations.csv` is per-catalog** — no catalog column; Mode B is one mapped
   catalog per run. (Add a `catalog` column later only if multi-catalog runs are needed.)

---

## Implementation

| File | Change |
|---|---|
| `src/uc_sync/location_mapping.py` | Loader for `object_locations.csv` → `{schema: loc}`, `{(schema,volume): loc}`, `{(schema,table): loc}`. |
| `src/uc_sync/package_import.py` | Mode-B detection (probe existing target catalog → force the three `create_*=false`); on `CREATE SCHEMA` inject `MANAGED LOCATION` from config; on external volume/table, **replace** the `LOCATION '…'` literal with the config value (targeted regex on the emitted DDL) and require a config entry — else `EXTERNAL_LOCATION_MISSING`; preflight that the location has a covering EL. |
| `notebooks/03_Import.py`, `notebooks/00_Install_Jobs.py` | `object_locations_path` widget; thread into the engine. |
| `src/uc_sync/sql_ddl.py`, `inventory.py` | **No change** — DDL stays as-is; the config drives placement. |
| `docs/runbook.md`, `docs/configuration.md`, `docs/troubleshooting.md` | Document Mode A vs B, the config file + the 3 row shapes, prerequisites, catalog rename, `EXTERNAL_LOCATION_MISSING`. |

Reuses existing machinery: create-toggle gating (`_create_enabled`), catalog-rename rewrite
(`_CatalogRewritingExecutor`), `IF NOT EXISTS` skip, deferred ownership. Mode B is mostly
*turning creation off* + one config file + literal `LOCATION` substitution.

---

## Tests (`tests/`)
1. **Mode detect**: existing target catalog → SC/EL/catalog creation skipped; non-existent
   → Mode A unchanged.
2. **Catalog rename**: `{"mobility-prd":"mobility-prd-tgt"}` — DDL/grants/USE-CATALOG land
   on the target name, backtick-quoted; **no double-rewrite** where source is a prefix of
   target; identity map `{"x":"x"}` also fine.
3. **Schema placement**: `schema,,,loc` → `CREATE SCHEMA … MANAGED LOCATION`; unlisted →
   bare (catalog root); existing → skip.
4. **External volume/table**: `schema,vol,,loc` / `schema,,tbl,loc` → `LOCATION` replaced
   with the config value; **missing entry → `EXTERNAL_LOCATION_MISSING`** and the rest of
   the import still completes; uncovered location → `EXTERNAL_LOCATION_MISSING`.
5. **Regression**: Mode A end-to-end still green (175 existing tests).

Run: `PYTHONPATH=src python3 -m pytest`.

---

## Verification (live: Mode B)
Prereq: grant the run principal `USE CATALOG, CREATE SCHEMA` on the target catalog.
- **No config** → all schemas land at catalog root
  (`databricks api get /api/2.1/unity-catalog/schemas/<catalog>.<schema>` → `storage_root:
  null`); SC/EL/catalog shown as skipped; any external object → `EXTERNAL_LOCATION_MISSING`.
- **With config** listing `crm` (schema loc) and `orders,archive,,<loc>` (external volume)
  → `crm` shows that `storage_root`; the volume is created at `<loc>`; others at catalog
  root.
- Rename case `{"...":"...-tgt"}` → objects created under the renamed target catalog.
- Report: schemas/tables/views/functions `SUCCESS`; **no** `*_cred` / `*_el` created; no
  `SCHEMA_NOT_FOUND` cascade.

---

## User prerequisites checklist (Mode B)
1. Target catalog created on the target metastore (name per `catalog_mapping_json`).
2. Its storage credential + external location(s) created — covering the catalog root and
   any location you put in `object_locations.csv`.
3. Run principal has `USE CATALOG` + `CREATE SCHEMA` (+ create rights) on the catalog.
4. (Optional) `object_locations.csv` — a row for each schema/external-volume/external-table
   that needs an explicit location.
