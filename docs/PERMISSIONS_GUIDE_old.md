# UC Sync — Permissions Guide

The minimal, **catalog-scoped** privileges the UC Sync utility's two service
principals need. **Hard client constraint: neither SPN is ever a metastore or
account admin** — every grant below is issued by the **catalog owner** (and, for
external placement, the external-location owner). This guide states the asserted
minimum; `testing/permission_probe.py`, run **as the candidate SPN**, verifies it
live against your metastore and regenerates the operation→grant table.

> Align with the sibling Workspace Migration Utility, which ships the same kind of
> `PERMISSIONS_GUIDE` + graded preflight.

---

## 1. Source read SPN (Inventory + Export — read-only)

Runs `01_Inventory` → `02_Export` on the source workspace: enumerates objects,
captures `SHOW CREATE` DDL over a SQL warehouse, reads function bodies / tags / ABAC
from each catalog's own `information_schema`, and reads grants via the UC permissions
API.

```sql
-- Preferred (least-privilege, read-only): omit CREATE/MODIFY.
GRANT USE CATALOG, USE SCHEMA, SELECT, EXECUTE, READ VOLUME ON CATALOG <src> TO `<src_spn>`;
GRANT MANAGE ON CATALOG <src> TO `<src_spn>`;   -- REQUIRED to read grants/ACLs (see §3)
-- + CAN USE on the source SQL warehouse (SHOW CREATE + information_schema run there).

-- Simpler equivalent, if the client prefers: ALL PRIVILEGES + MANAGE ON CATALOG <src>.
```

The source SP is read-only, so the tighter set (no `CREATE`/`MODIFY`) is preferred
there; `MANAGE` is the one non-negotiable addition (nothing weaker reads ACLs).

## 2. Target run SPN (Import — creates objects + applies governance)

Runs `03_Import` on the target workspace. The **catalog and schemas are pre-created
by the customer** (Mode B); the SPN fills in contents + governance.

```sql
GRANT ALL PRIVILEGES ON CATALOG <tgt> TO `<tgt_spn>`;   -- create tables/views/functions/volumes under the pre-created schemas
GRANT MANAGE         ON CATALOG <tgt> TO `<tgt_spn>`;   -- apply grants + policies onto the pre-created (customer-owned) catalog/schema + created objects
GRANT APPLY TAG      ON CATALOG <tgt> TO `<tgt_spn>`;   -- apply governed tags (if not already covered)

-- External placement (only when the bundle has external tables/volumes), issued by the EL owner:
GRANT CREATE EXTERNAL TABLE, CREATE EXTERNAL VOLUME ON EXTERNAL LOCATION <el> TO `<tgt_spn>`;

-- Ops state tables (uc_sync_audit / uc_sync_state):
GRANT USE CATALOG, USE SCHEMA, CREATE TABLE, MODIFY, SELECT ON SCHEMA <ops>.<schema> TO `<tgt_spn>`;
-- + CAN USE on the import/target SQL warehouse (DDL + ABAC + views over masked tables).
```

The **standing (incremental) set is identical** — re-runs need nothing extra, and
the utility never modifies the SPN's own grants (see §4). If the client prefers
tighter than `ALL PRIVILEGES` on target, use the explicit
`CREATE TABLE, CREATE FUNCTION, CREATE VOLUME` (+ `CREATE SCHEMA` if ever needed) set
+ `MANAGE` + `APPLY TAG`.

---

## 3. Why these specific privileges (verified against Databricks docs)

- **`MANAGE` is the key privilege and is NOT part of `ALL PRIVILEGES`** — it must be
  granted explicitly *alongside* it. Reading **all** grants on a securable requires
  the owner, the parent's owner, `MANAGE`, or metastore admin — plain `SELECT` shows
  only your own grants. `MANAGE` on the catalog **cascades** to its schemas/tables,
  so it covers reading (source) and applying (target) grants **and** policies across
  everything under the catalog — including the pre-created catalog & schema.
- **`ALL PRIVILEGES` (cascaded from the catalog)** covers `USE CATALOG` /
  `USE SCHEMA` / `SELECT` / `EXECUTE` / `READ VOLUME` (source reads) and, on target,
  `CREATE TABLE/VIEW/FUNCTION/VOLUME`.

## 4. The utility never touches the run-as SPN's own grants

When replicating source ACLs onto target, the utility **excludes the run-as SPN as a
grantee** (it already holds catalog-scoped `ALL PRIVILEGES + MANAGE`, and its grants
on the *source* catalog are irrelevant to the target). No residual-grant
reconciliation, no revokes, no ownership gymnastics for the SPN.

## 5. External locations & storage credentials — no metastore admin needed

ELs/SCs are **metastore-level** securables, not under the catalog, so catalog-scoped
grants don't reach them. The utility deliberately **does not require the source SPN
to read source ELs/SCs**: external paths come from the object's own DDL `LOCATION`
(covered by `SELECT` / `ALL PRIVILEGES`) and are re-pointed by the operator-supplied
`external_locations.csv`. On target, placing an external object into a pre-created EL
needs `CREATE EXTERNAL TABLE` / `CREATE EXTERNAL VOLUME` **on that EL**, granted by
the **EL owner** — again no metastore admin.

> **Report note (expected, not a bug):** because catalog-scoped grants don't reach
> metastore-level SC/EL, the **Storage Credentials / External Locations sheets may be
> empty** in a catalog-scoped run. Source SC/EL discovery is **best-effort** (empty if
> the SPN lacks a privilege, never a failure). In the 3-column `external_locations.csv`
> **create** mode the utility still creates + reports SC/EL; in the 2-column BYO /
> Mode B they are customer prerequisites, so empty/skip is correct.

## 6. `information_schema` scope — VERIFY LIVE

Databricks docs: the per-catalog `<catalog>.information_schema` needs only
`USE CATALOG` + `SELECT`/`BROWSE` (covered above), **not** the `system` catalog. Some
environments have been observed to require central `system.information_schema` access.
Do not assume — the **permission probe proves it**. If it turns out `system` reads are
needed, that is a `SELECT` grant on the `system` schema issued by whoever owns
`system`; it does **not** make the SPN a metastore admin:

```sql
GRANT USE CATALOG ON CATALOG system TO `<src_spn>`;
GRANT USE SCHEMA ON SCHEMA system.information_schema TO `<src_spn>`;
GRANT SELECT ON <the needed system.information_schema views> TO `<src_spn>`;
```

---

## 7. Verify it live — the permission probe

Run **as the candidate SPN** (a CLI profile authenticated as that SP, not an admin):

```bash
# Source read SPN:
python3 testing/permission_probe.py --side source \
    --profile <spn_profile> --warehouse-id <src_wh> --catalog <src_catalog> \
    --emit-guide docs/PERMISSIONS_GUIDE.md

# Target run SPN (writes to a scratch table in a pre-created schema, then drops it):
python3 testing/permission_probe.py --side target \
    --profile <spn_profile> --warehouse-id <tgt_wh> --catalog <tgt_catalog> \
    --schema <pre_created_schema> --emit-guide docs/PERMISSIONS_GUIDE.md
```

Each operation the utility performs is exercised and recorded PASS/FAIL with the exact
grant that unblocks it. Remove one grant, re-run, and the probe shows exactly which
operation breaks — proving minimality. The appended tables below are the verified
result for your metastore.

<!-- The probe appends its verified operation→grant tables here. -->
