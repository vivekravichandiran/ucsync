# UC Sync — Development Plan

**Branch:** `feature/abac_refactor` · **Status:** Draft for review · **Owner:** Abhishek Iyer
**Date:** 2026-09-08

This is the plan for the remaining UC Sync work. It is written to be reviewed by a developer: one
section per task, each stating **why** (bug / feature / hardening), **what today does**, and the
**proposed approach**. Facts about current behavior are cited to code; anything not yet verified is
marked **VERIFY LIVE**.

---

## Guiding principles (the contract these tasks must not break)

1. **DDL-only.** The utility migrates *definitions and governance*, never data. No table rows, no
   file contents. (See "Out of scope" for why volume data-copy is a separate effort.)
2. **Fail-closed, but never destroy data.** A table **created this run** (empty shell) that can't be
   protected is **dropped**. A **pre-existing** table (which may already hold **deep-cloned data**) is
   **never dropped** — instead the governance failure is reported and the **job hard-fails** so Ops
   fixes it. Either way, a governance failure is **never a green run** (task 10).
3. **Idempotent, then incremental.** A full run can be re-run safely. An incremental run applies
   **only deltas** — it must not re-do work already done.
4. **Bring-your-own prerequisites (Mode B).** When the customer pre-creates the catalog / schemas /
   storage, the utility uses them and only fills in contents + governance.
5. **Report, don't act, on removals.** Anything *removed* on source (a grant, a policy, an object)
   is **reported**, never deleted/revoked on target. The operator decides.
6. **No metastore / account admin for the SPN — hard client constraint.** Every privilege the
   source and target SPNs need is **catalog-scoped**, granted by the catalog owner. The utility
   must work without making either SPN a metastore or account admin. (See task 8.)
