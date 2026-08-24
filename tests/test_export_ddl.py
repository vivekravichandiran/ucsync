"""Tests for SHOW CREATE / synthesized CREATE / GRANT DDL export packaging."""

from __future__ import annotations

from uc_sync.export import ExportService
from uc_sync.models import ObjectType, UCObject
from uc_sync.sql_ddl import (
    create_ddl_for_object,
    grant_statements_for_object,
    show_create_command,
)


class FakeSql:
    def show_create(self, object_type: str, full_name: str) -> str:
        if object_type == "FUNCTION":
            return f"CREATE FUNCTION {full_name}() RETURNS INT RETURN 1"
        return f"CREATE TABLE {full_name} (id INT) USING DELTA"


def test_grant_statements_include_privileges_and_owner():
    obj = UCObject(
        object_type=ObjectType.TABLE,
        name="t",
        full_name="c.s.t",
        grants=[
            {
                "principal": "alice@example.com",
                "principal_type": "USER",
                "privileges": ["SELECT", "MODIFY"],
            },
            {
                "principal": "data_engineers",
                "principal_type": "GROUP",
                "privileges": ["OWNER"],
            },
        ],
    )
    statements = grant_statements_for_object(obj)
    assert (
        "GRANT SELECT ON TABLE `c`.`s`.`t` TO `alice@example.com`;"
        in statements
    )
    assert (
        "ALTER TABLE `c`.`s`.`t` OWNER TO `data_engineers`;" in statements
    )


def test_show_create_command_for_tables_and_functions():
    assert (
        show_create_command(ObjectType.TABLE, "c.s.t")
        == "SHOW CREATE TABLE `c`.`s`.`t`"
    )
    assert (
        show_create_command(ObjectType.FUNCTION, "c.s.f")
        == "SHOW CREATE FUNCTION `c`.`s`.`f`"
    )


def test_synthesize_catalog_schema_volume_location_credential():
    catalog = create_ddl_for_object(
        UCObject(
            object_type=ObjectType.CATALOG,
            name="c",
            full_name="c",
            definition={"storage_root": "abfss://root", "comment": "demo"},
        )
    )
    assert "CREATE CATALOG IF NOT EXISTS `c`" in catalog
    assert "MANAGED LOCATION 'abfss://root'" in catalog

    schema = create_ddl_for_object(
        UCObject(
            object_type=ObjectType.SCHEMA,
            name="s",
            full_name="c.s",
            definition={"comment": "schema"},
        )
    )
    assert schema.startswith("CREATE SCHEMA IF NOT EXISTS `c`.`s`")

    volume = create_ddl_for_object(
        UCObject(
            object_type=ObjectType.VOLUME,
            name="v",
            full_name="c.s.v",
        )
    )
    assert "CREATE VOLUME IF NOT EXISTS `c`.`s`.`v`" in volume

    external_volume = create_ddl_for_object(
        UCObject(
            object_type=ObjectType.EXTERNAL_VOLUME,
            name="ev",
            full_name="c.s.ev",
            storage_location="abfss://data/vol",
        )
    )
    assert "CREATE EXTERNAL VOLUME IF NOT EXISTS `c`.`s`.`ev`" in external_volume
    assert "LOCATION 'abfss://data/vol'" in external_volume

    location = create_ddl_for_object(
        UCObject(
            object_type=ObjectType.EXTERNAL_LOCATION,
            name="ext",
            full_name="ext",
            storage_location="abfss://data",
            storage_credential_name="cred",
        )
    )
    assert "CREATE EXTERNAL LOCATION IF NOT EXISTS `ext`" in location
    assert "STORAGE CREDENTIAL `cred`" in location

    credential = create_ddl_for_object(
        UCObject(
            object_type=ObjectType.STORAGE_CREDENTIAL,
            name="cred",
            full_name="cred",
            credential_type="AZURE_MANAGED_IDENTITY",
            access_connector_id="/subscriptions/x/accessConnectors/y",
        )
    )
    assert "CREATE STORAGE CREDENTIAL IF NOT EXISTS `cred`" in credential
    assert "AZURE_MANAGED_IDENTITY" in credential


def test_synthesize_metric_view_from_yaml_definition():
    ddl = create_ddl_for_object(
        UCObject(
            object_type=ObjectType.METRIC_VIEW,
            name="sales_metrics",
            full_name="c.s.sales_metrics",
            table_type="METRIC_VIEW",
            definition={
                "view_definition": (
                    "version: 1.1\n"
                    "source: c.s.sales\n"
                    "dimensions:\n"
                    "  - name: Region\n"
                    "    expr: source.region\n"
                    "measures:\n"
                    "  - name: Revenue\n"
                    "    expr: SUM(source.revenue)"
                )
            },
        )
    )
    assert ddl.startswith(
        "CREATE OR REPLACE VIEW `c`.`s`.`sales_metrics` "
        "WITH METRICS LANGUAGE YAML AS $$"
    )
    assert "version: 1.1" in ddl
    assert ddl.endswith("$$;")


