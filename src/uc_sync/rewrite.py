"""Path-only rewrite + DDL replay sanitizers for exported SQL, YAML, and JSON.

Catalog / schema / table / external-location **names are never rewritten** — the
governance-migration utility recreates every securable under its source name (see
``plans/uc-governance-migration-design.md`` §2.4). The only value rewritten here is
the **storage URL** (source ADLS path → mapped target ADLS path), driven by the
single mapping file. The ``strip_*`` sanitizers make captured ``SHOW CREATE`` DDL
replayable on a fresh target metastore.
"""

from __future__ import annotations

import json
import re
from typing import Any

from uc_sync.mapping import MappingResolver


def rewrite_text(
    text: str,
    *,
    location_resolver: MappingResolver | None = None,
) -> str:
    """Rewrite storage URLs in free-form text; leave every identifier untouched."""

    rewritten = str(text or "")
    if location_resolver is not None:
        rewritten = _rewrite_storage_urls(rewritten, location_resolver)
    return rewritten


def _rewrite_storage_urls(text: str, resolver: MappingResolver) -> str:
    pattern = re.compile(
        r"(abfss://[^\s'\"`]+|abfs://[^\s'\"`]+|s3://[^\s'\"`]+|gs://[^\s'\"`]+)",
        re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        source = match.group(1)
        target = resolver.rewrite_location(source.rstrip("/"))
        return target if target else source

    return pattern.sub(replace, text)


def rewrite_access_connector_id(text: str, target_connector_id: str) -> str:
    """Point a storage-credential's ``ACCESS_CONNECTOR_ID`` at the target connector.

    The source connector lives in the source region and is unusable on the target,
    so a target-region access-connector id (from the mapping file) is substituted
    when creating the credential. No-op when no target id is provided.
    """

    target = str(target_connector_id or "").strip()
    if not target:
        return text
    return re.sub(
        r"(ACCESS_CONNECTOR_ID\s*=\s*')[^']*(')",
        rf"\g<1>{target}\g<2>",
        str(text or ""),
        flags=re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# Token-aware clause stripping.
#
# A plain `re.sub` over the whole DDL string cannot tell a real `LOCATION
# '<path>'` / `COLLATE <name>` clause from the same words occurring, by pure
# coincidence, inside a column/table COMMENT string (e.g. a geo/session table
# whose comment says "...approximate location'"). `[^']*` has no notion of
# "this quote belongs to a different, unrelated string" — it just eats
# forward to the next quote character anywhere in the text, which can span
# across (and delete) real DDL content between an accidental comment match
# and some unrelated later string.
#
# `_tokenize_sql` first partitions the text into CODE vs LIT (quoted string /
# backtick identifier / comment / $$ block) segments — mirroring the
# quote-aware FSM already proven correct in
# `package_import._split_statements` — so every clause-stripper below only
# ever matches a keyword inside a CODE segment, and only ever removes the LIT
# segment immediately following it (the clause's own value). A LIT segment
# that is NOT immediately preceded by a matching keyword — e.g. any COMMENT
# string, no matter what words it contains — is structurally never a
# candidate at all, so it can never be touched.
# ---------------------------------------------------------------------------


def _tokenize_sql(text: str) -> list[tuple[str, str]]:
    """Partition DDL text into ordered ``('CODE' | 'LIT', chunk)`` segments.

    ``LIT`` = a single/double-quoted string literal (``''``-escape aware via
    the same toggle-pair logic as the import-side splitter), a backtick-quoted
    identifier, a ``--`` line comment, a ``/* */`` block comment, or a
    ``$$ … $$`` block. Everything else is ``CODE``. Lossless: joining every
    chunk, in order, reproduces ``text`` exactly.
    """

    s = str(text or "")
    tokens: list[tuple[str, str]] = []
    buf: list[str] = []
    cur = "CODE"
    i, n = 0, len(s)
    in_single = in_double = in_backtick = False
    in_line_comment = in_block_comment = in_dollar = False

    def flush() -> None:
        if buf:
            tokens.append((cur, "".join(buf)))
            buf.clear()

    while i < n:
        ch = s[i]
        two = s[i : i + 2]
        if in_line_comment:
            buf.append(ch)
            i += 1
            if ch == "\n":
                in_line_comment = False
                flush()
                cur = "CODE"
            continue
        if in_block_comment:
            if two == "*/":
                buf.append(two)
                i += 2
                in_block_comment = False
                flush()
                cur = "CODE"
            else:
                buf.append(ch)
                i += 1
            continue
        if in_dollar:
            if two == "$$":
                buf.append(two)
                i += 2
                in_dollar = False
                flush()
                cur = "CODE"
            else:
                buf.append(ch)
                i += 1
            continue
        if in_single:
            buf.append(ch)
            i += 1
            if ch == "'":
                in_single = False
                flush()
                cur = "CODE"
            continue
        if in_double:
            buf.append(ch)
            i += 1
            if ch == '"':
                in_double = False
                flush()
                cur = "CODE"
            continue
        if in_backtick:
            buf.append(ch)
            i += 1
            if ch == "`":
                in_backtick = False
                flush()
                cur = "CODE"
            continue
        if two == "--":
            flush()
            cur = "LIT"
            in_line_comment = True
            buf.append(two)
            i += 2
            continue
        if two == "/*":
            flush()
            cur = "LIT"
            in_block_comment = True
            buf.append(two)
            i += 2
            continue
        if two == "$$":
            flush()
            cur = "LIT"
            in_dollar = True
            buf.append(two)
            i += 2
            continue
        if ch == "'":
            flush()
            cur = "LIT"
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            flush()
            cur = "LIT"
            in_double = True
            buf.append(ch)
            i += 1
            continue
        if ch == "`":
            flush()
            cur = "LIT"
            in_backtick = True
            buf.append(ch)
            i += 1
            continue
        buf.append(ch)
        i += 1

    flush()
    return tokens


def _detokenize(tokens: list[tuple[str, str]]) -> str:
    return "".join(chunk for _, chunk in tokens)


def _strip_keyword_quoted_value(
    tokens: list[tuple[str, str]], keyword_re: "re.Pattern[str]"
) -> list[tuple[str, str]]:
    """Remove a ``<keyword> '<value>'`` / ``<keyword> "<value>"`` / `` <keyword>
    `v` `` clause from a tokenized DDL. ``keyword_re`` must match at the END of
    a CODE chunk (i.e. end the pattern with ``$``). The clause's value is
    taken ONLY from the LIT token immediately following the matched keyword —
    never by re-scanning raw text for "the next quote" — so a comment
    elsewhere in the DDL can never be mistaken for the value: comments are
    never a candidate in the first place, since keyword matching only ever
    looks at CODE chunks.
    """

    out: list[tuple[str, str]] = []
    i, n = 0, len(tokens)
    while i < n:
        kind, chunk = tokens[i]
        if kind == "CODE":
            m = keyword_re.search(chunk)
            if (
                m
                and i + 1 < n
                and tokens[i + 1][0] == "LIT"
                and tokens[i + 1][1][:1] in ("'", '"', "`")
            ):
                out.append(("CODE", chunk[: m.start()]))
                i += 2
                continue
        out.append((kind, chunk))
        i += 1
    return out


def _sub_in_code(
    tokens: list[tuple[str, str]], pattern: "re.Pattern[str]", repl: str = ""
) -> list[tuple[str, str]]:
    """Apply ``pattern.sub`` to CODE chunks only — never to LIT (string/comment)
    chunks — so a bare-identifier clause (e.g. ``COLLATE UTF8_BINARY`` with no
    quotes) can be matched directly without ever reaching into a comment: a
    CODE chunk contains no quote/comment characters by construction.
    """

    return [
        (kind, pattern.sub(repl, chunk) if kind == "CODE" else chunk)
        for kind, chunk in tokens
    ]


_MANAGED_LOCATION_KW_RE = re.compile(r"\s+MANAGED\s+LOCATION\s*$", re.IGNORECASE)
_LOCATION_KW_RE = re.compile(r"\s+LOCATION\s*$", re.IGNORECASE)


def strip_managed_storage_clauses(text: str, object_type: str = "") -> str:
    """Drop source managed LOCATION clauses so the target metastore assigns storage.

    External tables/volumes/locations keep LOCATION/URL (rewritten separately).
    Catalogs (and schemas) also keep their ``MANAGED LOCATION`` — it is
    path-rewritten to the target ADLS root, because a target metastore without a
    default storage root cannot create a catalog without one. Only *managed
    table/volume* LOCATION clauses are stripped (the target metastore assigns
    managed table storage under the catalog root).
    """

    upper = str(object_type or "").upper()
    if upper in {"EXTERNAL_TABLE", "EXTERNAL_VOLUME", "EXTERNAL_LOCATION"}:
        # Keep the external LOCATION/URL (rewritten separately) but still strip
        # collation clauses: a target metastore without collation enabled rejects
        # the replayed `DEFAULT COLLATION`/inline `COLLATE` with PARSE_SYNTAX_ERROR,
        # exactly as for managed tables and catalogs/schemas.
        rewritten = strip_default_collation(str(text or ""))
        rewritten = strip_inline_collate(rewritten)
        return strip_reserved_table_properties(rewritten)
    if upper in {"CATALOG", "SCHEMA"}:
        # Keep the (already path-rewritten) MANAGED LOCATION — a target metastore
        # with no default storage root cannot create a catalog without one — but
        # still strip collation / reserved-property noise.
        rewritten = strip_default_collation(str(text or ""))
        rewritten = strip_inline_collate(rewritten)
        return strip_reserved_table_properties(rewritten)
    rewritten = str(text or "")
    rewritten = _detokenize(
        _strip_keyword_quoted_value(_tokenize_sql(rewritten), _MANAGED_LOCATION_KW_RE)
    )
    # Managed CREATE TABLE / CREATE VOLUME clauses (not EXTERNAL ...).
    if "EXTERNAL" not in rewritten.upper().split("LOCATION", 1)[0]:
        rewritten = _detokenize(
            _strip_keyword_quoted_value(_tokenize_sql(rewritten), _LOCATION_KW_RE)
        )
    # Table-level default collation clause — drop it (see strip_default_collation).
    rewritten = strip_default_collation(rewritten)
    rewritten = strip_inline_collate(rewritten)
    # Inline column-mask / row-filter clauses are DELIBERATELY KEPT in the CREATE
    # TABLE DDL: with functions imported before tables, the clauses resolve, so a
    # table is created with its protection atomically — and if a mask/filter
    # function is missing, the CREATE TABLE itself fails (fail-closed) rather than
    # leaving an unprotected table behind. (strip_inline_policy_clauses is retained
    # for callers that still need it, but the migrate replay no longer applies it.)
    rewritten = strip_reserved_table_properties(rewritten)
    return rewritten


_COLLATE_KW_RE = re.compile(r"\s+COLLATE\s*$", re.IGNORECASE)
_COLLATE_BARE_RE = re.compile(
    r"\s+COLLATE\s+[A-Za-z_][A-Za-z0-9_]*", re.IGNORECASE
)


def strip_inline_collate(text: str) -> str:
    """Drop inline ``COLLATE <name>`` qualifiers from captured DDL.

    Distinct from the table-level ``COLLATION '<name>'`` clause handled above,
    this targets the per-type qualifier that rides on column and parameter type
    declarations, e.g. ``v STRING COLLATE UTF8_BINARY``. It shows up most often in
    synthesized *function* DDL: ``SHOW CREATE FUNCTION`` fails on some runtimes, so
    the DDL is rebuilt from catalog metadata whose ``type_text`` embeds the source
    collation, and table column definitions can carry the same qualifier.

    A target metastore that hasn't enabled collation rejects any ``COLLATE`` with
    ``[UNSUPPORTED_FEATURE.COLLATION] ... SQLSTATE: 0A000`` — a different error than
    the table-level clause's ``PARSE_SYNTAX_ERROR`` but the same root cause. The
    qualifier describes source string internals, so strip it and let the target use
    its default collation. The collation name is an unquoted identifier
    (``UTF8_BINARY``, ``UTF8_LCASE``, …) or a backtick-quoted one; ``COLLATION`` is
    left alone because it is never followed by whitespace here.

    Token-aware (see the ``_tokenize_sql`` block above): the backtick-quoted
    variant is only matched as the LIT token immediately after ``COLLATE``, and
    the bare-identifier variant is only matched inside CODE chunks — so a
    column/table COMMENT containing the word "collate" is never a candidate.
    """

    tokens = _tokenize_sql(str(text or ""))
    tokens = _strip_keyword_quoted_value(tokens, _COLLATE_KW_RE)
    tokens = _sub_in_code(tokens, _COLLATE_BARE_RE)
    return _detokenize(tokens)


# Table/catalog/schema-level default collation clause. SHOW CREATE emits either the
# older quoted form (``COLLATION 'UTF8_BINARY'``) or the newer unquoted
# ``DEFAULT COLLATION UTF8_BINARY``; the value is quoted, backtick-quoted, or a bare
# identifier. Anchored on ``COLLATION`` (optionally preceded by ``DEFAULT``) so a
# column ``DEFAULT <expr>`` or ``GENERATED BY DEFAULT AS IDENTITY`` is never touched.
_COLLATION_KW_RE = re.compile(
    r"\s+(?:DEFAULT\s+)?COLLATION\s*$", re.IGNORECASE
)
_COLLATION_BARE_RE = re.compile(
    r"\s+(?:DEFAULT\s+)?COLLATION\s+[A-Za-z_][A-Za-z0-9_]*", re.IGNORECASE
)


def strip_default_collation(text: str) -> str:
    """Drop a table/catalog/schema-level ``[DEFAULT] COLLATION <name>`` clause.

    A target metastore without collation enabled rejects the replayed clause with
    ``PARSE_SYNTAX_ERROR`` at ``DEFAULT`` / ``COLLATION``. The clause only records the
    source's default collation, so strip it and let the target apply its own. Inline
    per-column ``COLLATE <name>`` qualifiers are handled by strip_inline_collate().

    Token-aware (see the ``_tokenize_sql`` block above): the quoted/backtick
    variant is only matched as the LIT token immediately after ``[DEFAULT]
    COLLATION``, and the bare-identifier variant is only matched inside CODE
    chunks — so a column/table COMMENT containing the word "collation" (or
    "default") is never a candidate, unlike the previous whole-text regex.
    """

    tokens = _tokenize_sql(str(text or ""))
    tokens = _strip_keyword_quoted_value(tokens, _COLLATION_KW_RE)
    tokens = _sub_in_code(tokens, _COLLATION_BARE_RE)
    return _detokenize(tokens)


# A fully-qualified name: backtick-quoted or bare identifiers joined by dots.
_FQ_NAME = (
    r"(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*\.\s*(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_]*))*"
)


def strip_inline_policy_clauses(text: str) -> str:
    """Drop inline column-mask and row-filter clauses from captured CREATE DDL.

    ``SHOW CREATE TABLE`` emits column masks and the table row filter inline, e.g.::

        ssn STRING COLLATE UTF8_BINARY MASK `cat`.`sec`.`mask_ssn`,
        ...
        WITH ROW FILTER `cat`.`sec`.`hr_dept_filter` ON (dept)

    Retained as a utility, but **no longer part of the migrate replay pipeline**:
    functions now import before tables, so the inline clauses resolve and are kept
    in the CREATE TABLE for atomic fail-closed protection (see
    :func:`strip_managed_storage_clauses`). Mirrors :func:`strip_inline_collate`.
    """

    rewritten = str(text or "")
    # Table-level row filter: ``WITH ROW FILTER <fqname> ON (cols)``.
    rewritten = re.sub(
        rf"\s+WITH\s+ROW\s+FILTER\s+{_FQ_NAME}\s+ON\s*\([^)]*\)",
        "",
        rewritten,
        flags=re.IGNORECASE,
    )
    # Column mask: ``MASK <fqname> [USING COLUMNS (cols)]`` inside a column def.
    rewritten = re.sub(
        rf"\s+MASK\s+{_FQ_NAME}(?:\s+USING\s+COLUMNS\s*\([^)]*\))?",
        "",
        rewritten,
        flags=re.IGNORECASE,
    )
    return rewritten


# The ONLY table properties that genuinely cannot be replayed on CREATE TABLE
# (bug #7). Everything else — including replayable, meaningful settings like
# ``delta.dataSkippingStatsColumns`` (late-column clustering), ``delta.feature.
# allowColumnDefaults`` (column DEFAULTs), deletion vectors, row tracking, auto-
# optimize, compression, … — is KEPT so the target matches the source's real
# configuration. Blanket-dropping ``delta.*`` broke 12 tables and silently changed
# every "successful" one. Compared case-insensitively.
#   * the two auto-generated row-tracking materialized column names — assigned by
#     the engine; replaying the source's names throws DELTA_UNKNOWN_CONFIGURATION;
#   * the protocol floor versions — the target derives these from the enabled
#     features, and setting them explicitly is rejected / meaningless.
_UNREPLAYABLE_TABLE_PROPERTY_KEYS = {
    "delta.rowtracking.materializedrowidcolumnname",
    "delta.rowtracking.materializedrowcommitversioncolumnname",
    "delta.minreaderversion",
    "delta.minwriterversion",
}


def is_replayable_table_property(key: str) -> bool:
    """True unless ``key`` is one of the genuinely un-replayable table properties
    (bug #7). Used by the DDL-synthesis paths so they keep the same meaningful
    delta.* settings the SHOW CREATE path preserves."""
    return str(key or "").lower() not in _UNREPLAYABLE_TABLE_PROPERTY_KEYS


def strip_reserved_table_properties(text: str) -> str:
    """Remove ONLY the un-replayable table properties from a TBLPROPERTIES block.

    ``SHOW CREATE TABLE`` emits the table's full property set. A few keys cannot be
    replayed on ``CREATE TABLE`` (they throw ``DELTA_UNKNOWN_CONFIGURATION`` or are
    engine-derived) — the auto-generated row-tracking materialized column names and
    the min reader/writer protocol versions (see
    ``_UNREPLAYABLE_TABLE_PROPERTY_KEYS``). Those are dropped; **every other
    property is preserved** so the target's configuration matches the source
    (bug #7 — the previous code stripped all ``delta.*``/``databricks.*``, breaking
    late-column clustering + column DEFAULTs and silently dropping deletion vectors,
    row tracking, auto-optimize, compression, …). If no properties remain the whole
    ``TBLPROPERTIES (...)`` clause is removed.
    """

    rewritten = str(text or "")

    def _filter_block(match: re.Match[str]) -> str:
        body = match.group(1)
        kept: list[str] = []
        # Entries look like: 'key' = 'value'  (comma-separated, possibly multiline).
        # The quoted-pair regex tolerates ``)`` inside a value
        # (e.g. 'upper(region),lower(region)') because it anchors on quotes.
        for key, value in re.findall(
            r"'([^']*)'\s*=\s*'([^']*)'", body
        ):
            if key.lower() in _UNREPLAYABLE_TABLE_PROPERTY_KEYS:
                continue
            kept.append(f"'{key}' = '{value}'")
        if not kept:
            return ""
        return "TBLPROPERTIES (\n  " + ",\n  ".join(kept) + ")"

    # Greedy capture to the final ``)`` so a property VALUE containing ``)``
    # (e.g. 'upper(region),lower(region)') does not truncate the block. Any
    # trailing ``;`` stays outside the match. TBLPROPERTIES is the last clause in
    # captured table DDL, so nothing legitimate follows it.
    rewritten = re.sub(
        r"TBLPROPERTIES\s*\((.*)\)",
        _filter_block,
        rewritten,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Tidy any dangling whitespace left where the clause was removed.
    rewritten = re.sub(r"[ \t]+\n", "\n", rewritten)
    rewritten = re.sub(r"\n{3,}", "\n\n", rewritten)
    return rewritten


def rewrite_json_value(
    value: Any,
    *,
    location_resolver: MappingResolver | None = None,
) -> Any:
    if isinstance(value, dict):
        return {
            key: rewrite_json_value(item, location_resolver=location_resolver)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            rewrite_json_value(item, location_resolver=location_resolver)
            for item in value
        ]
    if isinstance(value, str):
        return rewrite_text(value, location_resolver=location_resolver)
    return value


def rewrite_json_text(
    text: str,
    *,
    location_resolver: MappingResolver | None = None,
) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return rewrite_text(text, location_resolver=location_resolver)
    rewritten = rewrite_json_value(payload, location_resolver=location_resolver)
    return json.dumps(rewritten, indent=2, default=str) + "\n"
