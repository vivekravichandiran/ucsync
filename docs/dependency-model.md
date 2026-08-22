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
8. Column masks / row filters (→ table **and** the mask/filter function)
9. Grants (→ securable)
10. Workspace bindings (→ catalog)

## Column masks & row filters

A column mask or row filter binds a table to a SQL function, so both the table
and the function must already exist before the binding is applied. Because
functions import after tables, `SHOW CREATE TABLE`'s inline `MASK` / `WITH ROW
FILTER` clauses are stripped from the captured CREATE DDL during migrate
(`strip_inline_policy_clauses`) and re-applied as `ALTER TABLE ... SET MASK` /
`SET ROW FILTER` statements in a dedicated **policy phase** that runs after every
object (tables and functions) has been created and before/around grants. The
statements are written to `policies/<TYPE>_<name>.sql` during export and
catalog-rewritten by the standard migrate pass. Re-runs treat an already-bound
policy as a skip.

## Topological sort

- Build adjacency from adapter-declared dependencies + SQL definition parse for views.
- Assign `dependency_level` (BFS depth) and stable `import_order` (level, object_type rank, full_name).
- Cycles → `MANUAL_ACTION_REQUIRED`, do not invent order.

## Object type rank (within level)

`STORAGE_CREDENTIAL` < `EXTERNAL_LOCATION` < `CATALOG` < `SCHEMA` < `VOLUME` < `TABLE` < `FUNCTION` < `MODEL` < `VIEW` < `MATERIALIZED_VIEW` < `GRANT` < `BINDING`