class _FailingShowCreate:
    """A source SQL executor that cannot reach the objects (mirrors direct-mode
    export running on the target, where source objects do not exist yet)."""

    def show_create(self, object_type: str, full_name: str) -> str:
        raise RuntimeError(
            f"[TABLE_OR_VIEW_NOT_FOUND] {full_name} cannot be found"
        )

    def execute(self, sql: str):
        raise RuntimeError("[TABLE_OR_VIEW_NOT_FOUND] cannot be found")


def test_show_create_failure_falls_back_to_synthesis_without_warning(tmp_path):
    """When SHOW CREATE is unavailable but the DDL can be synthesized from
    inventory, that is a clean success — not a SUCCESS_WITH_WARNINGS row. The
    scary TABLE_OR_VIEW_NOT_FOUND must never surface as an export warning."""
    objects = [
        UCObject(
            object_type=ObjectType.TABLE,
            name="orders",
            full_name="c.s.orders",
            definition={"table_type": "MANAGED",
                        "columns": [{"name": "id", "type_text": "int"}]},
        ),
    ]
    result = ExportService(
        str(tmp_path / "v"), "run1",
        sql_executor=_FailingShowCreate(),
        workspace_root=str(tmp_path / "w"),
    ).run(objects, dry_run=False)

    row = result["results"][0]
    assert row["status"] == "SUCCESS"
    assert not row["error_message"]
    assert result["ddl_by_source"].get("SYNTHESIZED") == 1
    assert "SHOW_CREATE" not in result["ddl_by_source"]


def test_export_writes_ddl_for_all_components(tmp_path):
    volume = tmp_path / "volume"
    workspace = tmp_path / "workspace"
    objects = [
        UCObject(
            object_type=ObjectType.CATALOG,
            name="c",
            full_name="c",
            definition={"storage_root": "abfss://root"},
            grants=[{"principal": "admins", "privileges": ["USE_CATALOG"]}],
        ),
        UCObject(
            object_type=ObjectType.SCHEMA,
            name="s",
            full_name="c.s",
            grants=[{"principal": "admins", "privileges": ["USE_SCHEMA"]}],
        ),
        UCObject(
            object_type=ObjectType.TABLE,
            name="orders",
            full_name="c.s.orders",
            definition={"table_type": "MANAGED"},
            grants=[{"principal": "readers", "privileges": ["SELECT"]}],
        ),
        UCObject(
            object_type=ObjectType.VOLUME,
            name="v",
            full_name="c.s.v",
        ),
        UCObject(
            object_type=ObjectType.EXTERNAL_LOCATION,
            name="ext",
            full_name="ext",
            storage_location="abfss://data",
            storage_credential_name="cred",
        ),
        UCObject(
            object_type=ObjectType.STORAGE_CREDENTIAL,
            name="cred",
            full_name="cred",
            credential_type="AZURE_MANAGED_IDENTITY",
            access_connector_id="/subscriptions/x/accessConnectors/y",
        ),
    ]

    result = ExportService(
        str(volume),
        "run1",
        sql_executor=FakeSql(),
        workspace_root=str(workspace),
    ).run(objects, dry_run=False)

    assert result["exported"] == 6
    assert result["ddl_files"] == 6
    assert result["grant_files"] == 3
    assert result["ddl_by_source"]["SHOW_CREATE"] == 1
    assert result["ddl_by_source"]["SYNTHESIZED"] >= 4

    root = volume / "run_run1" / "ddl"
    assert (root / "CATALOG_c.sql").exists()
    assert (root / "SCHEMA_c__s.sql").exists()
    assert (root / "TABLE_c__s__orders.sql").exists()
    assert (root / "VOLUME_c__s__v.sql").exists()
    assert (root / "EXTERNAL_LOCATION_ext.sql").exists()
    assert (root / "STORAGE_CREDENTIAL_cred.sql").exists()
    assert (root / "all_objects.sql").exists()
    assert (workspace / "ddl" / "all_objects.sql").exists()
    assert "CREATE CATALOG IF NOT EXISTS `c`" in (root / "CATALOG_c.sql").read_text()
    assert "CREATE TABLE c.s.orders" in (root / "TABLE_c__s__orders.sql").read_text()
