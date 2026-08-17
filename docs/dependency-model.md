# Dependency Model

## Graph rules

Edges represent "must exist before":

1. Storage credential / service credential
2. External location (→ credential)
3. Catalog (→ managed storage mapping; optional external location for storage_root)
4. Schema (→ catalog)
5. Tables, managed volumes, functions, models (→ schema; external table/volume → external location)
6. Views / dynamic views (→ referenced tables/views/functions)
7. Materialized views (→ referenced objects + optional schedule config)
8. Grants (→ securable)
9. Workspace bindings (→ catalog)

## Topological sort

- Build adjacency from adapter-declared dependencies + SQL definition parse for views.
- Assign `dependency_level` (BFS depth) and stable `import_order` (level, object_type rank, full_name).
- Cycles → `MANUAL_ACTION_REQUIRED`, do not invent order.

## Object type rank (within level)

`STORAGE_CREDENTIAL` < `EXTERNAL_LOCATION` < `CATALOG` < `SCHEMA` < `VOLUME` < `TABLE` < `FUNCTION` < `MODEL` < `VIEW` < `MATERIALIZED_VIEW` < `GRANT` < `BINDING`
