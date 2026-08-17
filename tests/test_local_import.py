from uc_sync.config import from_sources
from uc_sync.import_engine import ImportEngine
from uc_sync.models import ObjectType, UCObject


class FakeSql:
    def __init__(self):
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)

    def show_create(self, object_type, full_name):
        return (
            "CREATE VIEW `source`.`analytics`.`v_orders` AS "
            "SELECT * FROM `source`.`analytics`.`orders`"
        )


def _config(dry_run="false"):
    return from_sources(
        {
            "execution_mode": "LOCAL",
            "catalog_mapping_json": '{"source":"target"}',
            "dry_run": dry_run,
        }
    )


def test_local_import_rewrites_catalogs():
    sql = FakeSql()
    objects = [
        UCObject(ObjectType.CATALOG, "source", "source", catalog="source"),
        UCObject(
            ObjectType.SCHEMA,
            "analytics",
            "source.analytics",
            catalog="source",
            schema="analytics",
        ),
        UCObject(
            ObjectType.TABLE,
            "orders",
            "source.analytics.orders",
            catalog="source",
            schema="analytics",
        ),
        UCObject(
            ObjectType.VIEW,
            "v_orders",
            "source.analytics.v_orders",
            catalog="source",
            schema="analytics",
        ),
    ]
    results = ImportEngine(None, _config(), sql).run(objects)
    assert all(result.status == "SUCCESS" for result in results)
    assert "CREATE CATALOG IF NOT EXISTS `target`" in sql.statements
    assert "CREATE SCHEMA IF NOT EXISTS `target`.`analytics`" in sql.statements
    assert (
        "CREATE TABLE IF NOT EXISTS `target`.`analytics`.`orders` "
        "LIKE `source`.`analytics`.`orders`"
    ) in sql.statements
    assert any(
        "`target`.`analytics`.`v_orders`" in statement
        and "`target`.`analytics`.`orders`" in statement
        for statement in sql.statements
    )


def test_external_objects_without_mapping_fail_explicitly():
    obj = UCObject(
        ObjectType.EXTERNAL_TABLE,
        "ext",
        "source.analytics.ext",
        catalog="source",
        schema="analytics",
    )
    result = ImportEngine(None, _config(), FakeSql()).run([obj])[0]
    assert result.status == "ERROR"
    assert result.error_code == "LOCATION_MAPPING_MISSING"


def test_cross_workspace_keeps_catalog_name_without_local_mapping():
    cfg = from_sources(
        {
            "execution_mode": "CROSS_WORKSPACE",
            "catalogs": "source",
            "dry_run": "true",
        }
    )
    obj = UCObject(
        ObjectType.TABLE,
        "orders",
        "source.analytics.orders",
        catalog="source",
        schema="analytics",
    )
    result = ImportEngine(None, cfg, FakeSql()).run([obj])[0]
    assert result.target_full_name == obj.full_name
    assert result.status == "SKIPPED"


def test_cross_workspace_builds_managed_table_from_rest_metadata():
    cfg = from_sources(
        {
            "execution_mode": "CROSS_WORKSPACE",
            "catalogs": "source",
            "catalog_mapping_json": '{"source":"target"}',
            "dry_run": "false",
        }
    )
    obj = UCObject(
        ObjectType.TABLE,
        "orders",
        "source.analytics.orders",
        catalog="source",
        schema="analytics",
        definition={
            "data_source_format": "DELTA",
            "comment": "Orders",
            "columns": [
                {
                    "name": "order_id",
                    "type_text": "bigint",
                    "nullable": False,
                    "position": 0,
                },
                {
                    "name": "note",
                    "type_text": "string",
                    "nullable": True,
                    "position": 1,
                },
            ],
        },
        properties={"ucsync.fixture": "true", "delta.minReaderVersion": "3"},
    )
    sql = FakeSql()
    result = ImportEngine(None, cfg, sql).run([obj])[0]
    assert result.status == "SUCCESS"
    statement = sql.statements[0]
    assert "CREATE TABLE IF NOT EXISTS `target`.`analytics`.`orders`" in statement
    assert "`order_id` bigint NOT NULL" in statement
    assert "'ucsync.fixture' = 'true'" in statement
    assert "delta.minReaderVersion" not in statement


def test_cross_workspace_builds_view_and_function_from_rest_metadata():
    cfg = from_sources(
        {
            "execution_mode": "CROSS_WORKSPACE",
            "catalogs": "source",
            "catalog_mapping_json": '{"source":"target"}',
            "dry_run": "false",
        }
    )
    objects = [
        UCObject(
            ObjectType.VIEW,
            "v_orders",
            "source.analytics.v_orders",
            catalog="source",
            schema="analytics",
            definition={
                "view_definition": (
                    "SELECT * FROM `source`.`analytics`.`orders`"
                )
            },
        ),
        UCObject(
            ObjectType.FUNCTION,
            "add_one",
            "source.analytics.add_one",
            catalog="source",
            schema="analytics",
            definition={
                "input_params": {
                    "parameters": [
                        {
                            "name": "value",
                            "type_text": "bigint",
                            "position": 0,
                        }
                    ]
                },
                "full_data_type": "BIGINT",
                "routine_definition": "value + 1",
            },
        ),
    ]
    sql = FakeSql()
    results = ImportEngine(None, cfg, sql).run(objects)
    assert all(result.status == "SUCCESS" for result in results)
    assert any(
        "CREATE VIEW IF NOT EXISTS `target`.`analytics`.`v_orders`" in statement
        and "`target`.`analytics`.`orders`" in statement
        for statement in sql.statements
    )
    assert any(
        "CREATE OR REPLACE FUNCTION `target`.`analytics`.`add_one`" in statement
        and "(value bigint)" in statement
        and "RETURN value + 1" in statement
        for statement in sql.statements
    )
