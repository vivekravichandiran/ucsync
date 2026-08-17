"""External-location and external-table path migration tests."""

from __future__ import annotations

from uc_sync.config import from_sources
from uc_sync.import_engine import ImportEngine
from uc_sync.inventory import InventoryService
from uc_sync.location_mapping import load_location_mapping_csv
from uc_sync.mapping import MappingResolver
from uc_sync.models import ObjectType, UCObject
from uc_sync.validation import ValidationService


SOURCE_ROOT = "abfss://source@account.dfs.core.windows.net/root"
TARGET_ROOT = "abfss://target@account.dfs.core.windows.net/migrated"


def _config(dry_run="false"):
    return from_sources(
        {
            "execution_mode": "LOCAL",
            "catalog_mapping_json": '{"source":"target"}',
            "dry_run": dry_run,
        },
        {
            "location_mappings": [
                {
                    "source_external_location": "source_ext",
                    "source_location": SOURCE_ROOT,
                    "target_external_location": "target_ext",
                    "target_location": TARGET_ROOT,
                    "target_credential": "target_cred",
                }
            ]
        },
    )


def _external_objects():
    location = UCObject(
        ObjectType.EXTERNAL_LOCATION,
        "source_ext",
        "source_ext",
        storage_location=SOURCE_ROOT,
        external_location_name="source_ext",
        definition={
            "url": SOURCE_ROOT,
            "credential_name": "source_cred",
            "comment": "Migrated storage",
        },
    )
    table = UCObject(
        ObjectType.EXTERNAL_TABLE,
        "orders",
        "source.analytics.orders",
        catalog="source",
        schema="analytics",
        table_type="EXTERNAL",
        data_source_format="DELTA",
        storage_location=f"{SOURCE_ROOT}/tables/orders",
        definition={
            "table_type": "EXTERNAL",
            "data_source_format": "DELTA",
            "storage_location": f"{SOURCE_ROOT}/tables/orders",
            "columns": [
                {
                    "name": "id",
                    "type_text": "BIGINT",
                    "nullable": False,
                    "position": 0,
                }
            ],
            "comment": "Orders",
        },
    )
    return location, table


class FakeSql:
    def __init__(self):
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)

    def show_create(self, *_args):
        raise AssertionError("SHOW CREATE is not needed for mapped external tables")


def test_csv_location_mapping_aliases_and_prefix_rewrite(tmp_path):
    path = tmp_path / "locations.csv"
    path.write_text(
        "source_external_location,source_url,target_external_location,"
        "target_url,target_storage_credential\n"
        f"source_ext,{SOURCE_ROOT},target_ext,{TARGET_ROOT},target_cred\n",
        encoding="utf-8",
    )

    mappings = load_location_mapping_csv(path)
    resolver = MappingResolver(
        {"location_mappings": [item.to_dict() for item in mappings]}
    )

    assert resolver.rewrite_location(
        f"{SOURCE_ROOT}/tables/orders"
    ) == f"{TARGET_ROOT}/tables/orders"
    assert resolver.external_location_mapping(
        "source_ext", SOURCE_ROOT, "source_cred"
    )["target_credential"] == "target_cred"


def test_inventory_fetches_authoritative_table_type_and_path():
    class Source:
        def paginate(self, *_args, **_kwargs):
            return iter(
                [
                    {
                        "name": "orders",
                        "full_name": "source.analytics.orders",
                    }
                ]
            )

        def get(self, path):
            assert path.endswith("/source.analytics.orders")
            return {
                "table_type": "EXTERNAL",
                "data_source_format": "DELTA",
                "storage_location": f"{SOURCE_ROOT}/tables/orders",
                "columns": [{"name": "id", "type_text": "BIGINT"}],
            }

    table = list(
        InventoryService(Source(), _config())._iter_tables(
            "source", "analytics"
        )
    )[0]

    assert table.object_type == ObjectType.EXTERNAL_TABLE
    assert table.table_type == "EXTERNAL"
    assert table.data_source_format == "DELTA"
    assert table.storage_location == f"{SOURCE_ROOT}/tables/orders"
    assert table.to_dict()["storage_location"].endswith("/tables/orders")


def test_import_creates_location_before_table_with_rewritten_path():
    sql = FakeSql()
    results = ImportEngine(None, _config(), sql).run(_external_objects())

    assert [result.status for result in results] == ["SUCCESS", "SUCCESS"]
    assert "CREATE EXTERNAL LOCATION IF NOT EXISTS `target_ext`" in sql.statements[0]
    assert f"URL '{TARGET_ROOT}'" in sql.statements[0]
    assert "STORAGE CREDENTIAL `target_cred`" in sql.statements[0]
    assert "CREATE TABLE IF NOT EXISTS `target`.`analytics`.`orders`" in sql.statements[1]
    assert f"LOCATION '{TARGET_ROOT}/tables/orders'" in sql.statements[1]
    assert results[1].source_location == f"{SOURCE_ROOT}/tables/orders"
    assert results[1].target_location == f"{TARGET_ROOT}/tables/orders"
    assert results[1].target_external_location == "target_ext"
    assert results[1].target_credential == "target_cred"


def test_external_table_without_mapping_fails_explicitly():
    _, table = _external_objects()
    cfg = from_sources(
        {
            "execution_mode": "LOCAL",
            "catalog_mapping_json": '{"source":"target"}',
            "dry_run": "false",
        }
    )

    result = ImportEngine(None, cfg, FakeSql()).run([table])[0]

    assert result.status == "ERROR"
    assert result.error_code == "LOCATION_MAPPING_MISSING"


def test_existing_external_table_must_match_mapped_path():
    _, table = _external_objects()

    class Target:
        def __init__(self, location):
            self.location = location

        def get(self, _path):
            return {"storage_location": self.location}

    matching = ImportEngine(
        Target(f"{TARGET_ROOT}/tables/orders"), _config(), FakeSql()
    ).run([table])[0]
    conflicting = ImportEngine(
        Target("abfss://wrong/path"), _config(), FakeSql()
    ).run([table])[0]

    assert matching.status == "SUCCESS"
    assert matching.action == "NOOP"
    assert conflicting.status == "ERROR"
    assert conflicting.error_code == "EXTERNAL_STORAGE_MAPPING_CONFLICT"


def test_validation_checks_target_location_and_credential():
    class Target:
        def get(self, path):
            if "/external-locations/" in path:
                return {
                    "name": "target_ext",
                    "url": TARGET_ROOT,
                    "credential_name": "target_cred",
                }
            return {
                "full_name": "target.analytics.orders",
                "table_type": "EXTERNAL",
                "storage_location": f"{TARGET_ROOT}/tables/orders",
            }

    results = ValidationService(Target(), _config()).compare(
        _external_objects()
    )

    assert [result.status for result in results] == ["MATCH", "MATCH"]
    assert results[0].expected_target_credential == "target_cred"
    assert results[1].expected_target_location.endswith("/tables/orders")
