# UC Governance Migration Utility — Design Doc

**Status:** Draft for review
**Date:** 2026-08-22
**Supersedes the operating model of:** the current `UCSync` metadata-sync utility (`src/uc_sync/`)
**Companion doc:** [`uc-governance-migration-refactor-plan.md`](./uc-governance-migration-refactor-plan.md)

---

## 1. Purpose & context

Migrate the **Unity Catalog structure and governance** (metadata + ACLs) of a source metastore
(Azure region 1) to a target metastore (Azure region 2). This utility is **one of three** that run
for a full region move; each owns a clean slice:

```
Workspace-migration utility  → identities (Entra/SCIM users, groups, SPs), workspace assets,
                               AND account-level governed-tag definitions (tag policies)
THIS utility                 → UC structure + empty governed tables + governance (metadata + ACLs)
Data-migration utility       → Delta Share + DEEP CLONE, run as a principal EXEMPT from the ABAC
                               policies → data lands and the ABAC governance is preserved
```

**This utility is about metadata + ACLs. It never moves table data.** It creates **empty,
fully-governed table shells** (columns + comments — no data), and owns **structure** (credentials,
locations, catalogs, schemas, volumes, functions, views), **governed tags**, **ABAC policies**,
classic **masks/row filters** (all applied *as found on source*), and **grants**.

**No callback handshake with the data-migration utility**, and no unlock/re-apply step. But this is
**not** a run-once tool: it is **re-run incrementally** (§8) to pick up **new grants / tags /
policies** over time — the DDL of an already-existing object is not revisited, but its ACLs and
governance are. (Superseded design: an earlier draft added `prepare_for_clone`/`apply_governance`
callbacks; testing showed they are unnecessary — the data-migration utility clones as its own exempt
principal and ABAC governance is preserved — see §2.1.)

The data-migration utility clones data separately. Because each ABAC policy carries its own `EXCEPT`
list (replicated **as-is** from source), a clone run by the exempt data-migration principal preserves
the governance. Classic masks are replicated as-is too; whether a classic-masked table can be
deep-cloned is the **data utility's / source's** concern — **not this utility's**.

Identities are guaranteed to already exist on the target (created by the workspace-migration
utility), so **there is no principal remapping** — grants and policy principals replay as-is.

**Infosec:** the governed empty tables exist on target — with governed tags + ABAC policies attached —
so governance is reviewable before/independently of data landing.

---

## 2. Verified findings (tested live, 2026-08-21/22, `target_ws` metastore `azure:eastus2:fc10db6f…` + `source_ws`; corroborated by the user on `catalog_ws_bozvdk`)

These findings are **load-bearing** — the design is shaped by them.

### 2.1 Deep Clone vs. governance — the exempt-principal rule (informational; the clone is not ours)

`CREATE [OR REPLACE] TABLE tgt DEEP CLONE src`, where `tgt` carries governance:

| Governance on target table | Clone by a **subject** principal | Clone by an **exempt** principal (`EXCEPT` list / not in `TO`) |
|---|---|---|
| Comments, governed **tags**, **grants** | ✅ preserved | ✅ preserved |
| **ABAC** column-mask **or** row-filter policy | ❌ blocked (`COLUMN_MASKS_…` / `ROW_LEVEL_SECURITY_TABLE_CLONE_TARGET_NOT_SUPPORTED`) | ✅ **succeeds; tags + policy preserved**, even `CREATE OR REPLACE` |
| **Classic** `SET MASK` / `SET ROW FILTER` | ❌ blocked | ❌ **still blocked** — classic bindings have no exempt mechanism |

Verified for **both** ABAC column masks and ABAC row filters (subject → blocked; exempt → succeeds &
preserves). Key facts: the restriction fires only when the cloning principal is **subject** to the
policy; **owner/metastore-admin is not exemption** — only the policy's `EXCEPT` (or not being in `TO`)
is; **classic** masks have no `EXCEPT`, so they always block.

**What this means for this utility (and what it does NOT):**
1. This utility replicates governance **exactly as found on source** — including each ABAC policy's
   own `EXCEPT` list. It does **not** inject or manage the data-migration principal into `EXCEPT`;
   that already lives in the source policy and is recreated verbatim.
