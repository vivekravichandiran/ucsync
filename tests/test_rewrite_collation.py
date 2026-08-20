"""Regression tests for stripping the table-level COLLATION clause.

Newer Databricks runtimes emit `COLLATION '<name>'` in SHOW CREATE TABLE output
(e.g. ``USING delta\nCOLLATION 'UTF8_BINARY'``). Older SQL parsers reject that
standalone clause, so replaying the captured DDL fails with
``PARSE_SYNTAX_ERROR at or near 'COLLATION'``. The migrate step must drop it.
"""

from __future__ import annotations

from uc_sync.rewrite import (
    strip_inline_collate,
    strip_managed_storage_clauses,
    strip_reserved_table_properties,
)


# Real DDL captured via SHOW CREATE TABLE on a collation-emitting runtime.
_CAPTURED_TABLE_DDL = """CREATE TABLE ai27_uctest_target.sales.products (
  id INT,
  name STRING,
  category STRING,
  price DECIMAL(10,2))
USING delta
COLLATION 'UTF8_BINARY'
TBLPROPERTIES (
  'delta.enableDeletionVectors' = 'true',
  'delta.minReaderVersion' = '3',
  'delta.minWriterVersion' = '7');"""


def test_table_collation_clause_is_stripped():
    out = strip_managed_storage_clauses(_CAPTURED_TABLE_DDL, "TABLE")
    assert "COLLATION" not in out
    # Surrounding DDL is preserved and remains valid.
    assert "USING delta" in out
    assert "CREATE TABLE ai27_uctest_target.sales.products" in out
    # This fixture's TBLPROPERTIES were all delta.* internals, so the whole
    # clause is dropped by strip_reserved_table_properties.
    assert "TBLPROPERTIES" not in out


def test_double_quoted_collation_clause_is_stripped():
    ddl = 'CREATE TABLE c.s.t (id INT)\nUSING delta\nCOLLATION "UTF8_LCASE"\nTBLPROPERTIES ()'
    out = strip_managed_storage_clauses(ddl, "TABLE")
    assert "COLLATION" not in out
    assert "USING delta" in out


def test_inline_collate_is_stripped_from_function_ddl():
    # `SHOW CREATE FUNCTION` is unavailable on some runtimes, so function DDL is
    # synthesized from catalog metadata whose type_text carries the source
    # collation. A target that hasn't enabled collation rejects it with
    # UNSUPPORTED_FEATURE.COLLATION (0A000), so the qualifier must be dropped
    # from both the parameter type and the return type.
    fn = (
        "CREATE FUNCTION sec.mask_email(v string collate UTF8_BINARY) "
        "RETURNS STRING COLLATE UTF8_BINARY RETURN v"
    )
    out = strip_managed_storage_clauses(fn, "FUNCTION")
    assert "COLLATE" not in out.upper()
    assert out == "CREATE FUNCTION sec.mask_email(v string) RETURNS STRING RETURN v"


def test_inline_collate_is_stripped_from_table_columns():
    col = "CREATE TABLE c.s.t (name STRING COLLATE UTF8_BINARY)\nUSING delta"
    out = strip_managed_storage_clauses(col, "TABLE")
    assert "COLLATE" not in out.upper()
    assert "name STRING" in out
    assert "USING delta" in out


def test_inline_collate_helper_handles_quoted_name_and_leaves_collation():
    # Backtick-quoted collation names are stripped too.
    assert strip_inline_collate("v STRING COLLATE `UTF8_LCASE`") == "v STRING"
    # The table-level COLLATION '<name>' clause is NOT touched by this helper
    # (COLLATE is only matched when followed by whitespace, COLLATION never is).
    assert strip_inline_collate("USING delta COLLATION 'UTF8_BINARY'") == (
        "USING delta COLLATION 'UTF8_BINARY'"
    )


def test_collation_strip_is_idempotent_and_noop_without_clause():
    plain = "CREATE TABLE c.s.t (id INT)\nUSING delta"
    assert strip_managed_storage_clauses(plain, "TABLE") == plain
    once = strip_managed_storage_clauses(_CAPTURED_TABLE_DDL, "TABLE")
    assert strip_managed_storage_clauses(once, "TABLE") == once


# ---- reserved delta.* TBLPROPERTIES stripping ----------------------------

_FULL_TBLPROPS = """CREATE TABLE c.s.t (id INT)
USING delta
COLLATION 'UTF8_BINARY'
TBLPROPERTIES (
  'delta.enableDeletionVectors' = 'true',
  'delta.feature.rowTracking' = 'supported',
  'delta.minReaderVersion' = '3',
  'delta.minWriterVersion' = '7',
  'delta.rowTracking.materializedRowIdColumnName' = '_row-id-col-4d93',
  'delta.rowTracking.materializedRowCommitVersionColumnName' = '_row-commit-84c0',
  'my.business.tag' = 'gold');"""


def test_reserved_delta_properties_are_removed():
    out = strip_managed_storage_clauses(_FULL_TBLPROPS, "TABLE")
    assert "delta." not in out            # every reserved key gone
    assert "COLLATION" not in out         # collation gone too


def test_user_defined_properties_are_preserved():
    out = strip_managed_storage_clauses(_FULL_TBLPROPS, "TABLE")
    assert "'my.business.tag' = 'gold'" in out
    assert "TBLPROPERTIES" in out


def test_tblproperties_clause_dropped_when_only_delta_keys():
    only_delta = (
        "CREATE TABLE c.s.t (id INT)\nUSING delta\n"
        "TBLPROPERTIES (\n  'delta.enableRowTracking' = 'true')"
    )
    out = strip_reserved_table_properties(only_delta)
    assert "TBLPROPERTIES" not in out
    assert "USING delta" in out


def test_property_strip_leaves_view_collation_property_untouched():
    # View TBLPROPERTIES carry a non-delta 'collation' key that is valid to keep.
    view = (
        "CREATE VIEW c.s.v AS SELECT 1\n"
        "TBLPROPERTIES (\n  'collation' = 'UTF8_BINARY')"
    )
    out = strip_reserved_table_properties(view)
    assert "'collation' = 'UTF8_BINARY'" in out
