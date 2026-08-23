"""Governed-tag + ABAC-policy DDL builder tests (offline)."""

from __future__ import annotations

from uc_sync.governance import (
    abac_policy_create_statement,
    tag_statements_for_object,
)
from uc_sync.models import ObjectType, UCObject


def test_tag_statements_object_and_column_levels():
    tbl = UCObject(
        ObjectType.TABLE, "employees", "c.hr.employees", catalog="c", schema="hr",
        tags={"ai27_uc_classification": "CONFIDENTIAL"},
        definition={"column_tags": {"ssn": {"ai27_uc_pii": "SSN"}}},
    )
    stmts = tag_statements_for_object(tbl)
    assert "ALTER TABLE `c`.`hr`.`employees` SET TAGS ('ai27_uc_classification' = 'CONFIDENTIAL');" in stmts
    assert (
        "ALTER TABLE `c`.`hr`.`employees` ALTER COLUMN `ssn` "
        "SET TAGS ('ai27_uc_pii' = 'SSN');"
    ) in stmts


def test_tag_statements_catalog_and_volume():
    cat = UCObject(ObjectType.CATALOG, "c", "c", tags={"k": "v"})
    assert tag_statements_for_object(cat) == ["ALTER CATALOG `c` SET TAGS ('k' = 'v');"]
    vol = UCObject(ObjectType.VOLUME, "vol", "c.s.vol", tags={"k": "v"})
    assert tag_statements_for_object(vol) == [
        "ALTER VOLUME `c`.`s`.`vol` SET TAGS ('k' = 'v');"
    ]


def test_abac_column_mask_create_with_except():
    pol = UCObject(
        ObjectType.ABAC_POLICY, "mask_email", "c.hr.t#policy:mask_email",
        definition={
            "policy_name": "mask_email", "policy_type": "COLUMN_MASK",
            "on_securable_type": "TABLE", "on_securable": "c.hr.t",
            "function_name": "c.sec.mask_email", "on_column": "c",
            "to_principals": ["account users"],
            "except_principals": ["svc@databricks.com"],
            "match_columns": ["has_tag_value('ai27_uc_pii','EMAIL') AS c"],
            "comment": "mask", "when_condition": "",
        },
    )
    sql = abac_policy_create_statement(pol)
    assert "CREATE POLICY `mask_email` ON TABLE `c`.`hr`.`t`" in sql
    assert "COLUMN MASK `c`.`sec`.`mask_email`" in sql
    assert "TO `account users`" in sql
    assert "EXCEPT `svc@databricks.com`" in sql
    assert "MATCH COLUMNS has_tag_value('ai27_uc_pii','EMAIL') AS c" in sql
    assert "ON COLUMN `c`" in sql


def test_abac_row_filter_create_with_using_columns_and_when():
    pol = UCObject(
        ObjectType.ABAC_POLICY, "rf", "c.hr.t#policy:rf",
        definition={
            "policy_name": "rf", "policy_type": "ROW_FILTER",
            "on_securable_type": "SCHEMA", "on_securable": "c.hr",
            "function_name": "c.sec.region_filter",
            "to_principals": ["account users"], "except_principals": [],
            "match_columns": ["has_tag_value('ai27_uc_row_access','restricted') AS region"],
            "using_columns": "region", "when_condition": "has_tag_value('x','y')",
            "comment": "",
        },
    )
    sql = abac_policy_create_statement(pol)
    assert "CREATE POLICY `rf` ON SCHEMA `c`.`hr`" in sql
    assert "ROW FILTER `c`.`sec`.`region_filter`" in sql
    assert "EXCEPT" not in sql
    assert "WHEN has_tag_value('x','y')" in sql
    assert "USING COLUMNS (region)" in sql