2. Classic masks / row filters are also replicated **as found**. If that later blocks the
   data-migration utility's Deep Clone, that is the data utility's / source's problem to sequence
   (e.g. convert to ABAC on source). This utility does not strip, warn-gate, or work around it.

### 2.2 Governed tags are account-level and enforced

- Tag Policy API is **account-scoped**: `/api/2.1/tag-policies` (get/list/create/update/delete),
  fields `tag_key`, `values[]`, `description`.
- Assigning a governed tag value not defined at account level **hard-fails**
  (`INVALID_PARAMETER_VALUE` — live proof: `classification` accepted only
  `[PUBLIC, INTERNAL, CONFIDENTIAL, HIGHLY_CONFIDENTIAL, SECRET]`).
- **→ Governed tag *definitions* are OUT of scope** (handled once at account level by the
  workspace-migration utility). This utility only *assigns* governed tags and *verifies* the
  required definitions exist, failing objects that need a missing one (§7).

### 2.3 ABAC policies are metastore-scoped UC objects and creatable here

- **They are Unity Catalog objects that live in the metastore** — created via the UC API
  (`POST /api/2.1/unity-catalog/policies`) / SQL `CREATE POLICY`, attached `ON` a catalog/schema/table,
  surfaced in `system.information_schema.abac_policy_definitions`. Verified: **no workspace
  (`/api/2.0/policies`) or account-level policies endpoint exists**. ⇒ On a **new target metastore
  they do not carry over and must be recreated** by this utility (attached to the mapped securable).
- `CREATE POLICY <name> ON {CATALOG|SCHEMA|TABLE} … COLUMN MASK|ROW FILTER <func> TO <principals>
  [EXCEPT <principals>] FOR TABLES MATCH COLUMNS has_tag('k') | has_tag_value('k','v') …`.
- **A policy is created once `ON` a securable and auto-applies downward** — there is **no per-object
  policy assignment**. The per-object action is the **governed-tag assignment** (`SET TAGS`), which
  activates the policy.
- **The referenced tag MUST be a governed tag that exists at account level** — verified: a plain or
  undefined tag key **fails `CREATE POLICY`** (`UC_INVALID_POLICY_CONDITION: Unknown tag policy key`),
  and a plain tag does not drive a mask.
- Read via `SHOW POLICIES ON …`, `DESCRIBE POLICY …` (gives function, on-column, `EXCEPT`, match),
  `system.information_schema.abac_policy_definitions`.
- Utility's ABAC order: **functions → governed-tag assignments (`SET TAGS`) → `CREATE POLICY`
  (verbatim, incl. source `EXCEPT`) → grants last.**

### 2.4 Structure creation is feasible with same-as-source names

- Storage credentials: names **metastore-scoped**; a new target metastore leaves source names free.
  MI-based creds carry **no secret** — created from an access-connector resource id only.
- External locations: names metastore-scoped; reference the (same-named) target credential.
- Catalogs/schemas/volumes: created with the **same name as source**; `MANAGED LOCATION` / `LOCATION`
  set to the mapped target ADLS path (source sub-paths replicated by longest-prefix rewrite).

### 2.5 Tags & masks — exact mechanisms

- Read tags: `information_schema.{catalog,schema,table,column,volume}_tags`. Apply:
  `ALTER … SET TAGS ('k'='v')` (DBR 13.3+); governed tags need `ASSIGN`.
- Read masks/filters: table REST payload (`row_filter`, `columns[i].mask`) or
  `information_schema.column_masks` / `row_filters`. Apply (classic):
  `ALTER TABLE t ALTER COLUMN c SET MASK <func> [USING COLUMNS(…)]`,
  `ALTER TABLE t SET ROW FILTER <func> ON (cols)`.

---

## 3. Scope

### 3.1 In scope

