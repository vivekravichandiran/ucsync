"""RestSqlExecutor: governance reads over the Statement Execution API.

Guards the cross-workspace inventory fix — when the job runs on the target but
inventories a remote source, tag/ABAC reads must go to the source workspace's
SQL warehouse, not the local Spark session.
"""
from __future__ import annotations

import pytest

from uc_sync.governance import read_abac_policies
from uc_sync.import_engine import RestSqlExecutor


def _ok(data_array, **extra):
    return {"statement_id": "s", "status": {"state": "SUCCEEDED"},
            "result": {"data_array": data_array, **extra}}


class _FakeClient:
    """Minimal WorkspaceClient stand-in scripting a sequence of API responses.

    ``post_response`` may be a single response (reused) or a list consumed FIFO.
    """

    def __init__(self, post_response, get_responses=None):
        self._post_queue = (
            list(post_response) if isinstance(post_response, list) else None
        )
        self._post_response = None if self._post_queue is not None else post_response
        self._get_responses = list(get_responses or [])
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []

    def post(self, path, body):
        self.posts.append((path, body))
        item = self._post_queue.pop(0) if self._post_queue is not None else self._post_response
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, path):
        self.gets.append(path)
        return self._get_responses.pop(0)


def test_requires_warehouse_id():
    with pytest.raises(ValueError):
        RestSqlExecutor(_FakeClient({}), "")


def test_execute_returns_rows_as_lists():
    client = _FakeClient({
        "statement_id": "s1",
        "status": {"state": "SUCCEEDED"},
        "result": {"data_array": [["ai27", "finance", "CONFIDENTIAL"]]},
    })
    ex = RestSqlExecutor(client, "wh123")
    rows = ex.execute("SELECT 1")
    assert rows == [["ai27", "finance", "CONFIDENTIAL"]]
    # Statement submitted to the source warehouse.
    path, body = client.posts[0]
    assert path == "/api/2.0/sql/statements"
    assert body["warehouse_id"] == "wh123"
    assert body["statement"] == "SELECT 1"


def test_execute_polls_until_terminal():
    client = _FakeClient(
        {"statement_id": "s2", "status": {"state": "PENDING"}},
        get_responses=[
            {"statement_id": "s2", "status": {"state": "RUNNING"}},
            {
                "statement_id": "s2",
                "status": {"state": "SUCCEEDED"},
                "result": {"data_array": [["x"]]},
            },
        ],
    )
    ex = RestSqlExecutor(client, "wh", poll_seconds=0)
    assert ex.execute("SELECT x") == [["x"]]
    assert client.gets == [
        "/api/2.0/sql/statements/s2",
        "/api/2.0/sql/statements/s2",
    ]


def test_execute_raises_on_failure():
    client = _FakeClient({
        "statement_id": "s3",
        "status": {"state": "FAILED", "error": {"message": "bad sql"}},
    })
    ex = RestSqlExecutor(client, "wh", poll_seconds=0)
    with pytest.raises(RuntimeError, match="bad sql"):
        ex.execute("SELECT boom")


def test_deterministic_sql_error_is_not_retried():
    # A syntax/permission error must fail fast — submitted exactly once.
    client = _FakeClient([
        {"statement_id": "s", "status": {"state": "FAILED",
                                         "error": {"message": "TABLE_OR_VIEW_NOT_FOUND"}}},
    ])
    ex = RestSqlExecutor(client, "wh", poll_seconds=0, retry_base_seconds=0)
    with pytest.raises(RuntimeError, match="TABLE_OR_VIEW_NOT_FOUND"):
        ex.execute("SELECT 1")
    assert len(client.posts) == 1


def test_transient_statement_state_is_retried_then_succeeds():
    client = _FakeClient([
        {"statement_id": "s", "status": {"state": "FAILED",
                                         "error": {"message": "Warehouse is starting, please try again"}}},
        _ok([["ok"]]),
    ])
    ex = RestSqlExecutor(client, "wh", poll_seconds=0, retry_base_seconds=0)
    assert ex.execute("SELECT 1") == [["ok"]]
    assert len(client.posts) == 2  # retried once


