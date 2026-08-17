# Permissions & Preflight

## Identity model

Production: **service principal** OAuth credentials in Databricks secret scopes (Key Vault-backed preferred).

Current probe: user PAT profiles `uc-source` / `uc-target` as `vivek.ravichandiran@databricks.com` in workspace **admins** on both sides. Secret scopes: **none configured**.

## Source workspace (inventory/export)

Minimum:

| Area | Need |
|------|------|
| UC browse | `USE CATALOG` / `USE SCHEMA` on selected objects (or ownership) |
| Metadata | Ability to `GET` catalogs/schemas/tables/volumes/functions |
| Grants visibility | Read permissions APIs on securables |
| Bindings | Read catalog bindings for isolated catalogs |
| SQL assist | `SELECT`/`SHOW CREATE` via warehouse or cluster SQL |
| REST | Token/SP authorized to UC 2.1 APIs |

Do **not** assume metastore admin is required for inventory of owned/granted objects. Metastore list APIs may require account admin (403 observed on `/metastores`).

## Target workspace (import/audit/volume)

| Area | Need |
|------|------|
| Metastore | `CREATE CATALOG` (or pre-create catalogs) |
| Storage | Rights to use target storage credential / external location for managed `storage_root` |
| Schema/table/volume/function | `CREATE` on parent + ownership or grant management |
| Grants | `MANAGE` / grant privileges after create |
| Bindings | Permission to update workspace bindings when isolation used |
| Volume | `READ VOLUME` / `WRITE VOLUME` on export path |
| Audit table | `CREATE TABLE` + `MODIFY` on audit schema |
| Job | Can run notebook job as SP |

### Observed metastore grants (probe)

CREATE_* privileges assigned to workspace admin groups:

- Source: `_workspace_admins_classic_stable_westus3_vk_7405618912789045`
- Target: `_workspace_admins_classic_stable_target_vk_7405609958717235`

User PAT is in `admins` → create catalog succeeded when `storage_root` supplied.

## Preflight checklist (notebook stage 1)

```
PRE-FLIGHT
Source Authentication         PASS/FAIL
Source Workspace              PASS/FAIL
Source UC Access              PASS/FAIL
Source Inventory Access       PASS/FAIL
Target Authentication         PASS/FAIL
Target Workspace              PASS/FAIL
Target UC Access              PASS/FAIL
Target Create Permissions     PASS/FAIL/WARNING
Target Grant Permissions      PASS/FAIL/WARNING
Export Volume                 PASS/FAIL
Audit Table Writable          PASS/FAIL
Credential Mapping            PASS/WARNING
External Location Mapping     PASS/WARNING
Managed Storage Mapping       PASS/WARNING
Principal Mapping             PASS/WARNING
Overall                       READY | BLOCKED
```

Critical FAIL → stop before any mutation.

## Secret handling

Never place credentials in widgets, notebooks, manifests, audit `metadata_json`, logs, or DDL.

Redact tokens from exceptions (`security.redact`).