| Object family | Create on target? | Governance applied | Notes |
|---|---|---|---|
| Storage credentials | ✅ (optional, widget-gated) | grants | From access-connector-id mapping; same name as source; MI-based, no secret. Auto-created when a scoped catalog needs one and it's absent. |
| External locations | ✅ | grants | Same name as source; mapped target ADLS URL + credential. Auto-created when a scoped catalog needs one and it's absent. |
| Catalogs | ✅ **same name as source**; create if absent with same-name + mapped location | grants, tags, ABAC | No catalog rename — names kept identical. |
| Schemas | ✅ same name | grants, tags, ABAC | Mapped `MANAGED LOCATION` where applicable. |
| Volumes (managed + external) | ✅ | grants, tags | External uses mapped `LOCATION`. Volume **files** not copied. |
| Functions (incl. mask/filter UDFs) | ✅ | grants | Must exist before masks/filters/ABAC that reference them. |
| **Managed table — empty shell** | ✅ columns + comments (no data) | governed tags, ABAC, classic masks/filters (as found), grants | Minimal column DDL only — no `SHOW CREATE`/collation/props. |
| MV / streaming tables | ❌ (pipeline-backed) | governance applied once they exist | No shell path. |
| Views, dynamic views | ✅ (if not exists) | grants, tags | View definitions from source; if a referenced table isn't present yet → `PENDING`, applied on a later incremental run. |
| **Grants / ACLs** | — | ✅ replayed as-is | Identities pre-exist; applied last. New grants picked up on incremental runs. |
| **Governed tags (assignments)** | — | ✅ | Value must be defined at account level (else fail — §7). |
| **ABAC policies** | ✅ per securable | ✅ | Recreated **verbatim from source**, including the source `EXCEPT` list. |
| **Classic column masks / row filters** | — | ✅ applied as found | Deep-clone impact (if any) is the data utility's concern, not this utility's. |

### 3.2 Out of scope

| Item | Why | Owner |
|---|---|---|
| Managed table **data** (and MV / streaming-table creation) | Deep Clone in the data-migration utility (empty *shells* created here, never data) | Data utility |
| Governed **tag definitions** (tag policies) | Account-level (§2.2) | Workspace-migration / account setup |
| Identities & principal remapping | Guaranteed present on target | Workspace-migration utility |
| Registered models, vector search indexes, other ML/AI | Explicitly out | — |
| Connections / Delta Shares / recipients / providers | Carry remote secrets/endpoints | **Inventory + flag MANUAL** |
| Storage-credential **secrets**, cloud keys | Never exportable | Platform / Azure admin |
| **Removals** (dropped grants/tags/policies, deleted objects) and **DDL/view-definition changes** on existing objects | Incremental is **additive-only** (§8) | Reported, never applied |

---

## 4. Inputs

### 4.1 Mapping file (single file; catalog names are never mapped)

**Storage-credential + location mapping** (optional; only if the utility creates creds/locations)

```csv
source_adls_path,source_access_connector_id,target_credential_name,target_adls_path,target_access_connector_id
abfss://uc@stwestus.dfs.core.windows.net/meta,/subscriptions/…/accessConnectors/ac-west,cred_meta,abfss://uc@steastus.dfs.core.windows.net/meta,/subscriptions/…/accessConnectors/ac-east
```

- `target_credential_name` defaults to the source credential name (same-name creation).
- Longest-prefix match on `source_adls_path` rewrites derived URLs (external locations,
  catalog/schema managed roots, external-volume paths).
- If creds/locations were pre-created manually, this file can be omitted and the relevant
  `create_*` toggles set off.
- **No catalog-name mapping** — catalog (and schema/table/volume) names are always kept identical to
  source. A catalog absent on target is created with the **same name** and the **mapped location**.

### 4.2 Widgets

