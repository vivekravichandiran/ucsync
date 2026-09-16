"""Bug #5: table names that must be backtick-quoted (digit-leading like ``4g_...`` /
``5g_...``, or containing a hyphen) must re-qualify correctly. The CREATE-name
matcher must handle a MIXED name (bare catalog/schema + backticked table); the old
regex grabbed only ``cat.schema`` and left the backticked tail behind, producing a
4-part name rejected with "requires a single-part namespace"."""

from __future__ import annotations

from uc_sync.package_import import (
    _qualify_create_name,
    _split_qualified_name,
    quote_full_name,
)


def test_split_respects_backticks():
    assert _split_qualified_name("cat.schema.`4g_cust`") == ["cat", "schema", "4g_cust"]
    assert _split_qualified_name("`cat`.`schema`.`tbl`") == ["cat", "schema", "tbl"]
    assert _split_qualified_name("cat.schema.tbl") == ["cat", "schema", "tbl"]
    # A dot INSIDE a backtick-quoted part is not a separator.
    assert _split_qualified_name("cat.`odd.name`") == ["cat", "odd.name"]


def test_quote_full_name_handles_quoted_bare_and_mixed():
    assert quote_full_name("cat.schema.4g_cust") == "`cat`.`schema`.`4g_cust`"
    # Already-quoted parts are not double-quoted.
    assert quote_full_name("cat.schema.`4g_cust`") == "`cat`.`schema`.`4g_cust`"
    assert quote_full_name("`cat`.`schema`.`my-tbl`") == "`cat`.`schema`.`my-tbl`"


def test_qualify_mixed_name_digit_leading_table():
    """The exact bug: bare catalog/schema, backticked digit-leading table."""
    stmt = "CREATE TABLE cat.schema.`4g_cust` (id INT)"
    out = _qualify_create_name(stmt, "tgt.schema.4g_cust")
    assert out == "CREATE TABLE `tgt`.`schema`.`4g_cust` (id INT)"
    # No leftover tail → not a 4-part name.
    assert "`4g_cust`.`4g_cust`" not in out
    assert out.count("`4g_cust`") == 1


def test_qualify_hyphenated_and_plain_and_fully_backticked():
    # Hyphenated table (backticked at source).
    assert _qualify_create_name(
        "CREATE TABLE cat.schema.`cust-eu` (id INT)", "cat.schema.cust-eu"
    ) == "CREATE TABLE `cat`.`schema`.`cust-eu` (id INT)"
    # Plain name.
    assert _qualify_create_name(
        "CREATE TABLE cat.schema.orders (id INT)", "cat.schema.orders"
    ) == "CREATE TABLE `cat`.`schema`.`orders` (id INT)"
    # Fully backticked source name.
    assert _qualify_create_name(
        "CREATE TABLE `cat`.`schema`.`5g_events` (id INT)", "cat.schema.5g_events"
    ) == "CREATE TABLE `cat`.`schema`.`5g_events` (id INT)"


def test_qualify_view_with_mixed_name():
    stmt = "CREATE VIEW cat.schema.`4g_summary` AS SELECT 1"
    out = _qualify_create_name(stmt, "tgt.schema.4g_summary")
    assert out == "CREATE VIEW `tgt`.`schema`.`4g_summary` AS SELECT 1"


def test_qualify_2part_view_name_behind_comment_header():
    """Regression: since #11 the splitter preserves the utility's ``-- …`` header
    ahead of CREATE. The CREATE-name matcher must skip it and still re-qualify the
    2-part view name (SHOW CREATE VIEW emits ``schema.view``) to 3 parts — otherwise
    it resolves against the warehouse's default catalog (SCHEMA_NOT_FOUND)."""
    stmt = (
        "-- VIEW ai27_uc_gov_src.analytics.emp_summary\n"
        "-- source=SHOW_CREATE\n"
        "-- captured via SHOW CREATE TABLE `ai27_uc_gov_src`.`analytics`.`emp_summary`\n"
        "CREATE VIEW analytics.emp_summary (dept, headcount)\n"
        "AS SELECT dept, count(*) FROM ai27_uc_gov_src.hr.employees GROUP BY dept;"
    )
    out = _qualify_create_name(stmt, "ai27_uc_gov_src.analytics.emp_summary")
    assert "CREATE VIEW `ai27_uc_gov_src`.`analytics`.`emp_summary`" in out
    # The header comments are preserved (bug #11), and the body is untouched.
    assert out.startswith("-- VIEW ai27_uc_gov_src.analytics.emp_summary")
    assert "FROM ai27_uc_gov_src.hr.employees" in out


def test_normalize_or_replace_behind_comment_header():
    from uc_sync.package_import import _normalize_create_statement

    stmt = "-- VIEW c.s.v\n-- source=SHOW_CREATE\nCREATE VIEW s.v AS SELECT 1;"
    out = _normalize_create_statement(stmt)
    assert "CREATE OR REPLACE VIEW s.v" in out
    assert out.startswith("-- VIEW c.s.v")  # comment header preserved
