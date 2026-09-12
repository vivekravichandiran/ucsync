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
    # Bug #7: only the un-replayable keys (min reader/writer versions) are dropped;
    # the replayable delta.enableDeletionVectors is preserved, so the clause stays.
    assert "'delta.enableDeletionVectors' = 'true'" in out
    assert "minReaderVersion" not in out and "minWriterVersion" not in out


def test_double_quoted_collation_clause_is_stripped():
    ddl = 'CREATE TABLE c.s.t (id INT)\nUSING delta\nCOLLATION "UTF8_LCASE"\nTBLPROPERTIES ()'
    out = strip_managed_storage_clauses(ddl, "TABLE")
    assert "COLLATION" not in out
    assert "USING delta" in out


# Real DDL from a newer runtime: the table-level default collation is emitted as
# `DEFAULT COLLATION <UNQUOTED_IDENT>` (not the older quoted `COLLATION '<name>'`),
# which a target without collation rejects with PARSE_SYNTAX_ERROR at 'DEFAULT'.
_CAPTURED_DEFAULT_COLLATION_DDL = """CREATE TABLE ai27_uc_finance.gl.accounts (
  account_id BIGINT COMMENT 'id',
  name STRING COLLATE UTF8_BINARY COMMENT 'name',
  account_number STRING COLLATE UTF8_BINARY COMMENT 'acct # (ABAC-masked)',
  balance DECIMAL(18,2) COMMENT 'balance')
USING delta
COMMENT 'GL accounts (managed)'
DEFAULT COLLATION UTF8_BINARY
TBLPROPERTIES (
  'ai27_uc.fixture' = 'true')"""


def test_default_collation_unquoted_clause_is_stripped():
    out = strip_managed_storage_clauses(_CAPTURED_DEFAULT_COLLATION_DDL, "TABLE")
    assert "COLLATION" not in out.upper()      # table-level clause gone
    assert "COLLATE" not in out.upper()        # inline column qualifiers gone
    assert "USING delta" in out
    assert "COMMENT 'GL accounts (managed)'" in out
    # user-defined property survives (delta.* internals stripped separately)
    assert "'ai27_uc.fixture' = 'true'" in out


def test_default_collation_also_stripped_for_schema_ddl():
    ddl = "CREATE SCHEMA c.s\nDEFAULT COLLATION UTF8_LCASE\nWITH DBPROPERTIES ()"
    out = strip_managed_storage_clauses(ddl, "SCHEMA")
    assert "COLLATION" not in out.upper()


def test_external_table_collation_stripped_but_location_kept():
    """External tables took an early return that skipped the collation strip,
    so replayed external DDL failed with PARSE_SYNTAX_ERROR at 'DEFAULT'. The
    collation clauses must be dropped while the external LOCATION survives."""
    ddl = (
        "CREATE TABLE IF NOT EXISTS `ai27_uc_finance`.`ap`.`invoices_ext` (\n"
        "  invoice_id BIGINT,\n"
        "  vendor STRING COLLATE UTF8_BINARY,\n"
        "  amount DECIMAL(18,2),\n"
        "  status STRING COLLATE UTF8_BINARY)\n"
        "USING delta\n"
        "COMMENT 'External invoices fixture (external-table migration test)'\n"
        "DEFAULT COLLATION UTF8_BINARY\n"
        "LOCATION 'abfss://data@ai27tgtfine9fc8b.dfs.core.windows.net/ap/invoices_ext'"
    )
    out = strip_managed_storage_clauses(ddl, "EXTERNAL_TABLE")
    assert "COLLATION" not in out.upper()   # table-level clause gone
    assert "COLLATE" not in out.upper()      # inline column qualifiers gone
    # The external LOCATION (the reason for the early return) is preserved.
    assert (
        "LOCATION 'abfss://data@ai27tgtfine9fc8b.dfs.core.windows.net/ap/invoices_ext'"
        in out
    )
    assert "USING delta" in out


def test_external_volume_location_kept_and_collation_stripped():
    ddl = (
        "CREATE EXTERNAL VOLUME `c`.`orders`.`archive`\n"
        "LOCATION 'abfss://data@acct.dfs.core.windows.net/orders/archive'"
    )
    out = strip_managed_storage_clauses(ddl, "EXTERNAL_VOLUME")
    assert (
        "LOCATION 'abfss://data@acct.dfs.core.windows.net/orders/archive'" in out
    )


def test_column_default_and_generated_by_default_are_preserved():
    """The strip anchors on COLLATION, so column DEFAULT values and identity
    columns using BY DEFAULT must be left intact."""
    from uc_sync.rewrite import strip_default_collation
    for keep in (
        "  ts TIMESTAMP DEFAULT current_timestamp(),",
        "  id BIGINT GENERATED BY DEFAULT AS IDENTITY,",
        "  status STRING DEFAULT 'new',",
    ):
        assert strip_default_collation(keep) == keep


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


