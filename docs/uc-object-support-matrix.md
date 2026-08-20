# UC Object Support Matrix

Status legend:

| Status | Meaning |
|--------|---------|
| FULL | Inventory + export + import + validate via REST/SDK (SQL assist OK) |
| METADATA | Metadata sync only; secrets/artifacts manual |
| PARTIAL | Readable; import/validate gaps or hybrid SQL required |
| MANUAL | Mapping / human action required |
| OUT | Explicitly out of scope |
| TBD | Not present in probe sample; adapter stub only |

Mechanisms: **REST** = UC 2.1 API, **SDK** = Databricks SDK wrappers, **SQL** = warehouse/`SHOW`/`information_schema`.

| Object type | Inventory | Export | Import | Validate | Primary mechanism | `updated_at` (REST) | Notes |
|-------------|-----------|--------|--------|----------|-------------------|---------------------|-------|
| Catalog | FULL | FULL | FULL | FULL | REST | YES | Target CREATE needs mapped `storage_root` |
| Schema | FULL | FULL | FULL | FULL | REST | YES | Skip `information_schema` |
| Managed table | FULL | FULL | FULL | FULL | REST + SQL DDL | YES | Metadata only; no data copy |
| External table | FULL | FULL | PARTIAL | FULL | REST | YES | Remap location; else MANUAL_CONFIGURATION_REQUIRED |
| View | FULL | FULL | FULL | FULL | REST + SQL | YES | After dependencies |
| Dynamic view | PARTIAL | PARTIAL | PARTIAL | PARTIAL | SQL + REST | TBD | Capture definition when present |
| Metric view | FULL | FULL | FULL | FULL | REST + YAML DDL | YES/PARTIAL | Classified as `METRIC_VIEW`; export/import uses `CREATE VIEW ... WITH METRICS LANGUAGE YAML` because `SHOW CREATE TABLE` is unsupported |
| Materialized view | TBD | TBD | PARTIAL | PARTIAL | SQL + REST | TBD | Preserve schedule; `initial_refresh=false` |
| Streaming table | TBD | TBD | PARTIAL | PARTIAL | SQL + REST | TBD | No auto refresh |
| Managed volume | FULL | FULL | FULL | FULL | REST | YES | Map managed storage |
| External volume | FULL | FULL | PARTIAL | FULL | REST | YES | Depends on external location mapping |
| Function | FULL | FULL | FULL | FULL | REST | YES | 7 functions in source sample |
| Registered model | METADATA | METADATA | METADATA | PARTIAL | REST/MLflow | TBD | Artifacts = METADATA_ONLY |
| Storage credential | METADATA | METADATA | MANUAL | PARTIAL | REST | PARTIAL | Never export secrets; map name |
| Service credential | METADATA | METADATA | MANUAL | PARTIAL | REST | PARTIAL | Same as storage credential |
| External location | METADATA | METADATA | MANUAL | PARTIAL | REST | PARTIAL | Remap URL + credential |
| Connection | METADATA | METADATA | MANUAL | PARTIAL | REST | PARTIAL | Never export secrets |
| Foreign catalog | PARTIAL | PARTIAL | MANUAL | PARTIAL | REST | TBD | Depends on connection map |
| Share / Recipient / Provider | METADATA | METADATA | PARTIAL | PARTIAL | REST | TBD | APIs empty in probe |
| Grants | FULL | FULL | FULL | FULL | REST | N/A | After object create; principal map |
| Tags | PARTIAL | PARTIAL | PARTIAL | PARTIAL | SDK/SQL | N/A | List tags API 404 in probe |
| Row filter / column mask | FULL | FULL | FULL | PARTIAL | REST + SQL | N/A | Captured from REST `row_filter` / `columns[].mask`; replayed as `ALTER TABLE ... SET MASK` / `SET ROW FILTER` in a late `policies/` phase after all objects + functions exist |
| Workspace binding | FULL | FULL | FULL | FULL | REST | N/A | Remap workspace IDs |
| Owner | FULL | FULL | PARTIAL | FULL | REST | N/A | May need ALTER OWNER + principal map |
| Physical files / table data | OUT | OUT | OUT | OUT | — | — | Separate data migration |

## Adapter framework

Each row maps to `src/uc_sync/adapters/<type>.py` implementing:

- `list(parent) -> Iterable[UCObject]`
- `get(full_name) -> UCObject`
- `to_ddl(obj) -> str | None`
- `create(target, obj, mappings) -> ImportResult`
- `diff(source, target) -> ValidationStatus`

Unsupported paths must emit `MANUAL_ACTION_REQUIRED` or `UNSUPPORTED` into audit — never silent skip without status.
