# Databricks notebook source
# MAGIC %md
# MAGIC # UC Sync Test Fixture Generator
# MAGIC Creates isolated Unity Catalog metadata fixtures for integration tests.

# COMMAND ----------

dbutils.widgets.text("catalog", "ril_sandbox")
dbutils.widgets.text("schema_prefix", "ucsync_local")
dbutils.widgets.text("external_base", "")
dbutils.widgets.text("object_count", "10")
dbutils.widgets.text("apply_acls", "true")
dbutils.widgets.text(
    "acl_readers",
    "account users,data_analysts",
)
dbutils.widgets.text(
    "acl_writers",
    "Data Engineers",
)
dbutils.widgets.text(
    "acl_pii_readers",
    "pii_readers",
)

# COMMAND ----------

import json

catalog = dbutils.widgets.get("catalog").strip()
schema_prefix = dbutils.widgets.get("schema_prefix").strip()
external_base = dbutils.widgets.get("external_base").strip().rstrip("/")
object_count = int(dbutils.widgets.get("object_count"))
apply_acls = dbutils.widgets.get("apply_acls").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
}


def _split_principals(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


acl_readers = _split_principals(dbutils.widgets.get("acl_readers"))
acl_writers = _split_principals(dbutils.widgets.get("acl_writers"))
acl_pii_readers = _split_principals(dbutils.widgets.get("acl_pii_readers"))

if not catalog or not schema_prefix:
    raise ValueError("catalog and schema_prefix are required")
if object_count < 10:
    raise ValueError("object_count must be at least 10")


def q(value):
    return f"`{value.replace('`', '``')}`"


def full(schema, name=None):
    value = f"{q(catalog)}.{q(schema)}"
    return f"{value}.{q(name)}" if name else value


def principal_sql(name: str) -> str:
    return q(name)


results = []


def execute(object_type, full_name, statement):
    try:
        spark.sql(statement).collect()
        results.append(
            {"object_type": object_type, "full_name": full_name, "status": "SUCCESS"}
        )
    except Exception as exc:
        results.append(
            {
                "object_type": object_type,
                "full_name": full_name,
                "status": "ERROR",
                "error": str(exc),
            }
        )


def grant(object_type, full_name, privileges, securable_sql, principals):
    for principal in principals:
        execute(
            f"ACL_{object_type}",
            f"{full_name} -> {principal}",
            f"GRANT {privileges} ON {securable_sql} TO {principal_sql(principal)}",
        )


# Ten schemas exercise structural-object inventory/reporting. Leaf fixtures live
# in the first schema so dependencies remain easy to inspect.
schemas = [f"{schema_prefix}_{index:02d}" for index in range(1, object_count + 1)]
for schema in schemas:
    execute(
        "SCHEMA",
        f"{catalog}.{schema}",
        f"CREATE SCHEMA IF NOT EXISTS {full(schema)} "
        f"COMMENT 'UCSync integration fixture schema {schema}'",
    )

primary = schemas[0]
for index in range(1, object_count + 1):
    suffix = f"{index:02d}"
    table = f"managed_table_{suffix}"
    execute(
        "TABLE",
        f"{catalog}.{primary}.{table}",
        f"""
        CREATE TABLE IF NOT EXISTS {full(primary, table)} (
          id BIGINT COMMENT 'Fixture row identifier',
          object_name STRING COMMENT 'Fixture object name',
          created_at TIMESTAMP COMMENT 'Fixture creation timestamp'
        )
        USING DELTA
        COMMENT 'UCSync managed table fixture {suffix}'
        TBLPROPERTIES ('ucsync.fixture' = 'true', 'ucsync.sequence' = '{index}')
        """,
    )

    view = f"standard_view_{suffix}"
    execute(
        "VIEW",
        f"{catalog}.{primary}.{view}",
        f"""
        CREATE OR REPLACE VIEW {full(primary, view)}
        COMMENT 'UCSync standard view fixture {suffix}'
        AS SELECT id, object_name, created_at FROM {full(primary, table)}
        """,
    )

    dynamic_view = f"dynamic_view_{suffix}"
    execute(
        "DYNAMIC_VIEW",
        f"{catalog}.{primary}.{dynamic_view}",
        f"""
        CREATE OR REPLACE VIEW {full(primary, dynamic_view)}
        COMMENT 'UCSync identity-aware dynamic view fixture {suffix}'
        AS SELECT id, object_name, created_at, current_user() AS requesting_user
        FROM {full(primary, table)}
        WHERE current_user() IS NOT NULL
        """,
    )

    metric_view = f"metric_view_{suffix}"
    execute(
        "METRIC_VIEW",
        f"{catalog}.{primary}.{metric_view}",
        f"""
        CREATE OR REPLACE VIEW {full(primary, metric_view)}
        WITH METRICS
        LANGUAGE YAML
        AS $$
        version: 1.1
        comment: "UCSync metric view fixture {suffix}"
        source: {catalog}.{primary}.{table}
        dimensions:
          - name: Fixture ID
            expr: source.id
          - name: Object Name
            expr: source.object_name
        measures:
          - name: Row Count
            expr: COUNT(1)
        $$
        """,
    )

    function = f"scalar_function_{suffix}"
    execute(
        "FUNCTION",
        f"{catalog}.{primary}.{function}",
        f"""
        CREATE OR REPLACE FUNCTION {full(primary, function)}(value BIGINT)
        RETURNS BIGINT
        COMMENT 'UCSync scalar SQL function fixture {suffix}'
        RETURN value + {index}
        """,
    )

    volume = f"managed_volume_{suffix}"
    execute(
        "VOLUME",
        f"{catalog}.{primary}.{volume}",
        f"CREATE VOLUME IF NOT EXISTS {full(primary, volume)} "
        f"COMMENT 'UCSync managed volume fixture {suffix}'",
    )

    if external_base:
        external_table = f"external_table_{suffix}"
        execute(
            "EXTERNAL_TABLE",
            f"{catalog}.{primary}.{external_table}",
            f"""
            CREATE TABLE IF NOT EXISTS {full(primary, external_table)} (
              id BIGINT,
              object_name STRING,
              created_at TIMESTAMP
            )
            USING DELTA
            LOCATION '{external_base}/ucsync/{schema_prefix}/tables/{external_table}'
            COMMENT 'UCSync external table fixture {suffix}'
            """,
        )

        external_volume = f"external_volume_{suffix}"
        execute(
            "EXTERNAL_VOLUME",
            f"{catalog}.{primary}.{external_volume}",
            f"""
            CREATE EXTERNAL VOLUME IF NOT EXISTS {full(primary, external_volume)}
            LOCATION '{external_base}/ucsync/{schema_prefix}/volumes/{external_volume}'
            COMMENT 'UCSync external volume fixture {suffix}'
            """,
        )

# Sample ACLs so export/inventory exercises privilege sync paths.
if apply_acls:
    grant(
        "CATALOG",
        catalog,
        "USE CATALOG",
        f"CATALOG {q(catalog)}",
        sorted(set(acl_readers + acl_writers + acl_pii_readers)),
    )
    for schema in schemas:
        grant(
            "SCHEMA",
            f"{catalog}.{schema}",
            "USE SCHEMA",
            f"SCHEMA {full(schema)}",
            sorted(set(acl_readers + acl_writers + acl_pii_readers)),
        )
        grant(
            "SCHEMA",
            f"{catalog}.{schema}",
            "CREATE TABLE, CREATE VOLUME, CREATE FUNCTION",
            f"SCHEMA {full(schema)}",
            acl_writers,
        )

    for index in range(1, object_count + 1):
        suffix = f"{index:02d}"
        table = f"managed_table_{suffix}"
        grant(
            "TABLE",
            f"{catalog}.{primary}.{table}",
            "SELECT",
            f"TABLE {full(primary, table)}",
            acl_readers,
        )
        grant(
            "TABLE",
            f"{catalog}.{primary}.{table}",
            "SELECT, MODIFY",
            f"TABLE {full(primary, table)}",
            acl_writers,
        )

        for view_kind, view_name in (
            ("VIEW", f"standard_view_{suffix}"),
            ("DYNAMIC_VIEW", f"dynamic_view_{suffix}"),
            ("METRIC_VIEW", f"metric_view_{suffix}"),
        ):
            grant(
                view_kind,
                f"{catalog}.{primary}.{view_name}",
                "SELECT",
                f"VIEW {full(primary, view_name)}",
                acl_readers,
            )

        function = f"scalar_function_{suffix}"
        grant(
            "FUNCTION",
            f"{catalog}.{primary}.{function}",
            "EXECUTE",
            f"FUNCTION {full(primary, function)}",
            sorted(set(acl_readers + acl_writers)),
        )

        volume = f"managed_volume_{suffix}"
        grant(
            "VOLUME",
            f"{catalog}.{primary}.{volume}",
            "READ VOLUME",
            f"VOLUME {full(primary, volume)}",
            acl_readers,
        )
        grant(
            "VOLUME",
            f"{catalog}.{primary}.{volume}",
            "READ VOLUME, WRITE VOLUME",
            f"VOLUME {full(primary, volume)}",
            acl_writers,
        )

        if external_base:
            external_table = f"external_table_{suffix}"
            grant(
                "EXTERNAL_TABLE",
                f"{catalog}.{primary}.{external_table}",
                "SELECT",
                f"TABLE {full(primary, external_table)}",
                acl_readers,
            )
            grant(
                "EXTERNAL_TABLE",
                f"{catalog}.{primary}.{external_table}",
                "SELECT, MODIFY",
                f"TABLE {full(primary, external_table)}",
                acl_writers,
            )
            # Every other external table also gets a PII-reader grant so
            # inventory/permission reports have a distinct principal pattern.
            if index % 2 == 0 and acl_pii_readers:
                grant(
                    "EXTERNAL_TABLE",
                    f"{catalog}.{primary}.{external_table}",
                    "SELECT",
                    f"TABLE {full(primary, external_table)}",
                    acl_pii_readers,
                )

            external_volume = f"external_volume_{suffix}"
            grant(
                "EXTERNAL_VOLUME",
                f"{catalog}.{primary}.{external_volume}",
                "READ VOLUME",
                f"VOLUME {full(primary, external_volume)}",
                acl_readers,
            )
            grant(
                "EXTERNAL_VOLUME",
                f"{catalog}.{primary}.{external_volume}",
                "READ VOLUME, WRITE VOLUME",
                f"VOLUME {full(primary, external_volume)}",
                acl_writers,
            )

summary = {
    "catalog": catalog,
    "schemas": schemas,
    "primary_schema": primary,
    "object_count": object_count,
    "external_objects_requested": bool(external_base),
    "acls_requested": apply_acls,
    "acl_readers": acl_readers,
    "acl_writers": acl_writers,
    "acl_pii_readers": acl_pii_readers,
    "created": sum(row["status"] == "SUCCESS" for row in results),
    "failed": sum(row["status"] == "ERROR" for row in results),
    "by_type": {},
    "errors": [row for row in results if row["status"] == "ERROR"],
}
for row in results:
    if row["status"] == "SUCCESS":
        summary["by_type"][row["object_type"]] = (
            summary["by_type"].get(row["object_type"], 0) + 1
        )

object_failed = sum(
    1
    for row in results
    if row["status"] == "ERROR" and not str(row["object_type"]).startswith("ACL_")
)
acl_failed = sum(
    1
    for row in results
    if row["status"] == "ERROR" and str(row["object_type"]).startswith("ACL_")
)
summary["object_failed"] = object_failed
summary["acl_failed"] = acl_failed

print(json.dumps(summary, indent=2))
if object_failed:
    raise RuntimeError(json.dumps(summary))
if acl_failed:
    print(
        f"WARNING: {acl_failed} ACL grant(s) failed; objects were created successfully"
    )
dbutils.notebook.exit(json.dumps(summary))
