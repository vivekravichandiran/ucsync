# Object support matrix

| Object | Created on target? | Governance applied | Notes |
|---|---|---|---|
| Storage credential | ✅ (MI, from access-connector id; `create_storage_credentials`) | grants | Secret never exported; non-MI → MANUAL. Same name as source. |
| External location | ✅ (`create_external_locations`) | grants | Same name; URL path-rewritten to target; target credential from mapping. |
| Catalog | ✅ same name (`create_catalogs`) | grants, tags, ABAC | `MANAGED LOCATION` path-rewritten to target (kept, not stripped). |
| Schema | ✅ same name (`create_schemas`) | grants, tags, ABAC | |
| Volume (managed) | ✅ (`create_volumes`) | grants, tags | Files not copied. |
| External volume | ✅ (`create_volumes`) | grants, tags | `CREATE EXTERNAL VOLUME` at the path-rewritten target location; its covering external location is created too (from the mapping). Volume **files** are not copied. |
| Function (incl. mask/filter UDFs) | ✅ (`create_functions`) | grants | Captured from `information_schema.routines`+`.parameters` over the warehouse (lossless). Created before the masks/policies that reference them. |
| **Managed table (full definition, no data)** | ✅ (`create_tables`) | tags, ABAC, classic masks/filters (INLINE), grants | Full fidelity via `SHOW CREATE` **on the SQL warehouse** (`source_warehouse_id`): columns/types/nullability, comments, inline `MASK`/`WITH ROW FILTER`, user `TBLPROPERTIES`, partitioning, clustering, constraints (PK/CHECK), generated & identity columns. If `SHOW CREATE` fails after retries the object is a **hard `FAILURE`** (`DDL_CAPTURE_FAILED`) — no synthesized fallback. Data is out of scope. |
| External table | ✅ (`create_tables`) | grants | Path rewritten; requires a location mapping. `SHOW CREATE` on the warehouse (hard-fail, as above). |
| View / dynamic view | ✅ (`create_views`) | grants, tags | Definition from `SHOW CREATE` on the warehouse (hard-fail, as above). **Created on the SQL warehouse** (`import_warehouse_id`) at import — classic Spark errors on a view over a masked/row-filtered table. Fails naturally if a referenced object isn't present. |
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
