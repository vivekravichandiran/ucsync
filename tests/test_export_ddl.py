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


def test_synthesized_table_ddl_emits_inline_mask_and_row_filter():
    """Plan P2-D (defense-in-depth): the synthesizer emits inline column MASK and
    table-level WITH ROW FILTER, so a rebuild can never silently strip classic
    protection."""
    obj = UCObject(
        object_type=ObjectType.TABLE,
        name="employees",
        full_name="c.hr.employees",
        definition={
            "columns": [
                {"name": "ssn", "position": 0, "type_text": "STRING"},
                {"name": "val", "position": 1, "type_text": "STRING"},
                {"name": "dept", "position": 2, "type_text": "STRING"},
            ],
            "column_masks": [
                {"column_name": "ssn", "function_name": "c.sec.mask_ssn",
                 "using_column_names": []},
                {"column_name": "val", "function_name": "c.sec.mask_region",
                 "using_column_names": ["region"]},
            ],
            "row_filter": {"function_name": "c.sec.dept_filter",
                           "input_column_names": ["dept"]},
        },
    )
    ddl = create_ddl_for_object(obj)
    assert "`ssn` STRING MASK `c`.`sec`.`mask_ssn`" in ddl
    assert (
        "`val` STRING MASK `c`.`sec`.`mask_region` USING COLUMNS (`region`)" in ddl
    )
    assert "WITH ROW FILTER `c`.`sec`.`dept_filter` ON (`dept`)" in ddl


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
    """A source SQL executor whose SHOW CREATE fails (models a warehouse that could
    not capture a governed table after retries)."""

    def show_create(self, object_type: str, full_name: str) -> str:
        raise RuntimeError(
            f"[TABLE_OR_VIEW_NOT_FOUND] {full_name} cannot be found"
        )

    def execute(self, sql: str):
        raise RuntimeError("[TABLE_OR_VIEW_NOT_FOUND] cannot be found")


def test_show_create_failure_is_hard_failure_no_synth(tmp_path):
    """Plan P2-A: for the table/view family SHOW CREATE is the ONLY full-fidelity
    source. If it fails after retries the object is a HARD FAILURE — never a
    synthesized rebuild (which would silently drop masks / row filters /
    constraints). No DDL file is written and the run does not silently proceed."""
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
    assert row["status"] == "ERROR"
    assert row["error_code"] == "DDL_CAPTURE_FAILED"
    # No synthesized fallback for a table, and nothing exported.
    assert "SYNTHESIZED" not in result["ddl_by_source"]
    assert result["ddl_files"] == 0
    assert result["exported"] == 0


def test_table_missing_warehouse_is_hard_failure(tmp_path):
    """A table with no SQL executor at all is also a hard capture failure — DDL
    capture is warehouse-only for the table/view family."""
    objects = [
        UCObject(
            object_type=ObjectType.TABLE,
            name="orders",
            full_name="c.s.orders",
            definition={"columns": [{"name": "id", "type_text": "int"}]},
        ),
    ]
    result = ExportService(
        str(tmp_path / "v"), "run1", workspace_root=str(tmp_path / "w"),
    ).run(objects, dry_run=False)
    row = result["results"][0]
    assert row["status"] == "ERROR"
    assert row["error_code"] == "DDL_CAPTURE_FAILED"


class _RoutinesSql:
    """A warehouse executor answering information_schema.routines/parameters for a
    scalar UDF ``c.sec.mask_ssn(v STRING, salt INT) RETURNS STRING``."""

    def execute(self, sql: str):
        low = sql.lower()
        if "information_schema.routines" in low:
            # specific_name, data_type, full_data_type, routine_definition,
            # routine_body, is_deterministic, comment
            return [[
                "mask_ssn_1", "STRING", "STRING", "'***'", "SQL", "YES",
                "masks ssn",
            ]]
        if "information_schema.parameters" in low:
            # parameter_name, full_data_type, data_type, parameter_mode, ordinal
            return [
                [None, "STRING", "STRING", "OUT", 0],   # the RETURN row — skipped
                ["v", "STRING", "STRING", "IN", 1],
                ["salt", "INT", "INT", "IN", 2],
            ]
        return []


def test_function_captured_from_information_schema(tmp_path):
    """Plan P2-A: functions are captured warehouse-only from information_schema and
    reassembled into a correct CREATE FUNCTION (params in order, return, body,
    comment) — not via SHOW CREATE FUNCTION (unsupported in DBSQL)."""
    obj = UCObject(
        object_type=ObjectType.FUNCTION, name="mask_ssn",
        full_name="c.sec.mask_ssn",
    )
    result = ExportService(
        str(tmp_path / "v"), "run1", sql_executor=_RoutinesSql(),
        workspace_root=str(tmp_path / "w"),
    ).run([obj], dry_run=False)

    assert result["ddl_by_source"].get("INFORMATION_SCHEMA") == 1
    ddl = (tmp_path / "v" / "run_run1" / "ddl" / "FUNCTION_c__sec__mask_ssn.sql").read_text()
    assert "CREATE FUNCTION IF NOT EXISTS `c`.`sec`.`mask_ssn`" in ddl
    assert "`v` STRING, `salt` INT" in ddl or "v STRING, salt INT" in ddl
    assert "RETURNS STRING" in ddl
    assert "RETURN '***'" in ddl
    assert "OUT" not in ddl  # the RETURN row is not emitted as a parameter


def test_function_info_schema_failure_falls_back_to_synthesis(tmp_path):
    """A function whose information_schema read fails falls back to synthesizing
    from inventory (lossless — functions carry no masks), never a hard failure."""
    class _Boom:
        def execute(self, sql: str):
            raise RuntimeError("boom")

    obj = UCObject(
        object_type=ObjectType.FUNCTION, name="f", full_name="c.sec.f",
        definition={
            "input_params": {"parameters": [
                {"name": "v", "type_text": "STRING", "position": 0}]},
            "data_type": "STRING",
            "routine_definition": "'x'",
        },
    )
    result = ExportService(
        str(tmp_path / "v"), "run1", sql_executor=_Boom(),
        workspace_root=str(tmp_path / "w"),
    ).run([obj], dry_run=False)
    assert result["results"][0]["status"] == "SUCCESS"
    assert result["ddl_by_source"].get("SYNTHESIZED") == 1


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