| Widget | Values | Purpose |
|---|---|---|
| `stage` | `INVENTORY` \| `EXPORT` \| `IMPORT` | Which stage this run performs. |
| `connectivity_mode` | `direct` (default) \| `airgap` | Direct = target reads source over REST; airgap = source writes bundle, operator moves it, target imports. |
| `catalogs` | csv or blank | **Scope.** One or more catalog names; blank = whole metastore. |
| `schemas` | csv or blank | **Scope.** One or more `catalog.schema` (or bare schema within the scoped catalog); blank = all schemas in scope. |
| `source_workspace_url` + `source_sp_client_id` + secret (scope+key **or** value) | — | Direct mode only. Source workspace-admin SP with READ on all objects of the scoped catalog (catalog scope) or metastore (metastore scope) — see §5.3. |
| `output_volume_path` | `/Volumes/…` | Bundle + reports location. |
| `ops_catalog`, `ops_schema` | — | Audit/state tables (`uc_sync_audit`, `uc_sync_state`). |
| `create_*` toggles | bool | `create_storage_credentials`, `create_external_locations`, `create_catalogs`, `create_schemas`, `create_volumes`, `create_functions`, `create_tables` (empty shells), `create_views`, `create_abac_policies`. **Gate object creation only.** |
| `apply_*` toggles | bool | `apply_grants`, `apply_tags`, `apply_masks_row_filters`. **Applied to whatever exists on target** (created this run or pre-existing). |
| `mapping_file_path` | `/Volumes/…` | The storage-cred + location mapping (§4.1). |
| `dry_run` | bool | Plan only, no mutations. |

**Two-layer toggle semantics:** `create_*=false` does **not** skip the object — it means "assume it
already exists on target; do not create it, but still reconcile its grants/tags/policies." Example:
`create_catalogs=false` + `apply_grants=true` → find existing target catalogs and apply their ACLs.
Sequencing is always driven by dependency rank, independent of the toggles.

### 4.3 Catalog / schema scoping workflow (the common case)

The utility is designed to be pointed at **one catalog (optionally one schema)** at a time:

1. User creates the target metastore, then runs this utility with `catalogs=<one catalog>`
   (optionally `schemas=<one schema>`).
2. If that **catalog is absent on target**, it is **created with the same name** and the **mapped
   location** — and its required **external location + storage credential are created too** if they
   are not already present (using the mapping file for the target access-connector id + path).
3. The utility then replicates **only that catalog's (or schema's) subtree** — schemas, volumes,
   functions, table shells, views, governed tags, ABAC policies, classic masks/filters, grants —
   plus only the creds/locations that subtree depends on.

Leaving `catalogs` blank scopes the run to the whole metastore.

---

## 5. Run flow

Three stages, matching the airgap hand-off:

```
direct mode:   [target ws]  INVENTORY + EXPORT  →  IMPORT            (reads source over REST)
airgap mode:   [source ws]  INVENTORY + EXPORT  →  ⇩ download run_<id>/ dir
               [target ws]  ⇧ upload  →  IMPORT
```

- **INVENTORY** (read-only, source): enumerate securables in scope + attach grants/tags/masks/filters/
  policies. Produces the inventory report + the self-describing `bundle/inventory.json`.
- **EXPORT** (source): finalize the bundle under `output_volume_path/run_<id>/` (see §5.4 layout). In
  airgap this whole directory is what the operator downloads/uploads.
- **IMPORT** (target): walk the bundle in dependency order, honor `create_*`/`apply_*` toggles,
  create missing structure + full table definitions, apply governance, write per-object status.

### 5.4 Notebooks, jobs & run-directory layout (mirrors the workspace-migration utility)

**Notebooks — three thin stage notebooks** (widgets only; all logic in `src/`):
`01_Inventory`, `02_Export`, `03_Import`. The current `UC_Sync_Main` monolith is retired.

**Jobs — a job installer + packaged jobs** (`00_Install_Jobs` + `jobs/*.json`), config set once and
projected into each job's params:
- `direct_end_to_end` — 01→02→03, all in the **target** (reads source over REST).
- `airgap_source` — 01→02 in the **source**; 03 runs in the target after the bundle is moved.

**`connectivity_mode` subsumes the old `LOCAL` mode:** `direct` covers both cross-workspace and
same-workspace. When **no source workspace/SP is provided**, `direct` reads the **current** workspace
— this is the former "local" (catalog A → catalog B in one metastore). The **catalog/schema filter is
scope only**, not a mode: a no-source-creds + `catalogs=<one>` run is simply a local, scoped, direct run.

