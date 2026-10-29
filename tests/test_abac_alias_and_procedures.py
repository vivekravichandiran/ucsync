"""Regression tests for two import failures seen in a customer BU run (v1.2.1).

Bug 1 - an ABAC MATCH COLUMNS alias derived from a hyphenated column name
        (``AS mc_sales-fleet-vol-mtd``) was replayed unquoted → INVALID_IDENTIFIER,
        and the catalog-scoped policy failure dropped every fresh table fail-closed.
Bug 2 - SQL stored procedures (information_schema routine_type = 'PROCEDURE') were
        rebuilt as ``CREATE FUNCTION … RETURNS NULL RETURN BEGIN …`` →
        PARSE_SYNTAX_ERROR; and the import splitter cut a BEGIN … END body at its
        inner ';'.
"""
from uc_sync.governance import abac_policy_create_statement
from uc_sync.models import ObjectType, UCObject
from uc_sync.package_import import _split_statements
from uc_sync.rewrite import strip_managed_storage_clauses
from uc_sync.sql_ddl import format_ddl_file, function_ddl_from_information_schema


# --------------------------------------------------------------------------- Bug 1

def _mask_policy(match_columns, on_column):
    return UCObject(
        ObjectType.ABAC_POLICY, "p", "cat#policy:p",
        definition={
            "policy_name": "p", "policy_type": "COLUMN_MASK",
            "on_securable_type": "CATALOG", "on_securable": "cat",
            "function_name": "cat.sec.mask_double", "on_column": on_column,
            "to_principals": ["account users"], "except_principals": [],
            "match_columns": match_columns, "when_condition": "", "comment": "",
        },
    )


def test_hyphenated_match_columns_alias_is_backquoted():
    sql = abac_policy_create_statement(_mask_policy(
        ["has_tag('mask_double_tag') AS mc_sales-fleet-vol-mtd"],
        "mc_sales-fleet-vol-mtd",
    ))
    assert "MATCH COLUMNS has_tag('mask_double_tag') AS `mc_sales-fleet-vol-mtd`" in sql
    assert "ON COLUMN `mc_sales-fleet-vol-mtd`" in sql
    assert "AS mc_sales-fleet-vol-mtd" not in sql


def test_plain_and_prequoted_aliases_are_unchanged():
    sql = abac_policy_create_statement(_mask_policy(
        ["has_tag('a') AS mc_enroldate", "has_tag('b') AS `mc_cust-id`"], "mc_enroldate",
    ))
    assert "has_tag('a') AS mc_enroldate" in sql
    assert "has_tag('b') AS `mc_cust-id`" in sql
    assert "``" not in sql


def test_only_the_trailing_alias_is_quoted_not_as_inside_a_literal():
    sql = abac_policy_create_statement(_mask_policy(
        ["has_tag_value('k','x AS y') AS cust-id"], "cust-id",
    ))
    assert "has_tag_value('k','x AS y') AS `cust-id`" in sql


def test_match_columns_without_alias_is_left_alone():
    sql = abac_policy_create_statement(_mask_policy(["has_tag('k')"], ""))
    assert "MATCH COLUMNS has_tag('k')" in sql


def test_row_filter_using_columns_aliases_are_backquoted():
    pol = UCObject(
        ObjectType.ABAC_POLICY, "rf", "cat#policy:rf",
        definition={
            "policy_name": "rf", "policy_type": "ROW_FILTER",
            "on_securable_type": "CATALOG", "on_securable": "cat",
            "function_name": "cat.sec.region_filter",
            "to_principals": ["account users"], "except_principals": [],
            "match_columns": ["has_tag('r') AS sales-region", "has_tag('s') AS site"],
            "using_columns": "(sales-region, site)", "when_condition": "",
            "comment": "",
        },
    )
    sql = abac_policy_create_statement(pol)
    assert "MATCH COLUMNS has_tag('r') AS `sales-region`, has_tag('s') AS site" in sql
    assert "USING COLUMNS (`sales-region`, site)" in sql


# --------------------------------------------------------------------------- Bug 2

class FakeSql:
    """routines query → one routine row; parameters query → params."""

    def __init__(self, routine_row, param_rows):
        self.routine_row = routine_row
        self.param_rows = param_rows
        self.statements = []

    def execute(self, stmt):
        self.statements.append(stmt)
        if "information_schema.routines" in stmt:
            return [self.routine_row]
        if "information_schema.parameters" in stmt:
            return self.param_rows
        return []


# Shapes as information_schema returns them for a SQL procedure (verified live).
_PROC_BODY = (
    "BEGIN\n"
    "  DECLARE v_cnt INT DEFAULT 0;\n"
    "  SET v_cnt = (WITH t AS (SELECT 1 AS a) SELECT count(*) FROM t);\n"
    "  IF v_cnt > 0 THEN\n"
    "    SET p_out = CASE WHEN v_cnt > 1 THEN 'many' ELSE 'one' END;\n"
    "  END IF;\n"
    "END"
)
# routines: specific_name, data_type, full_data_type, routine_definition, routine_body,
#           external_language, is_deterministic, comment, routine_type, security_type
_PROC_ROW = ["sp_audit_logging", "NULL", "NULL", _PROC_BODY, "SQL", None, False,
             "audit proc", "PROCEDURE", "INVOKER"]
