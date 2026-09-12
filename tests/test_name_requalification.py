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