**Run-directory layout** (clean, fixed — no stray per-object files):
```
run_<id>/
  reports/     inventory.xlsx  export.xlsx  import.xlsx     (+ *.html, emitted only if WRITE_HTML flag on)
  bundle/      inventory.json        ← self-describing metadata (DDL + grants + tags + masks + policies)
  manifest.json   checksums/
```

### 5.1 Creation order (dependency rank, from today's `dependency.py`)

```
storage credentials → external locations → catalogs → schemas → volumes → functions
→ empty table shells → views → governed tags → ABAC policies → classic masks/filters → grants (last)
```

Grants last so a securable is fully governed before access is granted. Catalog/schema-scoped ABAC
policies are table-independent and can be created before any table exists.

### 5.2 Relationship to the data-migration utility

This utility finishes standalone. Separately, the data-migration utility clones data into the shells,
running as a principal that the source ABAC policies already `EXCEPT` (recreated verbatim here), so
the clone succeeds and ABAC governance is preserved. No callback returns to this utility; instead,
this utility is **re-run incrementally** (§8) to pick up governance added on source over time.

### 5.3 Direct-mode source credential (resolved)

Same model as the workspace-migration utility: provide the **source workspace-admin SP**
(`client_id` + secret as a **secret scope + key**, or a widget value), with **READ across all
objects** of the scoped catalog (for catalog-scoped runs) or the whole metastore (for metastore-scoped
runs), sufficient to read DDL, grants, tags, masks, and policies. Always redacted from artifacts/logs.

---

## 6. Decisions (resolved)

1. **ABAC `EXCEPT` handling:** the utility recreates each ABAC policy **verbatim from source**,
   including its `EXCEPT` list. It does **not** inject or manage the data-migration principal —
   exemption is defined on the source policy and carried over.
2. **ABAC row filters:** verified to behave identically to ABAC column masks under the exempt-principal
   rule (§2.1).
3. **Classic masks / ABAC on source:** applied **as found**. This utility is metadata + ACLs; Deep
   Clone success/failure is not its concern.
4. **Views over not-yet-present tables:** attempt, fall to `PENDING`, apply on a later incremental run.
5. **Direct-mode source SP:** resolved (§5.3).
6. **Catalog names:** never mapped/renamed — always same as source; created if absent (§4.3).

---

## 7. Infosec preflight gate (plan 5a side note)

Before applying governance to an object (and reported in the inventory), the utility verifies that the
target has everything the source governance needs:

1. The **governed-tag definitions + allowed values** used by the object exist at account level
   (else `SET TAGS` / `CREATE POLICY` fail — §2.2/§2.3).
2. The **mask/row-filter functions** referenced by the object's masks/filters/policies exist on target.

If a required piece is missing, the object is marked **FAILED** with the specific reason
(e.g. `GOVERNED_TAG_MISSING: pii_type=SSN`) rather than being silently governed-partially. Because
grants are applied last, a securable whose governance couldn't be fully applied never gets its
consumer grants — "governed before granted."

---

## 8. Incremental runs (additive-only, hash + live-diff)

The utility is **re-run over time**; each run is additive. It reuses the state table (`uc_sync_state`,
keyed by `source_full_name` + `object_type`, storing a `source_definition_hash`). Per object and per
binding:

| Situation | Action |
|---|---|
| New at source (object, grant, tag, policy) | **CREATE / APPLY** — including **new ACLs on existing objects** |
| Unchanged (hash matches) | **SKIP** — existing object's DDL not revisited |
| **Removed at source** (grant dropped, tag/mask/filter/policy removed, object deleted) | **REPORT only — never REVOKE / UNSET / DROP** |
| DDL / view-definition change on an existing object | **OUT of scope** (reported as drift, not applied) |

- New-ACL handling is the core incremental case: a grant/tag/policy added on source after the first
  run is detected (live set-diff of source vs target per object) and **applied**.
- Removals surface as `EXTRA_TARGET` and are left untouched. A future `allow_destructive_operations`
  toggle (default **off**) would be the only way to enforce removals; not in v1.
