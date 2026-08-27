#!/usr/bin/env python3
"""Capture function bodies, ABAC CREATE POLICY statements, and column/table tags.

SHOW CREATE FUNCTION is unsupported on the serverless runtime, and ABAC policy
bodies/tags don't appear in SHOW CREATE TABLE, so gather them from each catalog's
own information_schema (+ the utility's ABAC reconstruction) here.
"""
import json, os, ssl, subprocess, sys, time
ssl._create_default_https_context = ssl._create_unverified_context
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from uc_sync.governance import read_abac_policies, abac_policy_create_statement

PROFILE, WH = sys.argv[1] if len(sys.argv) > 1 else "source_ws", \
               sys.argv[2] if len(sys.argv) > 2 else "eb2659cbee25f7d0"
CATALOGS = ["ai27_uc_gov_src", "ai27_uc_finance", "ai27_uc_sales"]


class Sql:
    def __init__(self, profile, wh):
        self.profile, self.wh = profile, wh
    def execute(self, statement, catalog=None):
        body = {"warehouse_id": self.wh, "statement": statement, "wait_timeout": "50s"}
        if catalog:
            body["catalog"] = catalog
        p = subprocess.run(["databricks", "api", "post", "/api/2.0/sql/statements",
                            "-p", self.profile, "--json", json.dumps(body)],
                           capture_output=True, text=True)
        out = json.loads(p.stdout)
        sid = out.get("statement_id")
        while out.get("status", {}).get("state") in ("PENDING", "RUNNING") and sid:
            time.sleep(2)
            g = subprocess.run(["databricks", "api", "get",
                                f"/api/2.0/sql/statements/{sid}", "-p", self.profile],
                               capture_output=True, text=True)
            out = json.loads(g.stdout)
        st = out.get("status", {})
        if st.get("state") != "SUCCEEDED":
            raise RuntimeError(st.get("error", {}).get("message", str(st)))
        return out.get("result", {}).get("data_array", []) or []


sql = Sql(PROFILE, WH)
snap = {"functions": {}, "abac_create": {}, "column_tags": {}, "table_tags": {}}

for cat in CATALOGS:
    # function definitions (signature + body) from information_schema
    rows = sql.execute(
        "SELECT r.specific_schema, r.routine_name, r.data_type, r.routine_definition, "
        "r.comment FROM information_schema.routines r WHERE r.routine_catalog = current_catalog()",
        catalog=cat)
    for schema, name, dtype, body, comment in rows:
        # parameters in order
        params = sql.execute(
            "SELECT parameter_name, data_type FROM information_schema.parameters "
            f"WHERE specific_schema='{schema}' AND specific_name='{name}' "
            "AND parameter_mode='IN' ORDER BY ordinal_position", catalog=cat)
        sig = ", ".join(f"{pn} {pt}" for pn, pt in params)
        snap["functions"][f"{cat}.{schema}.{name}"] = {
            "signature": sig, "returns": dtype, "body": body, "comment": comment}
    # ABAC CREATE POLICY
    for obj in read_abac_policies(sql, cat):
        stmt = abac_policy_create_statement(obj)
        snap["abac_create"][obj.full_name] = stmt
    # column tags
    ct = sql.execute(
        "SELECT schema_name, table_name, column_name, tag_name, tag_value "
        "FROM information_schema.column_tags", catalog=cat)
    snap["column_tags"][cat] = ct
    # table tags
    tt = sql.execute(
        "SELECT schema_name, table_name, tag_name, tag_value "
        "FROM information_schema.table_tags", catalog=cat)
    snap["table_tags"][cat] = tt

out = "fixtures/capture/governance.json"
with open(out, "w") as f:
    json.dump(snap, f, indent=2)
print(f"functions={len(snap['functions'])} abac={len(snap['abac_create'])} -> {out}")
print(json.dumps(snap, indent=2))