7. **Same experience as the Workspace Migration Utility** where a pattern already exists there
   ([repo](https://github.com/abhishekiyer-databricks/wsmig_utility)): a
   `PERMISSIONS_GUIDE`, a graded preflight, idempotent+incremental with deletions reported.

## Modes (auto-detected, no toggle)

- **Mode A — build from scratch.** Target catalog does **not** exist → utility creates storage
  credential (SC) → external location (EL) → catalog → schemas → objects.
- **Mode B — into existing.** Target catalog **already exists** → utility skips SC/EL/catalog and
  replicates schemas + contents. Detected in `package_import._maybe_enter_existing_catalog_mode`.

## Task index

| # | Task | Type | Priority | Status |
|---|---|---|---|---|
| 1 | [Incremental (delta) sync](#1--incremental-delta-sync) | Feature | P1 | Not started |
| 2 | [Single external-storage mapping file](#2--single-external-storage-mapping-file) | Feature | P1 | Not started |
| 3 | [External-object failures reported, not preflighted](#3--external-object-failures-reported-not-preflighted) | Bug/Hardening | P1 | Not started |
| 4 | [Inventory completeness: enumerate & report every UC object type](#4--inventory-completeness-enumerate--report-every-uc-object-type) | Feature | P2 | Not started |
| 5 | [Skipped/failed external object double-counted as fail-closed](#5--skippedfailed-external-object-double-counted-as-fail-closed) | Bug | P2 | Not started |
| 6 | [Tag rows show SUCCESS after a fail-closed drop](#6--tag-rows-show-success-after-a-fail-closed-drop) | Bug (cosmetic) | P3 | Not started |
| 7 | [`run_as` SPN for the source job](#7--run_as-spn-for-the-source-job) | Ops | P2 | Not started |
| 8 | [Permissions: minimal SPN grants (source & target) + guide](#8--permissions-minimal-spn-grants-source--target--guide) | Ops | P1 | Not started |
| 9 | [Graded preflight gate (environment)](#9--graded-preflight-gate-environment) | Ops | P2 | Not started |
| 10 | [Governance failure hard-fails the job (no green run with unprotected data)](#10--governance-failure-hard-fails-the-job) | Feature/Hardening | P1 | Not started |

---

## 1 — Incremental (delta) sync

**Type:** Feature · **Priority:** P1

### Why
Today every run re-applies the **entire** inventory: `create-or-skip` each object and **re-GRANT
every ACL** (`package_import.py`), regardless of whether anything changed. That is wasteful and it
cannot represent *removals* — a grant/policy deleted on source is invisible on target. We want a
re-run to apply **only what changed since the last run**.

> **Your review point, answered.** "If the ACL was done last time, why do it again?" — you're right,
> it shouldn't. Re-granting everything is the *current full-mode* behavior and is exactly the
> anti-pattern to fix. Incremental mode below **only applies deltas**; unchanged grants/objects are
> left untouched. And because removals are **report-only** (principle #5), a source ACL removal is
> never re-applied *or* revoked — it is surfaced in the report and the operator decides.

### What incremental does (delta, computed from a fingerprint)
State already lives in the **`uc_sync_state` Delta table** — it is `USING DELTA`, UPSERTed via
`MERGE` keeping **one row per source object**, and already carries a `source_definition_hash`
(`src/uc_sync/sync_state.py`). This is the same control-table + fingerprint model the Workspace
Migration Utility uses. We extend each row with two more fingerprints and a grant snapshot:

| Fingerprint stored per object | Detects a change in |
|---|---|
| `ddl_hash` (exists today) | the object's own DDL |
| `governance_hash` (new) | its masks / row filters / column & table tags / ABAC |
| `grants` (new: explicit grant set) | its ACLs |

On an **incremental** run, for each source object, compare current vs stored:

| What changed | Action on target |
|---|---|
| Object is new (no stored row) | Create DDL (empty table shell / full view/function/volume def). |
| `ddl_hash` changed — view/function | `CREATE OR REPLACE`. |
| `ddl_hash` changed — table | **Report `CHANGED`** (no auto-`ALTER`; schema evolution can affect data). |
| Volume | A volume has no data DDL and **no `CREATE OR REPLACE`** — once created its location is fixed. It is only ever `CREATED_NEW` or `UNCHANGED`; a comment/owner/rename change is applied via `ALTER VOLUME`, nothing else. |
| `governance_hash` changed | Apply **only** the changed masks/RF/tags/ABAC; on failure, handle per **task 10** (existing tables are never dropped — job hard-fails instead). |
| A grant appears that wasn't in the stored set | `GRANT` it. |
| A grant in the stored set is gone from source | **Report `REMOVED`** — do **not** revoke (principle #5). |
| Nothing changed | **Skip entirely** — no DDL, no re-grant, no governance re-apply. |
| Object gone from source | **Report `SOURCE_ABSENT`** — do **not** drop. |

**Nothing is re-applied when nothing changed.** Governance/ACLs are touched *only* when their
fingerprint says they changed — this is what makes it incremental rather than a full re-apply.

### Run mode — auto-detected, not a widget
Mode is derived from state, **not** set by the operator: if `uc_sync_state` already holds a baseline
for the scope (a prior successful run recorded in the audit/state tables) → run **incremental**; if
not → run **full** and seed the baseline. An optional `force_full` flag (default off) lets an
operator re-seed / full-reconcile on demand. The customer never has to reason about a `sync_mode`
setting.

### Reporting
The existing report is **unchanged**. We **add one new "Delta" sheet** that lists **only the
objects/grants where a change was detected this run** — one row each, with its action
(`CREATED_NEW`, `REPLACED`, `CHANGED`, `SOURCE_ABSENT`, `GOVERNANCE_UPDATED`, `GRANT_ADDED`,
`GRANT_REMOVED`). `UNCHANGED` objects are **not** listed row-by-row (only summarised in a count), so
the sheet stays a focused "what changed" view. Every action is also written to `uc_sync_audit` with
the `run_id`. `GRANT_REMOVED` and `SOURCE_ABSENT` are informational rows labelled "reported, not
actioned" — nothing is revoked or dropped for them.

### On grants: explicit vs inherited (so the snapshot is correct)
Grants are read from the UC permissions API (`GET /api/2.1/unity-catalog/permissions/{type}/{name}`,
`inventory._attach_grants`), which returns the grants defined **directly on** that securable
(**explicit**) — not privileges **inherited** from a parent (a `GRANT` on a catalog is effective on
its tables without appearing on them). We snapshot/replicate **explicit only**: a catalog-level
change is one `GRANT_ADDED` at the catalog, not a duplicate on every child (which would break
parity).

### Affected components
`sync_state.py` (two new fingerprints + grant snapshot, comparator); `inventory.py`/`export.py`
(compute `governance_hash`, capture explicit-grant snapshot); `package_import.py` (delta gating);
`report.py` (Delta sheet); `package_import.py`/notebooks (auto-detect mode from state; optional `force_full` flag).

### Test plan
1. First run, no baseline in state → auto-detects **full**, seeds the baseline.
2. New object → `CREATED_NEW`; unchanged objects skipped (no writes).
3. Changed view/function → `REPLACED`; changed table → `CHANGED` (report-only).
4. **New mask/ABAC/tag on an otherwise-unchanged table** → `GOVERNANCE_UPDATED` (fingerprint change
   detects it even though DDL is unchanged); a restricted principal sees it enforced.
5. Grant added → `GRANT_ADDED`; grant removed on source → `GRANT_REMOVED` reported, **target grant
   intact** (assert not revoked).
6. No-change re-run → all `UNCHANGED`, **zero writes** (proves no re-apply).
7. Second run with an existing baseline → auto-detects **incremental**; `force_full` re-seeds.
8. `full` path unchanged (existing suite green).

### Resolved decisions (2026-09-09)
- **Delta match key = `full_name`** (not `source_object_id`). A UC object id is stable within one
  source's life but **not across a source rebuild** (`recreate.py` assigns new ids), so it can't be
  the durable baseline key. Keying on `full_name` is robust. A **rename** therefore reads as
  **drop + add** — the new name is `CREATED_NEW`, the old is `SOURCE_ABSENT` (reported, **not dropped**,
  principle #5). That's safe for a DDL-only, report-don't-act tool: no data loss; the operator cleans
  up the orphan. `source_object_id`, when available, is carried **only to annotate a likely rename**
  in the report — never as the match key.
- **Fingerprint retention = latest-only.** One row per source object in `uc_sync_state`, updated in
  place via `MERGE` (already the behavior) — same as the Workspace Migration Utility (UPSERT against
  its Delta state table). History is **not** lost: per-run actions live in `uc_sync_audit` (tagged
  with `run_id`). State stays small; history stays queryable.

---

## 2 — Single external-storage mapping file

**Type:** Feature · **Priority:** P1

### Why
External volumes/tables carry an explicit `LOCATION` in their DDL that must be re-pointed
source→target. Today that needs a separate exact-per-object file, and Mode A uses a *different*
3-column mapping file (`ai27_target_mapping.csv`). **Two files, two mental models.** Reduce to one.

### The one file — `external_locations.csv`, shape auto-selects behavior
```csv
# 2 columns  → BYO mode: SC + EL already exist (customer-created); just re-point LOCATIONs
source_base_path,target_base_path
abfss://data@srcacct.dfs.core.windows.net,abfss://data@tgtacct.dfs.core.windows.net

# 3 columns → CREATE mode: utility creates the storage credential + external location itself
source_base_path,target_base_path,access_connector_id
abfss://data@srcacct.dfs.core.windows.net,abfss://data@tgtacct.dfs.core.windows.net,/subscriptions/.../accessConnectors/tgt-conn
```

**The presence of `access_connector_id` decides the mode — no extra toggle:**
- **2 columns (no connector)** → the SC & EL are prerequisites the customer created. The utility
  creates neither; it just **prefix-swaps** each external object's source `LOCATION` onto the target
  base. *(Answers "how do we manage without the connector column?": we don't need it — creating
  nothing means no connector is required.)*
- **3 columns (connector present)** → the utility **creates** the target storage credential (using
  the Azure access connector) and the external location, then places objects. This is exactly
  today's Mode A mapping behavior, folded into the same file.

This replaces `ai27_target_mapping.csv` (3-col) and the proposed base-path file (2-col) with one
file whose column count is the only thing the operator has to get right.

### The prefix swap (deliberately simple — no matching cleverness)
For each external volume/table, take the source `LOCATION` literal from its DDL; if it **starts
with** a `source_base_path`, replace exactly that prefix with the paired `target_base_path` and keep
the remainder **byte-for-byte**:
```
abfss://data@srcacct…/ap/invoices_ext  →  abfss://data@tgtacct…/ap/invoices_ext
└──────── swapped ────────┘└ kept ┘
```
- Plain `startswith`, first matching row wins. Base paths are expected non-overlapping (one per
  account/container). **No** longest-prefix logic, **no** segment-boundary math, **no** path
  normalization.
- **No matching row → the object is not placed** (reported skipped, see task 3). **If the create
  fails, it fails** (task 3 reports it with the reason). The utility never massages the path.

### Optional per-object override
`object_locations.csv` (exact `schema,volume,table,location` rows) remains supported for the rare
object that doesn't follow the base pattern. Precedence: **exact override → base-path swap → skip.**
It is optional and de-emphasized; most operators only need `external_locations.csv`.

### Affected components
`location_mapping.py` (one loader: detect 2- vs 3-column shape, expose `rewrite(location)` for the
2-col case and the existing SC/EL-create path for the 3-col case); `package_import.py` (use the
unified mapping); notebooks 03 + `00_Install_Jobs` (one `external_locations_path` widget, replacing
the two separate ones); docs.

### Test plan
1. 2-column file → BYO mode: SC/EL not created; external `LOCATION`s prefix-swapped.
2. 3-column file → create mode: SC + EL created from the connector; objects placed (Mode A parity).
3. Source `LOCATION` matches no base row → object skipped (task 3), rest of run continues.
4. No file at all → external objects skipped; managed objects unaffected.
5. `object_locations.csv` override beats the base-path swap.
6. Back-compat: an existing 3-column `ai27_target_mapping.csv` loads unchanged.

---

## 3 — External-object failures reported, not preflighted

**Type:** Bug / Hardening · **Priority:** P1

### Why
When an external volume/table can't be created (path not under any EL, missing permission, bad
path), the operator must see **which object failed and why**, and the run must continue. We
explicitly do **not** want a preflight that inspects external locations and blocks ahead of time.

> **Decision (2026-09-08):** the earlier "EL-coverage preflight" idea is **rejected**. Attempt the
> create; on failure, report it. Nothing about external objects gates the run.

### What today does
The create failure is recorded as `FAILURE` (`package_import.py:840-850`), but the message can be a
raw SQL/overlap error and isn't always distinguished from a genuine skip.

### Proposed approach
- Attempt the external create normally.
- On failure, record a **per-object** report row: object · attempted target path · underlying
  reason (uncovered EL / permission denied / bad path / overlap), with a stable `error_code`.
- **Never abort the run; never touch other objects.**
- Keep the *deliberate skip* (`MANUAL_ACTION_REQUIRED`, no location supplied) clearly distinct from
  a *failure* (location supplied, create failed) so the report never conflates them.

### Affected components
`package_import.py` (surface the real reason on external-create failure), `report.py` (render
object · path · reason), `docs/troubleshooting.md`.

### Test plan
- Uncovered EL → one `FAILURE` with the real reason; run completes; other objects created.
- Permission denied → reported with that reason.
- No location supplied → clean `MANUAL_ACTION_REQUIRED` skip (not a failure).

---

## 4 — Inventory completeness: discover & report the reachable UC AI-asset types

**Type:** Feature · **Priority:** P2

### Why
Today inventory walks only catalog → schema → {tables, views, volumes, functions} and discovers
ELs/SCs (`inventory.py:run`). The UC AI-asset types under a schema are **silently absent** — a
customer can't tell whether "not migrated" means "we checked and you have none" or "we never
looked." We want inventory to **see and report** every UC object type the run principal can actually
reach with catalog-scoped privileges. **Migration scope is unchanged — everything added here is
report-only.**

### Reporting: reuse the existing per-object-type tab pattern (no new "Coverage" section)
The report is already **one worksheet per object type** (`report._TYPE_SHEETS`: Catalogs, Schemas,
Volumes, Functions, Tables, Views, …) plus Summary / Issues / Tags / Grants. It **already has an
`_INVENTORY_ONLY` mechanism** (`report.py:385`) — "objects the utility inventories but never creates;
each gets a lean sheet flagged as inventory-only." So the new types slot in as **their own
inventory-only tabs**, consistent with what's there — not a bolt-on section.

### Scope decision (per review 2026-09-08)
**Only Tier A (catalog-scoped, reachable with catalog `ALL PRIVILEGES`, no metastore admin) is
inventoried.** Tier B (metastore-scoped: connections/foreign catalogs, service credentials, workspace
bindings, unreferenced storage credentials) **and** Tier C (Delta Sharing shares/recipients/providers,
clean rooms) are **out of scope for BOTH migration and inventory** — the run principal can't list them
without metastore privileges we deliberately don't grant, and several can't be copied anyway. They are
handled by a **documentation note** (see Out of scope §C), not by code.

### Tier A — add these inventory-only tabs (report-only), derived from the SDK list surface
| Object | SDK call | Tab | Note |
|---|---|---|---|
| Registered models (+ version count) | `registered_models.list` / `model_versions.list` | Models *(scaffolded in `_INVENTORY_ONLY`)* | AI asset |
| Vector Search indexes | `vector_search_indexes.list_indexes(endpoint)` | Vector Search Indexes *(new)* | listed per endpoint |
| Online tables | `online_tables` | Online Tables *(new)* | derived from a source table |
| Quality / lakehouse monitors | `quality_monitors.get(table)` | Monitors *(new)* | per-table; API evolving — **verify live** |
| UC secrets (schema-level) | `secrets_uc.list(catalog, schema)` | UC Secrets *(new)* | newer API — **verify live** (not workspace secrets) |

Also **move Streaming Tables & Materialized Views out of the default migration set** → report-only:
they are created/refreshed by **DLT/SDP managed pipelines**, so re-issuing their DDL would spin a new
pipeline and re-derive data. Streaming tables can't be recreated standalone (need their ingestion
source) → always report-only; MV migration is gated behind an optional `migrate_materialized_views`
toggle (default **off**). Their tabs already exist (`Streaming Tables`, `Materialized Views`).

Collectors are **derived from the SDK's `w.<service>.list()` surface** under catalog→schema so new UC
types are picked up structurally. All Tier A rows carry `in_scope_for_migration = false`.

### Prune the stale inventory-only scaffolding
`report._INVENTORY_ONLY` currently also lists `CONNECTION`, `SERVICE_CREDENTIAL`, `FOREIGN_CATALOG`,
`SHARE`, `RECIPIENT`, `PROVIDER` (Tier B/C). Since those are now out of scope, **remove those sheets**
(leaving them empty would imply coverage we don't provide) and keep only the Tier A inventory-only
tabs.

### Effort (honest)
**Low–moderate (~1–2 days).** Read-only SDK list calls + inventory-only tabs (the report mechanism
already exists) + the small ST/MV migration-exclusion + `migrate_materialized_views` toggle. Low risk.

### Affected components
`inventory.py` (Tier A discovery collectors, derived from the SDK list surface), `report.py`
(Tier A inventory-only tabs; prune Tier B/C sheets), `models.py` (add any missing enum values, e.g.
online table / VS index / monitor / UC secret), the `migrate_materialized_views` toggle + move ST/MV
out of the default create set, tests with a faked SDK client, and the Out-of-scope §C doc note.

### Test plan
- Seeded source with ≥1 registered model (+ an online table if feasible) → its inventory-only tab is
  populated, `in_scope_for_migration=false`; migration proceeds unaffected.
- Streaming table / MV present → shown report-only, **not migrated** unless `migrate_materialized_views=true`.
- No Tier B/C sheets appear in the report.

---

## 5 — Skipped/failed external object double-counted as fail-closed

**Type:** Bug · **Priority:** P2 · **Repro:** run_id `240339694936437` / import `319599302145590`.

### Why / what today does
An external object that is skipped (no location) is correctly reported `MANUAL_ACTION_REQUIRED` and
never created. **But** if it also carries a governed tag or ABAC match, the fail-closed phase acts
on it and marks it a *second* time as `FAILURE`/`PROTECTION_FAILED` ("dropped fail-closed") — though
it was never created and nothing was dropped. (`gl.accounts_ext` showed both statuses;
`ap.invoices_ext` correctly showed one.)

### Proposed approach
Track skipped/manual **and** failed-to-create objects in a set alongside `_created_objects` /
`_failed_objects`, and have the tag phase, `_abac_matched_tables` / `_record_governance_failure`,
and `_mark_ungoverned_objects()` **skip anything in it**. An object that was never created keeps a
single status; nothing is falsely reported as dropped.

### Test plan
- External object with governed tag + ABAC, no location → exactly one status; no `PROTECTION_FAILED`.
- A governed object that *was* created and fails protection still drops fail-closed (regression).

---

## 6 — Tag rows show SUCCESS after a fail-closed drop

**Type:** Bug (cosmetic) · **Priority:** P3 · **Repro:** run_id `302654184972931`.

### Why / what today does
When a table is dropped fail-closed, its earlier tag ops still read `SUCCESS (APPLY_TAGS)` on the
Tags sheet though the tag is gone. Object rollup already shows `FAILURE`, so this is cosmetic only.

### Proposed approach
In `report.py`, relabel tag/mask/grant ops whose owning object ended `FAILURE` (dropped) as
`ROLLED BACK (object dropped)`, cross-checking `_failed_objects` / `_failed_tables`.

---

## 7 — `run_as` SPN for the source job

**Type:** Ops · **Priority:** P2

### Why / what today does
`run_as` is applied only to target/e2e jobs (`install_jobs._TARGET_RUN_AS_JOB_KEYS`); the
`airgap_source` (Inventory+Export) job always runs as the deploying user. Production needs it to run
as a **source-side** SPN.

### Proposed approach
Add a `source_run_as_spn` widget (distinct from the target `run_as_spn`), applied to `airgap_source`
in `install_jobs._build_spec`. Required grants for that SPN are defined in task 8. Interim: set the
source job's run-as manually in the UI after install.

---

## 8 — Permissions: minimal SPN grants (source & target) + guide

**Type:** Ops · **Priority:** P1

### Why + the exact goal
The customer pre-creates the **catalog and schemas**. The utility must (a) copy the **grants and
policies** onto those pre-created catalog & schema, and (b) migrate every child **table/view/function/
volume DDL and its ACLs/policies**. We must state the exact grant set the SPNs need — and it must
work **without metastore or account admin** (a hard client NO, principle #6).

### What the utility actually reads/writes (verified against code)
- **Reads (source):** object enumeration + DDL via `SHOW CREATE` (warehouse); function bodies, tags
  and ABAC via each catalog's **own** `<catalog>.information_schema` (`governance.py`,
  `sql_ddl.function_ddl_from_information_schema`); **grants/ACLs via the UC permissions API**
  (`inventory._attach_grants`).
- **Writes (target):** create tables/views/functions/volumes (schemas pre-exist); apply grants,
  tags, masks/RF, ABAC onto the pre-created catalog/schema and the created objects; write the
  `uc_sync_state` / `uc_sync_audit` Delta ops tables.

### The answer: catalog-scoped `ALL PRIVILEGES` + `MANAGE` — no metastore admin
Everything above is reachable with **catalog-scoped** grants that the **catalog owner** issues — the
SPN never needs to be a metastore/account admin.

- **`MANAGE` is the key privilege and is NOT part of `ALL PRIVILEGES`** — it must be granted
  explicitly *alongside* it (hence "ALL PRIVILEGES **+** MANAGE"). Verified: reading *all* grants on
  a securable requires the owner, the parent's owner, `MANAGE`, or metastore admin — plain `SELECT`
  shows only your own grants. `MANAGE` on the catalog **cascades** to its schemas/tables, so it
  covers reading (source) and applying (target) grants **and** policies across everything under the
  catalog — including onto the pre-created catalog & schema themselves.
- **`ALL PRIVILEGES` (cascaded from the catalog)** covers `USE CATALOG`/`USE SCHEMA`/`SELECT`/
  `EXECUTE`/`READ VOLUME` (source reads: `SHOW CREATE`, `information_schema`, function bodies, volume
  metadata) and, on target, `CREATE TABLE/VIEW/FUNCTION/VOLUME` (+`CREATE SCHEMA` if ever needed).

```sql
-- SOURCE read SPN (issued by the source catalog OWNER — no metastore admin):
GRANT ALL PRIVILEGES ON CATALOG <src> TO `<src_spn>`;   -- USE/SELECT/EXECUTE/READ VOLUME (reads DDL, info_schema, fns, volumes)
GRANT MANAGE         ON CATALOG <src> TO `<src_spn>`;   -- read grants + policies on the catalog and ALL children (NOT in ALL PRIVILEGES)
-- + CAN USE on source_warehouse_id (SHOW CREATE + information_schema run on the warehouse)

-- TARGET run SPN (issued by the target catalog OWNER — no metastore admin):
GRANT ALL PRIVILEGES ON CATALOG <tgt> TO `<tgt_spn>`;   -- create tables/views/functions/volumes under the pre-created schemas
GRANT MANAGE         ON CATALOG <tgt> TO `<tgt_spn>`;   -- apply grants + policies onto the pre-created catalog/schema (owned by the customer) and created objects
GRANT APPLY TAG      ON CATALOG <tgt> TO `<tgt_spn>`;   -- apply governed tags (if not already covered)
-- + ops tables: GRANT USE CATALOG, USE SCHEMA, CREATE TABLE, MODIFY, SELECT ON SCHEMA <ops>.<schema>
-- + CAN USE on the import/target warehouse (DDL + masks/row filters)
```

**Least-privilege option (if the client prefers tighter than `ALL PRIVILEGES`):** on **source**,
replace `ALL PRIVILEGES` with `USE CATALOG, USE SCHEMA, SELECT, EXECUTE, READ VOLUME` (a read-only
set that omits `CREATE`/`MODIFY`) — still **+ `MANAGE`**, still catalog-scoped, still no metastore
admin. The source SP is read-only, so this tighter set is preferable there; `MANAGE` is the one
non-negotiable addition (nothing weaker reads ACLs). On **target** the SP must create objects, so
`ALL PRIVILEGES + MANAGE` (or the explicit `CREATE …` set + `MANAGE` + `APPLY TAG`) is appropriate.

### External locations & storage credentials — how we avoid needing metastore admin
ELs/SCs are **metastore-level** securables, *not* under the catalog, so catalog-scoped grants don't
cover them. We deliberately **do not require the SPN to read source ELs/SCs**: external object
locations come from the **object's own DDL `LOCATION`** (covered by `SELECT`/`ALL PRIVILEGES`) and
are re-pointed via the operator-supplied `external_locations.csv` (task 2). On target, placing an
external object into a **pre-created EL** needs `CREATE EXTERNAL TABLE`/`CREATE EXTERNAL VOLUME`
**on that EL**, granted by the **EL owner** (the customer) — again no metastore admin.

> **Report note (document this):** because catalog-scoped grants don't reach metastore-level SC/EL,
> the **Storage Credentials / External Locations sheets may be empty** in a catalog-scoped run — this
> is **expected**, not a bug. Source discovery of SC/EL is **best-effort** (empty if the SPN lacks a
> privilege on them, never a failure). In the 3-column create mode the utility still creates + reports
> SC/EL (driven by `external_locations.csv`); in the 2-column BYO / Mode B they are customer
> prerequisites, so empty/skip is correct.

### `information_schema` scope — VERIFY LIVE (still no metastore admin either way)
Databricks docs: the per-catalog `<catalog>.information_schema` needs only `USE CATALOG` +
`SELECT`/`BROWSE` (covered by the grants above), **not** the `system` catalog. **You observed the
customer env required central `system` / `system.information_schema` access.** We do not assume — the
**permission probe** (below) proves it. If it turns out `system.information_schema` reads are needed,
that is a **`SELECT` grant on the `system` schema issued by whoever owns `system`** — it does **not**
make the SPN a metastore admin. The probe records exactly what's required.

**Standing (incremental) set = the same catalog-scoped grants.** Nothing extra for re-runs, and the
utility does not modify the SPN's grants at all (see below).

### Do not touch the run-as SPN's own permissions (simplified — decision 2026-09-08)
Keep it simple: the utility never adds to or revokes from the run-as SPN's grants. When replicating
source grants onto the target, **exclude the run-as SPN as a grantee entirely** — it already holds
catalog-scoped `ALL PRIVILEGES + MANAGE`, and its grants on the *source* catalog are irrelevant to
the target, so they are simply **ignored** during replication. No residual-grant reconciliation, no
revokes, no ownership gymnastics for the SPN. (This replaces the earlier "reconcile residual
`ALL_PRIVILEGES`" idea.)

### The permission probe (the deliverable that removes all guessing)
A small script (`testing/permission_probe.py`) that, **run as the candidate SPN**, executes each
read/write the utility performs (enumerate, `SHOW CREATE`, each `information_schema` query, the
permissions-API read, EL/SC list, a dry-run create + grant on target) and records, per operation,
whether it passed and the exact grant that unblocks it. Its output **is** the verified
`docs/PERMISSIONS_GUIDE.md` for that metastore — including whether `system.information_schema` access
was actually required. This is how we satisfy a strict customer: proven minimum, not asserted.

### Affected components
`docs/PERMISSIONS_GUIDE.md` (new, generated from the probe), `testing/permission_probe.py` (new),
`package_import.py` (exclude the run-as SPN as a grantee when replicating ACLs — no SPN grant changes).

### Test plan
- Probe against the seeded source/target → produces the grant list; each read/write passes on
  exactly that list and fails with one grant removed (proves minimality).
- The run-as SPN's own grants are **unchanged** by the run (none added, none revoked); it is skipped
  as a grantee when replicating source ACLs onto target.

---

## 9 — Graded preflight gate (environment)

**Type:** Ops · **Priority:** P2 · **Repro:** 2026-09-07 `mobility-stg` run produced a bundle but **no report**, with **no failure** (silent degrade — `openpyxl` missing on a proxy-restricted cluster; report generation is best-effort and swallowed).

### Why
Match the Workspace Migration Utility experience: a run must **never silently half-complete or
complete without its report**. That tool gates imports behind a graded preflight
(`src/importers/preflight.py`, `preflight_enforce=true`) returning `GO` / `GO-WITH-WARNINGS` /
`NO-GO`.

### Proposed approach
`src/uc_sync/preflight.py`, run at the top of 01/02/03, gated by `preflight_enforce` (default
`true`), grading **environment** conditions only:
- **BLOCKING (NO-GO):** required libraries importable at the right versions (`openpyxl`, `PyYAML≥6`,
  `databricks-sdk≥0.36`) with an actionable message; source/warehouse reachable; scoped catalogs
  readable.
- **DEGRADING (GO-WITH-WARNINGS):** e.g. ops state schema absent on first run (will be created).
- **COSMETIC:** informational.

Plus: **stop swallowing report failures** (make the report step non-best-effort, or gate behind an
explicit `allow_missing_report=false`), and **pin `databricks-sdk==0.36.0`** across `jobs/*.json` +
`requirements.txt` + `pyproject.toml` so the DBR 15.4 closure is exactly `databricks-sdk` + `openpyxl`
+ `et-xmlfile` (no `protobuf` pull) — a minimal proxy allowlist.

> **Scope boundary (task 3):** external-object placement is **not** a preflight concern. The
> preflight checks the *environment*, never whether each external object's path will succeed.

### Test plan
- Missing `openpyxl` → BLOCKING NO-GO, clear message (not a silent skip); `preflight_enforce=false`
  downgrades to a loud warning.
- Libs present + SDK pinned → GO, report written.

---

## 10 — Governance failure hard-fails the job

**Type:** Feature / Hardening (governance safety) · **Priority:** P1

### Why
Two problems, one task. (1) A governance failure must **never be a silently-green run** — today
`03_Import.py` ends with `dbutils.notebook.exit(summary)`, so a run that dropped tables or hit
`PROTECTION_FAILED` still exits **green** and Ops misses it. (2) With incremental runs against tables
the customer has **already deep-cloned data into**, the old "fail-closed = drop" is no longer safe —
dropping would destroy data. So the safety mechanism for existing tables must shift from *drop* to
*hard-fail-and-flag*.

### The two cases (the split already exists in code via `_created_tables`)
| Table state | Data? | On a governance-apply failure |
|---|---|---|
| **Created this run** (empty shell — in `_created_tables`) | none | **DROP** it (fail-closed) + mark `PROTECTION_FAILED`. *(current behavior, kept)* |
| **Pre-existing** (`SKIP_EXISTING`; may hold deep-cloned data) | possibly | **Do NOT drop.** Mark `PROTECTION_FAILED`, report it. |

**Both cases → contribute to an end-of-run HARD FAIL.**

### Hard-fail mechanism
Accumulate every `PROTECTION_FAILED` across the run; **complete the run** (so the report is
complete), then the import job **exits non-zero** (raise) if any occurred — replacing the
unconditional success exit. A governance failure now produces a **red job**, for the new-table drop
case as well as the existing-table update case.

### How governed tags + catalog-level ABAC protect an existing table *without* dropping it
Verified: ABAC policies are captured with their `on_securable_type` and re-created
`ON CATALOG/SCHEMA/…` at the level they were defined (`governance.abac_policy_create_statement`), and
governed tags are per-object. So for the common **tag-driven, catalog-scoped** pattern:
- **Capture ABAC policies at the catalog** when processing the catalog's ACLs/policies; re-create
  them **at the catalog** on target.
- **Capture the governed tag from the table**; apply it **to the target table**.
- **Apply order: catalog ABAC policy first, then the per-table tag** — tagging then *activates*
  protection that is already present; if the policy is somehow absent, the tag is benign (no false
  sense of protection).
- For a deep-cloned existing table, protection = *(catalog policy exists)* + *(tag applied)* — both
  **metadata** operations, **non-destructive**. If either fails → `PROTECTION_FAILED` → hard-fail,
  **no drop**.

**Governance delta spans two paths — both must be handled for an existing table:**
1. **Table-level** — the table's own `governance_hash` (masks / row filters / column & table tags).
   *Scenario: a new governed tag added to an existing column* → the table's hash changes →
   `GOVERNANCE_UPDATED` → the tag is applied → an **existing** matching policy activates on it.
2. **Policy-object level** — ABAC policies are **catalog/schema-scoped objects delta-tracked in their
   own right** (`CREATED_NEW` / `REPLACED` / `UNCHANGED`), not attributes of the table.
   *Scenario: a brand-new policy is added* → the policy is `CREATED_NEW` and created at its securable;
   the existing table's tag is already present, so the **table itself may read `UNCHANGED`** — the
   *policy creation* is what activates protection. This case is carried by the policy delta, not the
   table hash, so it must not be missed.

Failure mapping ties the two together: `_abac_matched_tables` maps a policy to the tables it protects,
so a **new/changed policy that fails to apply** (e.g. its account-level governed tag isn't defined)
flags the **existing tables it would protect** as `PROTECTION_FAILED` → hard-fail, **no drop**.

### Reporting (which page)
The failure is an incremental action → a `GOVERNANCE_FAILED` row on the **Delta sheet** (task 1),
**and** rolled into the existing **Issues / Summary** sheets. The job hard-fail is what makes it
unmissable regardless of who reads the report.

### Affected components
`package_import.py` (track `PROTECTION_FAILED` for both cases; never drop a `SKIP_EXISTING` table;
apply catalog-ABAC-then-tag ordering), `notebooks/03_Import.py` (exit non-zero when any
`PROTECTION_FAILED` occurred), `report.py` (`GOVERNANCE_FAILED` on Delta + Issues rollup).

### Test plan
1. New table created this run, governance fails → table **dropped** + job **red**.
2. Pre-existing table (holds data), governance update fails → **not dropped**, reported
   `GOVERNANCE_FAILED`, job **red**.
3. Existing deep-cloned table + catalog ABAC policy present + governed tag applied → protected with
   **no touch to data**; job green.
4. Fully clean run → job **green**.

### Decided (deferred) — 2026-09-09
- A *deny-access* fallback for an existing table (revoke reads until protection is fixed, instead of
  leaving it exposed until Ops acts) is **agreed as deferred**: **hard-fail + report is the baseline**;
  deny-access is more secure but more invasive (touches ACLs) and can be added later.

---

## Out of scope (this version) — stated deliberately

### A. Volume data copy (files & directories inside a volume) — **large, separate effort**
**Honest assessment: this is a big lift and does not belong in this version.** Everything above is
**DDL/metadata + governance** (principle #1). Copying the *contents* of a volume is **data-plane
movement**: potentially TB-scale bulk file/dir copy across storage accounts and **regions**, with
its own concerns — throughput/cost, cross-region egress, incremental file-level sync, checksums/
verification, partial-failure and resume, and permissions on the copied files. It is a different
class of tool (a data-copy pipeline) with a different risk profile from a governance migrator.
Recommendation: **track it as its own workstream**, not folded in here. If needed sooner, the
lightweight interim is to *report* volume paths so an operator runs `azcopy`/Databricks
`dbutils.fs`/`COPY` out of band — but the utility itself should not silently start moving data.

### B. Migration of the Tier-A AI assets
Task 4 makes inventory **see and report** registered models, vector-search indexes, online tables,
monitors, and UC secrets (report-only). **Migrating** them is out of scope for this version — the
report shows them `in_scope_for_migration=false` so nothing is a surprise. Streaming tables /
materialized views likewise become report-only (DLT/SDP-owned), MV migration only behind an explicit
off-by-default toggle.

### C. Tier-B / Tier-C metastore-scoped objects — out of migration AND inventory (documentation note)
The following are **not inventoried and not migrated** by the utility, and the docs must say so
plainly (a fixed note in the runbook and the report preamble):
- **Tier B (metastore-scoped, need a metastore/securable grant we don't require):** Lakehouse
  Federation **connections** & **foreign catalogs**, **service credentials**, **workspace bindings**,
  and **storage credentials not referenced by a migrated external location**.
- **Tier C (metastore-scoped + external handshake):** Delta Sharing **shares / recipients /
  providers**, and **clean rooms**.

Why out of both: the run principal is catalog-scoped (no metastore admin, principle #6), so it
**cannot even list** these; and Tier C objects carry external identity/tokens/agreements that can't be
copied — the relationship must be re-established on the target. **Handling:** an operator with
metastore/sharing privileges verifies and re-creates these separately; the utility neither sees nor
touches them.

### D. Non-UC / workspace-level assets
Workspace secret scopes (`/api/2.0/secrets`), vector-search **endpoints**, and model-serving
endpoints are **not UC securables** — they belong to the workspace migration utility
([wsmig_utility](https://github.com/abhishekiyer-databricks/wsmig_utility)), not this one.

---

## Validation — staged, human-gated E2E (Claude runs it as a QA tester)

Three stages, each ending at a **human gate**. Claude does the work; you review and sign off between
stages. The end state is a **production-ready certification** or **bugs + a bugfix plan**.

### Testing charter (the QA agent's system prompt)
> You are an expert QA tester for utilities on Databricks. You have to test this utility end to end as
> a human tester would. Your task is to create a git repo, install jobs notebook run by passing the
> correct widget values and run the job, validate the output and give me a final test report. You have
> to think of all edge cases and include them in your seeding, and you have to report the final
> findings to me, if there are any bugs you find, do the RCA and add it to a bugfix plan and create the
> plan in the `@plans/` directory. If at any point you are unsure you are expected to stop and ask, and
> not take actions outside of the standard testing loop. The only thing outside of the loop defined in
> this prompt is setting up the playground on the target metastore — Azure side ADLS storage account,
> access connector for Azure Databricks, in Databricks storage credential and external location,
> catalog & schema. At the end of the session the utility needs to be certified production ready or
> bugs identified with a bugfix plan.

### Ground rules
- **Front door only** for anything that exercises the utility: jobs are **created by running
  `00_Install_Jobs`** (never `databricks jobs create`); the pipeline is run by **triggering the
  installed jobs**, not side-channel notebook runs.
- **The only out-of-loop / direct setup** is the **target playground** on the target metastore: Azure
  ADLS storage account → access connector for Azure Databricks → Databricks **storage credential** →
  **external location** → **catalog** → **schema**. Everything else stays inside the testing loop.
- **If unsure at any point → STOP and ask.** Do not take actions outside the loop above.

---

### Stage 1 — Seed source + build target playground → **GATE: your UI review**
1. **Seed source:** `fixtures/recreate.py all` (azure → storage → catalogs → objects → negative;
   idempotent), **plus the Tier-A inventory seed**: ≥1 **registered model** and (if feasible) one
   **online table** so task-4 inventory-only tabs are exercised. *(Tier B/C objects are **not** seeded
   — out of scope per Out-of-scope §C.)*
2. **Build the target playground** (the allowed out-of-loop setup): ADLS account + access connector +
   storage credential + external location + **catalogs** `ai27_uc_gov_src / finance / sales` +
   **schemas** `gov_src{hr,finance,sec,analytics}` · `finance{gl,ap,sec}` · `sales{crm,orders,sec}`;
   grant the run-SPN the **task-8 target set** (catalog-scoped `ALL PRIVILEGES` + `MANAGE`, no metastore
   admin).
3. **Deliver a UI review guide** — a table of *use case → where to look in Catalog Explorer → what to
   verify* (e.g. `gov_src.finance.<masked table>` → column mask; `finance.gl` → catalog/schema ABAC +
   governed tag; external tables/volumes → their source `LOCATION`; the seeded model → Models). So you
   can eyeball the source use-cases before any run.
4. **GATE:** you review the source use-cases + target playground in the UI and sign off.

### Stage 2 — Full run + Claude's own validation → **GATE: your sign-off**
1. **Pull the code** into a git repo / Databricks Repo (`feature/abac_refactor`).
2. **Run `00_Install_Jobs`** to create the source & target jobs. Widgets: `external_locations_path`
   (2-col BYO for the primary run), `object_locations_path` (optional override), `force_full`,
   `preflight_enforce=true`, `catalog_mapping_json` (identity or rename).
3. **Trigger the jobs** `01 Inventory` → `02 Export` → `03 Import` (same `run_id`; run 03 on serverless
   / Standard USER_ISOLATION). External-placement sub-cases: **V1** exact `object_locations`, **V2**
   2-col base-path swap, **V3** neither → skip.
4. **Download the report** (xlsx) to **`~/Downloads/ucsync_runs/<run_id>/`**.
5. **Claude validates independently** — read the report **and** cross-check the live target via the
   **SDK/APIs** against source: objects created under the pre-created schemas; grants/tags/masks/ABAC
   applied; external placement; Tier-A inventory-only tabs populated; fail-closed behavior; SPNs hold
   only the task-8 grants (no metastore admin). Produce a **test report**. Any bug → **RCA + a bugfix
   plan in `plans/`**.
6. **GATE:** your sign-off (Stage 3 does not start without it).

### Stage 3 — Incremental run + validation → **certification**
1. **Seed the incremental mutations** (below) on source.
2. **Trigger the jobs again** — incremental is **auto-detected** from the state baseline (no widget).
3. **Download** the new report to `~/Downloads/ucsync_runs/<run_id>/`; repeat the report + API
   validation; deliver the **incremental test report**.
4. **Certify:** utility is **production-ready**, or **bugs identified with a bugfix plan** in `plans/`.

#### Incremental seed (Stage 3) — exactly what will be introduced on source
| Mutation on source | Expected Delta action on target |
|---|---|
| Add a **new table** | `CREATED_NEW` (DDL only) |
| Add a **grant** on an existing table | `GRANT_ADDED` |
| Add a **new mask + catalog-level ABAC + governed tag** to an **otherwise-unchanged** table | `GOVERNANCE_UPDATED` — applied to the already-created target table (fingerprint change detects it despite unchanged DDL) |
| **Remove one grant** on source | `GRANT_REMOVED` — **reported, target grant intact** (not revoked) |
| **Negative:** make a governance update **fail** on a **pre-existing (deep-cloned)** table | Task 10: **not dropped**, `GOVERNANCE_FAILED`, **job exits red** |
| Everything untouched | `UNCHANGED` — **zero writes** |

### Acceptance criteria (checked in Stages 2 & 3, in the report *and* via APIs)
1. Pre-created schemas show `SKIP_EXISTING`; grants/policies applied to them.
2. Tables/views/functions created under the existing schemas, with ACLs.
3. External objects: 2-col file → prefix-swapped paths; 3-col file → SC/EL created; unmatched → skip.
4. External-object failures (task 3): reported per-object with reason; run continues.
5. Inventory completeness (task 4): the seeded model (+ online table) appear in their inventory-only
   tabs, `in_scope_for_migration=false`; ST/MV report-only; **no Tier B/C tabs**.
6. Governance: masks/RF/tags/ABAC enforced for a restricted principal.
7. Fail-closed: negative fixtures fail closed; skipped/failed external objects not double-counted.
8. Incremental (task 1): the seeded changes show the correct Delta actions; `GRANT_REMOVED` and
   `SOURCE_ABSENT` are report-only (targets untouched); unchanged objects → zero writes.
9. Governance hard-fail (task 10): pre-existing table governance failure → **not dropped**,
   `GOVERNANCE_FAILED`, **job red**; new-table governance failure → dropped, **job red**.
10. Preflight (task 9): `preflight_enforce=true` → missing report lib / unreachable warehouse is a
    clear NO-GO, never a silent green run.
11. Permissions (task 8): the whole E2E succeeds with the SPNs holding **only catalog-scoped
    `ALL PRIVILEGES` + `MANAGE`** (source: the tighter read-only set + `MANAGE`), issued by the catalog
    owner — and **neither SPN is a metastore/account admin** (assert explicitly).

---

## References
- Sibling tool (align UX): `github.com/abhishekiyer-databricks/wsmig_utility` — `docs/PERMISSIONS_GUIDE.md`, `src/importers/preflight.py`.
- Existing-catalog (Mode B) design: `plans/schema-locations-and-external-objects.md`; memory `existing-catalog-mode-and-object-locations`.
- Governance / fail-closed (done): `plans/governance-accounting-and-failclosed.md`; memory `governance-failclosed-plan`.
- State primitives: `src/uc_sync/sync_state.py`, `src/uc_sync/audit.py`; memory `ops-tables-and-incremental-sync`.
- Fixtures / seed: `fixtures/README.md`, `fixtures/config.env`; memory `fixture-recreation-bundle`.
- Verified UC facts (grants need MANAGE/owner/admin; per-catalog vs system information_schema): Databricks docs (privileges reference, ownership, information_schema).
</content>
