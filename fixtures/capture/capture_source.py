#!/usr/bin/env python3
"""Capture the live DDL + governance of the ai27 source fixture catalogs.

Reads SHOW CREATE (tables/views/functions), volumes, ABAC policies, tags and
grants from the source workspace via the SQL Statement Execution API and dumps
everything to capture.json. This is the ground-truth snapshot the recreation
bundle is built from — run it while the source workspace still exists.

Usage:
  python3 fixtures/capture/capture_source.py --profile source_ws --warehouse eb2659cbee25f7d0
"""
import argparse, json, subprocess, sys, time

CATALOGS = ["ai27_uc_gov_src", "ai27_uc_finance", "ai27_uc_sales"]


def sql(profile, wh, statement, catalog=None):
    body = {"warehouse_id": wh, "statement": statement, "wait_timeout": "50s"}
    if catalog:
        body["catalog"] = catalog
    p = subprocess.run(["databricks", "api", "post", "/api/2.0/sql/statements",
                        "-p", profile, "--json", json.dumps(body)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"CLI error: {p.stderr[:400]}")
    out = json.loads(p.stdout)
    sid = out.get("statement_id")
    while out.get("status", {}).get("state") in ("PENDING", "RUNNING") and sid:
        time.sleep(2)
        g = subprocess.run(["databricks", "api", "get",
                            f"/api/2.0/sql/statements/{sid}", "-p", profile],
                           capture_output=True, text=True)
        out = json.loads(g.stdout)
    st = out.get("status", {})
    if st.get("state") != "SUCCEEDED":
        raise RuntimeError(st.get("error", {}).get("message", f"state={st.get('state')}"))
    return out.get("result", {}).get("data_array", []) or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="source_ws")
    ap.add_argument("--warehouse", required=True)
    ap.add_argument("--out", default="fixtures/capture/capture.json")
    args = ap.parse_args()
    q = lambda s, c=None: sql(args.profile, args.warehouse, s, c)

    snap = {"catalogs": {}}
    for cat in CATALOGS:
        print(f"== {cat} ==", file=sys.stderr)
        c = {"comment": None, "tags": [], "schemas": {},
             "abac_policies": [], "grants": []}
        # catalog tags + comment
        try:
            c["tags"] = q(f"SELECT tag_name, tag_value FROM system.information_schema.catalog_tags "
                          f"WHERE catalog_name = '{cat}'")
        except Exception as e:
            c["tags_error"] = str(e)[:200]
        try:
            c["grants"] = q(f"SHOW GRANTS ON CATALOG `{cat}`")
        except Exception as e:
            c["grants_error"] = str(e)[:200]
        # ABAC policies at catalog level (and below) — SHOW POLICIES if supported
        for scope in ("CATALOG",):
            try:
                c["abac_policies"] += [["CATALOG", cat] + r for r in q(f"SHOW POLICIES ON CATALOG `{cat}`")]
            except Exception as e:
                c["abac_error"] = str(e)[:200]

        schemas = [r[0] for r in q(f"SHOW SCHEMAS IN `{cat}`") if r[0] != "information_schema"]
        for sch in schemas:
            print(f"   schema {sch}", file=sys.stderr)
            s = {"tables": {}, "views": {}, "functions": {}, "volumes": {},
                 "tags": [], "grants": [], "abac_policies": []}
            try:
                s["tags"] = q(f"SELECT tag_name, tag_value FROM system.information_schema.schema_tags "
                              f"WHERE catalog_name='{cat}' AND schema_name='{sch}'")
            except Exception:
                pass
            try:
                s["grants"] = q(f"SHOW GRANTS ON SCHEMA `{cat}`.`{sch}`")
            except Exception:
                pass
            try:
                s["abac_policies"] = q(f"SHOW POLICIES ON SCHEMA `{cat}`.`{sch}`")
            except Exception:
                pass
            # tables + views
            for row in q(f"SHOW TABLES IN `{cat}`.`{sch}`"):
                tname = row[1]
                full = f"`{cat}`.`{sch}`.`{tname}`"
                try:
                    ddl = q(f"SHOW CREATE TABLE {full}")[0][0]
                except Exception as e:
                    ddl = f"-- ERROR: {str(e)[:200]}"
                entry = {"ddl": ddl, "grants": [], "abac_policies": []}
                try:
                    entry["grants"] = q(f"SHOW GRANTS ON TABLE {full}")
                except Exception:
                    pass
                try:
                    entry["abac_policies"] = q(f"SHOW POLICIES ON TABLE {full}")
                except Exception:
                    pass
                bucket = "views" if "CREATE VIEW" in ddl.upper() else "tables"
                s[bucket][tname] = entry
            # functions (catalog must be set in session for SHOW USER FUNCTIONS)
            for row in q(f"SHOW USER FUNCTIONS IN `{sch}`", cat):
                fq = row[0]  # already catalog.schema.func
                fname = fq.split(".")[-1]
                try:
                    ddl = q(f"SHOW CREATE FUNCTION `{cat}`.`{sch}`.`{fname}`")[0][0]
                except Exception as e:
                    ddl = f"-- ERROR: {str(e)[:200]}"
                s["functions"][fname] = {"ddl": ddl}
            # volumes
            try:
                for row in q(f"SHOW VOLUMES IN `{cat}`.`{sch}`"):
                    vname = row[1] if len(row) > 1 else row[0]
                    s["volumes"][vname] = {"info": row}
            except Exception:
                pass
            c["schemas"][sch] = s
        snap["catalogs"][cat] = c

    with open(args.out, "w") as f:
        json.dump(snap, f, indent=2)
    # summary
    for cat, c in snap["catalogs"].items():
        n_t = sum(len(s["tables"]) for s in c["schemas"].values())
        n_v = sum(len(s["views"]) for s in c["schemas"].values())
        n_f = sum(len(s["functions"]) for s in c["schemas"].values())
        n_vol = sum(len(s["volumes"]) for s in c["schemas"].values())
        print(f"{cat}: schemas={len(c['schemas'])} tables={n_t} views={n_v} "
              f"functions={n_f} volumes={n_vol}", file=sys.stderr)
    print(f"wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