def test_only_unreplayable_delta_properties_are_removed():
    """Bug #7: only the genuinely un-replayable keys are dropped (auto-generated
    row-tracking materialized column names + min reader/writer protocol versions);
    every other delta.* setting is KEPT so the target matches the source."""
    out = strip_managed_storage_clauses(_FULL_TBLPROPS, "TABLE")
    # Un-replayable keys are gone.
    assert "minReaderVersion" not in out
    assert "minWriterVersion" not in out
    assert "materializedRowIdColumnName" not in out
    assert "materializedRowCommitVersionColumnName" not in out
    # Replayable, meaningful settings are preserved (were wrongly dropped before).
    assert "'delta.enableDeletionVectors' = 'true'" in out
    assert "'delta.feature.rowTracking' = 'supported'" in out
    assert "COLLATION" not in out         # collation still gone


def test_user_defined_properties_are_preserved():
    out = strip_managed_storage_clauses(_FULL_TBLPROPS, "TABLE")
    assert "'my.business.tag' = 'gold'" in out
    assert "TBLPROPERTIES" in out


def test_tblproperties_clause_dropped_when_only_unreplayable_keys():
    only_unreplayable = (
        "CREATE TABLE c.s.t (id INT)\nUSING delta\n"
        "TBLPROPERTIES (\n  'delta.minReaderVersion' = '3',\n"
        "  'delta.minWriterVersion' = '7')"
    )
    out = strip_reserved_table_properties(only_unreplayable)
    assert "TBLPROPERTIES" not in out
    assert "USING delta" in out


def test_replayable_delta_properties_survive_bug7():
    """The two settings that broke tables when stripped: late-column clustering
    stats and column DEFAULTs — must now be preserved."""
    ddl = (
        "CREATE TABLE c.s.t (id INT, ts TIMESTAMP)\nUSING delta\nCLUSTER BY (ts)\n"
        "TBLPROPERTIES (\n"
        "  'delta.dataSkippingStatsColumns' = 'ts',\n"
        "  'delta.feature.allowColumnDefaults' = 'supported',\n"
        "  'delta.minReaderVersion' = '3')"
    )
    out = strip_reserved_table_properties(ddl)
    assert "'delta.dataSkippingStatsColumns' = 'ts'" in out
    assert "'delta.feature.allowColumnDefaults' = 'supported'" in out
    assert "minReaderVersion" not in out


def test_property_strip_leaves_view_collation_property_untouched():
    # View TBLPROPERTIES carry a non-delta 'collation' key that is valid to keep.
    view = (
        "CREATE VIEW c.s.v AS SELECT 1\n"
        "TBLPROPERTIES (\n  'collation' = 'UTF8_BINARY')"
    )
    out = strip_reserved_table_properties(view)
    assert "'collation' = 'UTF8_BINARY'" in out


# ---- Phase 2 sanitizer hardening -------------------------------------------


def test_tblproperties_value_with_parens_not_truncated():
    """A property VALUE containing ``)`` (clustering expr stats) must not
    truncate the TBLPROPERTIES block and corrupt the DDL."""
    ddl = (
        "CREATE TABLE c.s.t (id INT)\nUSING delta\nCLUSTER BY (region)\n"
        "TBLPROPERTIES (\n"
        "  'ai27_uc.fixture' = 'true',\n"
        "  'databricks.delta.expressionStats.selectedColumns' = "
        "'upper(region),lower(region)',\n"
        "  'delta.minReaderVersion' = '3',\n"
        "  'delta.enableRowTracking' = 'true');"
    )
    out = strip_reserved_table_properties(ddl)
    # A value containing ``)`` must not truncate the block (bug #7 keeps this key).
    assert "'ai27_uc.fixture' = 'true'" in out
    assert (
        "'databricks.delta.expressionStats.selectedColumns' = "
        "'upper(region),lower(region)'"
    ) in out
    assert "'delta.enableRowTracking' = 'true'" in out  # replayable → kept
    assert "minReaderVersion" not in out                # un-replayable → dropped
    assert "CLUSTER BY (region)" in out


def test_catalog_managed_location_is_kept_not_stripped():
    """Catalog MANAGED LOCATION must survive (target has no default storage),
    while managed table LOCATION is still stripped."""
    catalog_ddl = (
        "CREATE CATALOG IF NOT EXISTS `c` "
        "MANAGED LOCATION 'abfss://uc-root@acct.dfs.core.windows.net/c';"
    )
    kept = strip_managed_storage_clauses(catalog_ddl, "CATALOG")
    assert "MANAGED LOCATION 'abfss://uc-root@acct.dfs.core.windows.net/c'" in kept

    table_ddl = (
        "CREATE TABLE `c`.`s`.`t` (id INT) USING delta "
        "LOCATION 'abfss://x@acct.dfs.core.windows.net/t';"
    )
    stripped = strip_managed_storage_clauses(table_ddl, "TABLE")
    assert "LOCATION" not in stripped


def test_rewrite_access_connector_id():
    from uc_sync.rewrite import rewrite_access_connector_id
    ddl = ("CREATE STORAGE CREDENTIAL IF NOT EXISTS `c` WITH AZURE_MANAGED_IDENTITY "
           "(ACCESS_CONNECTOR_ID = '/subscriptions/SRC/.../connectors/src-ac');")
    out = rewrite_access_connector_id(ddl, "/subscriptions/TGT/.../connectors/tgt-ac")
    assert "/subscriptions/TGT/.../connectors/tgt-ac" in out
    assert "src-ac" not in out
    # No target id -> unchanged.
    assert rewrite_access_connector_id(ddl, "") == ddl
