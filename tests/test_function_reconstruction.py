"""Regression tests for function DDL reconstruction from information_schema.

Surfaced by the ai27_ucsync_testcatalog E2E run: Python UDFs and table-valued
functions were rebuilt as scalar SQL UDFs → PARSE_SYNTAX_ERROR on import. Scalar
SQL UDFs must be unaffected.
"""
from uc_sync.sql_ddl import function_ddl_from_information_schema


class FakeSql:
    """Minimal sql executor: routines query → one routine row; parameters query → params."""

    def __init__(self, routine_row, param_rows):
        self.routine_row = routine_row
        self.param_rows = param_rows

    def execute(self, stmt):
        if "information_schema.routines" in stmt:
            return [self.routine_row]
        if "information_schema.parameters" in stmt:
            return self.param_rows
        return []


# routines cols: specific_name, data_type, full_data_type, routine_definition,
#                routine_body, external_language, is_deterministic, comment
# params cols:   parameter_name, full_data_type, data_type, parameter_mode, ordinal


def test_python_udf_uses_language_and_dollar_body():
    routine = ["py_normalize_1", "STRING", "STRING COLLATE UTF8_BINARY",
               "\nreturn (s or '').strip().lower()\n", "EXTERNAL", "Python", False,
               "Python UDF"]
    params = [["s", "string", "string", "IN", 1]]
    ddl = function_ddl_from_information_schema(FakeSql(routine, params),
                                               "cat.functions.py_normalize")
    assert "LANGUAGE PYTHON AS $$" in ddl
    assert "return (s or '').strip().lower()" in ddl
    assert ddl.rstrip().endswith("$$;")
    assert "RETURN return" not in ddl  # the old (broken) scalar-SQL shape


def test_tvf_emits_returns_table():
    routine = ["tvf_seq_1", "TABLE_TYPE", "(seq INT, label STRING COLLATE UTF8_BINARY)",
               "SELECT 1 AS seq, 'x' AS label", "SQL", None, True, "TVF example"]
    params = [["n", "int", "int", "IN", 0]]
    ddl = function_ddl_from_information_schema(FakeSql(routine, params),
                                               "cat.functions.tvf_seq")
    assert "RETURNS TABLE" in ddl
    assert "(seq INT" in ddl
    assert "RETURN SELECT" in ddl


def test_scalar_sql_udf_unchanged():
    routine = ["mask_ssn_1", "STRING", "STRING COLLATE UTF8_BINARY",
               "CASE WHEN is_account_group_member('admins') THEN v ELSE 'XXX' END",
               "SQL", None, True, "Mask SSN"]
    params = [["v", "string", "string", "IN", 1]]
    ddl = function_ddl_from_information_schema(FakeSql(routine, params),
                                               "cat.functions.mask_ssn")
    assert "RETURNS STRING" in ddl
    assert "RETURN CASE WHEN" in ddl
    assert "LANGUAGE" not in ddl
    assert "$$" not in ddl


def test_monitor_metric_table_is_report_only_everywhere():
    """Wiring guard: the new type is report-only in every classification set."""
    from uc_sync.delta import _ALWAYS_REPORT_ONLY_TYPES
    from uc_sync.export import _REPORT_ONLY_NO_DDL_TYPES, _HARD_FAIL_SHOW_CREATE_TYPES
    from uc_sync.report import _REPORT_ONLY_TYPES_REPORT, _INVENTORY_ONLY

    assert "MONITOR_METRIC_TABLE" in _ALWAYS_REPORT_ONLY_TYPES
    assert "MONITOR_METRIC_TABLE" in _REPORT_ONLY_NO_DDL_TYPES
    assert "MONITOR_METRIC_TABLE" in _REPORT_ONLY_TYPES_REPORT
    assert "MONITOR_METRIC_TABLE" in {t for t, _ in _INVENTORY_ONLY}
    # Streaming tables are now always report-only (no wasted SHOW CREATE).
    assert "STREAMING_TABLE" in _REPORT_ONLY_NO_DDL_TYPES
    assert "STREAMING_TABLE" not in _HARD_FAIL_SHOW_CREATE_TYPES