- Every run emits a **change report**: `CREATED / APPLIED / UNCHANGED / REMOVED-AT-SOURCE (reported)`.

---

## 9. Reports (clean, workspace-migration-utility style)

**Three stage reports, Excel, one per stage: `inventory.xlsx`, `export.xlsx`, `import.xlsx`** (under
`run_<id>/reports/`). The **Import** report is the comprehensive one — it carries the full spine
(inventory→export→import status) plus the governance sheets (§9.1); Inventory/Export are the same
shape scoped to their stage. **HTML is generated only when a `WRITE_HTML` flag is on** (default off,
mirroring the workspace-migration utility) — the generator stays in code, commented/gated, so it can
be enabled later without new work. Excel is the only active format.

One inventory row per securable is the spine; export and import status are columns on it.

| object | type | inventory | export | import | manual? | note |
|---|---|---|---|---|---|---|
| `sales_prod` | CATALOG | ✅ | ✅ | ✅ created (same name + mapped loc) | | |
| `sales_prod.crm.customers` | TABLE (shell) | ✅ | ✅ | ✅ created + governed | | |
| `sales_prod.crm.customers.email` | ABAC_POLICY | ✅ | ✅ | ✅ applied (EXCEPT from source) | | |
| `sales_prod.crm.legacy.ssn` | COLUMN_MASK (classic) | ✅ | ✅ | ✅ applied | | |
| `cred_meta` | STORAGE_CRED | ✅ | ✅ | ⚠️ MANUAL | ⚠️ | access connector not found |
| `finance_share` | SHARE | ✅ | — | — | ⚠️ | inventory-only, recreate manually |

- **Import status only populated where export succeeded.**
- **Manual column** flags only what the utility can't do — *excluding* the two prerequisites the
  customer owns (ADLS containers + access connectors), which are assumed, not failures.
- Formats: one Excel workbook + one branded HTML summary per run, plus machine-readable
  inventory/manifest JSON. Strip the current report's bulky columns to only what an operator needs.

### 9.1 Governance-detail sheets (column / policy grain)

Beyond the spine, the workbook has dedicated **governance sheets** so a reviewer can read, per line,
exactly which function/policy is applied where — each with inventory/export/import status columns:

**"Column Masks & Row Filters"** — one row per masked column or filtered table:

| catalog | schema | table | column | kind | function | using/on cols | policy | to → except | inv | exp | imp |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bankx_abac_demo | customers | customers | ssn | CLASSIC MASK | `bankx.sec.mask_ssn` | — | — | — | ✅ | ✅ | ✅ applied |
| catalog_ws_bozvdk | azcopy_test | clone_abac_src | email | ABAC MASK | `…mask_email` | `has_tag('ai27_masking')` | `abac_test` | account users → abhishek.iyer | ✅ | ✅ | ✅ applied |
| aon_demo | claims | large_losses | (region) | ABAC ROW FILTER | `…rf` | `has_tag_value('access_class','bu_column')` | `bu_row_filter` | account users → — | ✅ | ✅ | ✅ applied |

**"Tags"** — governed-tag assignments (the ABAC trigger):

| object | level (CATALOG/SCHEMA/TABLE/COLUMN/VOLUME) | key | value | governed? | inv | exp | imp |
|---|---|---|---|---|---|---|---|
| `…clone_abac_src.email` | COLUMN | `ai27_masking` | true | ✅ | ✅ | ✅ | ✅ applied |
| `sales_prod` | CATALOG | `classification` | INTERNAL | ✅ | ✅ | ✅ | ✅ applied |

**"ABAC Policies"** — the policy objects: `policy_name`, `on_securable_type`, `on_securable`,
`policy_type` (COLUMN_MASK/ROW_FILTER), `function`, `match_columns` (tag condition), `to_principals`,
`except_principals`, inv/exp/imp.

**"Grants"** — `object`, `principal`, `principal_type`, `privileges`, inv/exp/imp.

Every governance sheet joins back to the spine by full name, and the import column carries the same
statuses as elsewhere (`applied` / `PENDING` / `FAILED: <reason>` / `MANUAL`).
