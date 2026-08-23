# Object support matrix

| Object | Created on target? | Governance applied | Notes |
|---|---|---|---|
| Storage credential | ✅ (MI, from access-connector id; `create_storage_credentials`) | grants | Secret never exported; non-MI → MANUAL. Same name as source. |
| External location | ✅ (`create_external_locations`) | grants | Same name; URL path-rewritten to target; target credential from mapping. |
| Catalog | ✅ same name (`create_catalogs`) | grants, tags, ABAC | `MANAGED LOCATION` path-rewritten to target (kept, not stripped). |
| Schema | ✅ same name (`create_schemas`) | grants, tags, ABAC | |
| Volume (managed) | ✅ (`create_volumes`) | grants, tags | Files not copied. |
| External volume | inventory-only | — | External storage registration not duplicated. |
| Function (incl. mask/filter UDFs) | ✅ (`create_functions`) | grants | Created before the masks/policies that reference them. |
| **Managed table (full definition, no data)** | ✅ (`create_tables`) | tags, ABAC, classic masks/filters, grants | Full fidelity via `SHOW CREATE`: columns/types/nullability, comments, user `TBLPROPERTIES`, partitioning, clustering, constraints (PK/CHECK), generated & identity columns. Data is out of scope. |
| External table | ✅ (`create_tables`) | grants | Path rewritten; requires a location mapping. |
| View / dynamic view | ✅ (`create_views`) | grants, tags | Definition from `SHOW CREATE`. Pending if a referenced object isn't present yet. |
| Metric view | ✅ (`create_views`) | grants | YAML definition replayed. |
| Materialized view / streaming table | ❌ (pipeline-backed) | governance applied once they exist | No create path. |
| **Governed tags (assignments)** | — | ✅ `SET TAGS` at catalog/schema/table/column/volume | Definition must exist at account level (else `GOVERNANCE_PREREQ_MISSING`). |
| **ABAC policies** | ✅ per securable (`create_abac_policies`) | ✅ | `CREATE POLICY` verbatim incl. source `EXCEPT`; column mask + row filter. |
| **Classic column masks / row filters** | — | ✅ applied as found (`apply_masks_row_filters`) | Rejected on CHECK-constraint tables (UC limitation). |
| Grants / ownership | — | ✅ replayed as-is, last | Identities assumed present on target; additive on re-run. |
| Registered models, vector indexes | ❌ out of scope | — | — |
| Connections / shares / recipients / providers | inventory-only, flagged MANUAL | — | Carry remote secrets/endpoints. |

**Out of scope entirely:** table **data** (Deep Clone in the data-migration
utility), governed **tag definitions** (account level), identities/principal
remapping (assumed present), and **removals** (additive-only — reported, never
applied).
