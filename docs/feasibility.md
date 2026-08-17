# UC Sync — Technical Feasibility Assessment

**Date:** 2026-08-15  
**Method:** Live REST probes against configured CLI profiles `uc-source` and `uc-target`  
**Verdict:** **FEASIBLE** for metadata-only Unity Catalog cross-metastore sync on Databricks-native notebook/job execution, with explicit mapping and permission prerequisites.

---

## 1. Workspace pairing (verified)

| Role | Profile | Workspace URL | Workspace ID | Metastore ID | Region signal |
|------|---------|---------------|--------------|--------------|---------------|
| Source | `uc-source` | `https://adb-7405618912789045.5.azuredatabricks.net` | `7405618912789045` | `008f5578-1fca-4f87-b4fb-ce0545efc00e` | `metastore_azure_westus3` |
| Target | `uc-target` | `https://adb-7405609958717235.15.azuredatabricks.net` | `7405609958717235` | `5a7903e7-8a04-4b13-987d-bfa7f6b5e906` | `metastore_azure_eastus` |

Cross-metastore confirmed (distinct metastore IDs). This is a true region/metastore migration path, not same-metastore workspace binding alone.

Authenticated identity on both: `vivek.ravichandiran@databricks.com` (workspace `admins` group on both).

---

## 2. API surface — what works today

| Capability | Source | Target | Notes |
|------------|--------|--------|-------|
| PAT auth | PASS | PASS | HTTP 200 on SCIM Me + UC APIs |
| List catalogs (paginated) | PASS | PASS | Target returns sparse pages with empty `catalogs` + `next_page_token` — client must keep paging |
| Schemas / tables / volumes / functions | PASS | PASS | Full object GET returns columns, properties, `created_at`, `updated_at` |
| Storage credentials | PASS | PASS | 1 each (`classic_stable_westus3_vk` / `classic_stable_target_vk`) |
| External locations | PASS | PASS | Different ADLS accounts — **mapping mandatory** |
| Connections list | PASS | PASS | Mostly system AI agent connections |
| Delta Sharing (shares/recipients/providers) | PASS (empty) | PASS (empty) | APIs reachable; no objects in sample |
| Grants (direct + effective) | PASS | PASS | `/permissions/{securable_type}/{full_name}` |
| Workspace bindings | PASS | PASS | Works for `ISOLATED` catalogs; empty for `OPEN` |
| SQL warehouses | PASS | PASS | 1 serverless warehouse each (`STOPPED`) |
| Secret scopes | EMPTY | EMPTY | **OAuth-via-scope not configured yet** |
| Catalog + schema CREATE | — | PASS | Requires explicit `storage_root` (Default Storage account mode) |
| Catalog DELETE (force) | — | PASS | Feasibility probe cleaned up |

---

## 3. Source inventory sample (`ril_*` catalogs)

Live count (excluding `information_schema` schemas):

| Object | Count |
|--------|------:|
| Catalogs | 5 (`ril_raw`, `ril_sandbox`, `ril_bulk`, `ril_curated`, `ril_migration`) |
| Schemas | 17 |
| Managed tables | 72 |
| Views | 2 |
| External tables | 1 |
| Volumes | 4 |
| Functions | 7 |

Object types present that exercise the design: managed Delta, external Delta, external volume, functions, views, grants, isolated catalog bindings (on workspace default catalogs).

---

## 4. Target state (important)

Target already contains `ril_raw`, `ril_sandbox`, `ril_bulk`, `ril_curated`, `ril_migration`, plus `ril_demo` / `rilmigration` and `uc_migration_state`.

`ril_sandbox.edge` on target already has the same managed table names as source (`tbl_all_types`, `tbl_defaults`, …).

**Implication:** First useful modes are `COMPARE` / `VALIDATE` / `SYNC` with `CREATE_OR_SKIP` / `RECONCILE`, not greenfield `CREATE_ONLY` for those catalogs.

---

## 5. Feasibility by design requirement