def test_transient_submit_exception_is_retried():
    client = _FakeClient([
        RuntimeError("HTTP 503: service unavailable"),
        _ok([["ok"]]),
    ])
    ex = RestSqlExecutor(client, "wh", poll_seconds=0, retry_base_seconds=0)
    assert ex.execute("SELECT 1") == [["ok"]]
    assert len(client.posts) == 2


def test_exhausts_retries_and_raises():
    client = _FakeClient([RuntimeError("boom")] * 3)
    ex = RestSqlExecutor(client, "wh", poll_seconds=0, retry_base_seconds=0, max_retries=2)
    with pytest.raises(RuntimeError, match="after 3 attempt"):
        ex.execute("SELECT 1")
    assert len(client.posts) == 3  # initial + 2 retries


def test_execute_follows_chunk_links():
    client = _FakeClient(
        {
            "statement_id": "s4",
            "status": {"state": "SUCCEEDED"},
            "result": {
                "data_array": [["a"]],
                "next_chunk_internal_link": "/api/2.0/sql/statements/s4/result/chunks/1",
            },
        },
        get_responses=[
            {"data_array": [["b"]], "next_chunk_internal_link": None},
        ],
    )
    ex = RestSqlExecutor(client, "wh", poll_seconds=0)
    assert ex.execute("SELECT c") == [["a"], ["b"]]
    assert client.gets == ["/api/2.0/sql/statements/s4/result/chunks/1"]


def test_show_create_table_runs_over_rest():
    """Direct-mode export captures SHOW CREATE from the remote source over the
    Statement Execution API (the objects do not exist on the local/target Spark)."""
    client = _FakeClient(_ok([["CREATE TABLE c.s.t (id BIGINT) USING delta"]]))
    ex = RestSqlExecutor(client, "wh", poll_seconds=0)
    ddl = ex.show_create("TABLE", "c.s.t")
    assert ddl == "CREATE TABLE c.s.t (id BIGINT) USING delta"
    _, body = client.posts[0]
    assert body["statement"] == "SHOW CREATE TABLE `c`.`s`.`t`"


def test_show_create_function_not_supported():
    # Databricks SQL has no SHOW CREATE FUNCTION — functions are synthesized, so
    # this must fail fast without issuing a statement.
    client = _FakeClient({})
    ex = RestSqlExecutor(client, "wh")
    with pytest.raises(RuntimeError, match="FUNCTION"):
        ex.show_create("FUNCTION", "c.s.f")
    assert client.posts == []


def test_read_abac_policies_over_rest_parses_json_array_strings():
    """End-to-end seam: JSON_ARRAY renders array columns as JSON strings, and
    read_abac_policies must turn those into real lists (EXCEPT principals etc.).
    Mirrors the real source row for ai27_uc_gov_src.hr.employees_secure."""
    # 1st POST: the abac_policy_definitions SELECT. 2nd POST: DESCRIBE POLICY.
    select_row = [
        "ai27_uc_rowfilter_region",          # policy_name
        "ROW_FILTER",                         # policy_type
        "ai27_uc_gov_src",                    # catalog_name
        "hr",                                 # schema_name
        "employees_secure",                   # securable_name
        "TABLE",                              # on_securable_type
        '["account users"]',                 # to_principals   (JSON string)
        '["abhishek.iyer@databricks.com"]',  # except_principals (JSON string)
        "TABLE",                              # for_securable_type
        None,                                 # when_condition
        '["region"]',                        # match_columns   (JSON string)
    ]
    describe_rows = [
        ["Function Name", "ai27_uc_gov_src.sec.region_filter"],
        ["Using Columns", "region"],
    ]
    client = _FakeClient([_ok([select_row]), _ok(describe_rows)])
    ex = RestSqlExecutor(client, "wh", poll_seconds=0)

    policies = read_abac_policies(ex, "ai27_uc_gov_src")

    assert len(policies) == 1
    d = policies[0].definition
    assert d["policy_name"] == "ai27_uc_rowfilter_region"
    assert d["policy_type"] == "ROW_FILTER"
    assert d["on_securable"] == "ai27_uc_gov_src.hr.employees_secure"
    assert d["except_principals"] == ["abhishek.iyer@databricks.com"]
    assert d["match_columns"] == ["region"]
    assert d["function_name"] == "ai27_uc_gov_src.sec.region_filter"