# parameters: name, full_data_type, data_type, mode, default, ordinal
_PROC_PARAMS = [
    ["in_mode", "string collate UTF8_BINARY", "STRING", "IN", None, 0],
    ["p_out", "string collate UTF8_BINARY", "STRING", "OUT", None, 1],
    ["p_note", "string", "STRING", "INOUT", None, 2],
    ["p_limit", "int", "INT", "IN", "5", 3],
]


def _proc_ddl():
    sql = FakeSql(_PROC_ROW, _PROC_PARAMS)
    return function_ddl_from_information_schema(sql, "cat.silver.sp_audit_logging"), sql


def test_procedure_is_rebuilt_as_create_procedure():
    ddl, _ = _proc_ddl()
    assert ddl.startswith("CREATE OR REPLACE PROCEDURE `cat`.`silver`.`sp_audit_logging`(")
    assert "FUNCTION" not in ddl
    assert "RETURNS" not in ddl and "RETURN BEGIN" not in ddl
    assert "LANGUAGE SQL" in ddl
    assert "SQL SECURITY INVOKER" in ddl
    assert "COMMENT 'audit proc'" in ddl
    assert ddl.endswith("AS " + _PROC_BODY + ";")


def test_procedure_keeps_parameter_modes_and_in_defaults():
    ddl, sql = _proc_ddl()
    assert (
        "(IN in_mode string collate UTF8_BINARY, OUT p_out string collate UTF8_BINARY, "
        "INOUT p_note string, IN p_limit int DEFAULT 5)"
    ) in ddl
    assert any("parameter_default" in s for s in sql.statements)


def test_procedure_without_parameters():
    row = list(_PROC_ROW)
    row[0] = "get_counts"
    ddl = function_ddl_from_information_schema(FakeSql(row, []), "cat.silver.get_counts")
    assert "PROCEDURE `cat`.`silver`.`get_counts`()\nLANGUAGE SQL" in ddl


def test_scalar_function_is_unaffected_by_procedure_branch():
    row = ["f1", "INT", "INT", "x + 1", "SQL", None, True, None, "FUNCTION", "DEFINER"]
    ddl = function_ddl_from_information_schema(
        FakeSql(row, [["x", "int", "INT", "IN", None, 0]]), "cat.fn.plus_one"
    )
    assert ddl == "CREATE OR REPLACE FUNCTION `cat`.`fn`.`plus_one`(x int) RETURNS INT RETURN x + 1;"


def test_exported_procedure_file_replays_as_one_statement():
    """Capture → export rewrite → import split must keep the BEGIN … END body whole."""
    ddl, _ = _proc_ddl()
    obj = UCObject(ObjectType.FUNCTION, "sp_audit_logging", "cat.silver.sp_audit_logging")
    text = format_ddl_file(obj, ddl, source="INFORMATION_SCHEMA",
                           command="information_schema.routines/parameters")
    text = strip_managed_storage_clauses(text, "FUNCTION")
    statements = _split_statements(text)
    assert len(statements) == 1
    assert "CREATE OR REPLACE PROCEDURE" in statements[0]
    assert statements[0].rstrip().endswith("END;")
    assert "collate" not in statements[0].lower()  # stripped for collation-less targets


# ------------------------------------------------------------ splitter: BEGIN … END

def test_splitter_keeps_nested_scripting_blocks_together():
    text = (
        "CREATE OR REPLACE PROCEDURE c.s.p(IN x INT) LANGUAGE SQL SQL SECURITY INVOKER AS\n"
        "lbl: BEGIN\n"
        "  DECLARE i INT DEFAULT 0;\n"
        "  WHILE i < x DO SET i = i + 1; END WHILE;\n"
        "  CASE WHEN i = 1 THEN SELECT 1; ELSE SELECT 2; END CASE;\n"
        "  BEGIN\n"
        "    SELECT CASE WHEN i > 0 THEN 'end' ELSE 'begin' END;\n"
        "  END;\n"
        "  LOOP SET i = i - 1; IF i = 0 THEN LEAVE lbl; END IF; END LOOP;\n"
        "END lbl;\n"
        "GRANT EXECUTE ON FUNCTION c.s.p TO `g`;\n"
    )
    statements = _split_statements(text)
    assert len(statements) == 2
    assert statements[0].endswith("END lbl;")
    assert statements[1] == "GRANT EXECUTE ON FUNCTION c.s.p TO `g`;"


def test_splitter_unchanged_for_tables_with_begin_end_columns():
    text = (
        "CREATE TABLE c.s.t (begin_ts TIMESTAMP, begin INT, end INT);\n"
        "ALTER TABLE c.s.t SET TAGS ('k'='v');\n"
    )
    assert len(_split_statements(text)) == 2


def test_splitter_unchanged_for_functions_with_case_end():
    text = (
        "CREATE OR REPLACE FUNCTION c.s.f(x INT) RETURNS STRING "
        "RETURN CASE WHEN x > 0 THEN 'pos' ELSE 'neg' END;\n"
        "ALTER FUNCTION c.s.f OWNER TO `u`;\n"
    )
    assert len(_split_statements(text)) == 2