| Requirement | Status | Evidence / constraint |
|-------------|--------|------------------------|
| Notebook + Job only (no VM/CLI app) | FEASIBLE | Standard Databricks Repo + Job notebook task |
| Cross-workspace REST from notebook | FEASIBLE | UC 2.1 APIs work with Bearer token from either workspace |
| OAuth SP via secret scope | BLOCKED (config) | No secret scopes exist yet on source or target; design remains correct — must create scopes + SP secrets before production Job runs |
| Export package on UC Volume | FEASIBLE (pending volume) | Volume APIs work; need a managed/external volume under a migration catalog on the execution workspace |
| Managed Delta audit table | FEASIBLE | Verified under `classic_stable_target_vk.uc_sync_ops`. Avoid `ril_migration` — its managed-storage access connector is broken (`UC_AZURE_CREDENTIAL_NOT_FOUND`) |
| `source_last_modified_at` from REST | FEASIBLE for core types | Catalogs, schemas, tables, volumes expose `updated_at` (epoch ms). Use `source_last_modified_source=REST_API`. Do not invent when absent |
| Canonical model + DDL secondary | FEASIBLE | Table GET returns columns/properties/storage; DDL via `SHOW CREATE TABLE` when warehouse running |
| Dependency-ordered import | FEASIBLE | Credential → location → catalog → schema → table/volume/function → view |
| Credential / location mapping | REQUIRED | Different ADLS storage accounts between westus3 and eastus |
| Managed storage mapping | REQUIRED | Target CREATE CATALOG failed without `storage_root`; succeeded with mapped `abfss://…` under target storage |
| Principal mapping | REQUIRED for prod | Source grants include `account users` + user principals; groups differ by workspace clone |
| Workspace binding remap | FEASIBLE | API returns `workspace_id` + `binding_type`; never copy source IDs blindly |
| Idempotent hash compare | FEASIBLE | Hash canonical JSON of definition fields |
| Dry-run | FEASIBLE | Orchestrator-only; no writes |
| Incremental sync | PARTIAL | `updated_at` reliable for probed types; always pair with definition hash |
| Tags as first-class | PARTIAL | Standalone `/tags` list 404; use object-level / SQL `information_schema` / SDK tag assignment APIs |
| Row filters / column masks | PARTIAL | Column payload includes `column_masks`; no masks seen in sample — implement adapter, mark MANUAL if incomplete |
| Materialized views / streaming tables | UNVERIFIED in sample | Treat as SQL+REST hybrid; no auto refresh by default |
| Registered models | UNVERIFIED | UC models API requires schema; MLflow search empty — METADATA_ONLY until proven |
| Physical data copy | OUT OF SCOPE | Confirmed by design; external tables keep source URL unless remapped |
| Databricks MCP | UNAVAILABLE | Cursor Databricks MCP in error state; feasibility used REST + profiles instead |

---

## 6. Hard blockers before production Job

1. **Create Databricks secret scopes** on the execution (preferably target) workspace and store source/target SP client id/secret.  
2. **Provision export volume** e.g. `/Volumes/<migration_catalog>/<schema>/uc_exports`.  
3. **Create mapping YAML** for storage credentials, external locations, managed storage roots, principals, workspaces.  
4. **Grant Job SP / runner** metastore `CREATE_*` + object ownership/USE privileges (today CREATE works via workspace admin PAT).  
5. **Start or attach SQL warehouse** for `SHOW CREATE` / validation SQL paths (currently STOPPED).

None of these are architectural blockers; they are environment prerequisites.

---

## 7. Recommended deployment model

**Model A (preferred):** Notebook Job runs in **target** workspace (`uc-target`).

- Local target UC/SQL for import + audit + volume writes  
- Cross-workspace client to source for inventory/export  
- Matches PDF preferred model and current admin access on both sides  

---

## 8. Go / No-Go

| Decision | Result |
|----------|--------|
| Proceed with architecture + scaffold | **GO** |
| Proceed to implement INVENTORY/EXPORT/COMPARE first | **GO** |
| Proceed to destructive IMPORT without mappings + dry-run | **NO-GO** |
| Claim full MV/model/tag parity without further probes | **NO-GO** |

---

## 9. Probe artifacts (reference)

- Metastore assignment: `GET /api/2.1/unity-catalog/current-metastore-assignment`  
- Feasibility create: `POST /api/2.1/unity-catalog/catalogs` with `storage_root` → 200, schema create → 200, force delete → 200  
- Cleanup: probe catalog `uc_sync_feasibility_*` deleted
