#!/usr/bin/env python3
"""One-command recreation of the ai27 UC-migration test fixtures.

Rebuilds everything after an org-wide Azure/workspace cleanup sweep:
  1. azure     -> Azure RGs, ADLS Gen2 accounts, containers, connectors, roles
  2. storage   -> UC storage credentials + external locations (source)
  3. catalogs  -> CREATE CATALOG/SCHEMA (30_catalogs.sql)
  4. objects   -> gov_src / finance / sales objects + governance + data + grants
  5. negative  -> fail-closed negative fixtures (ai_27 dependency)

Config lives in fixtures/config.env. Account-name placeholders ({{GOV_ACCOUNT}},
{{FIN_ACCOUNT}}, {{SALES_ACCOUNT}}, {{EXPORT_SP}}) in the .sql files are filled in
from that config before execution.

Usage:
  python3 fixtures/recreate.py all                  # every stage, in order
  python3 fixtures/recreate.py azure storage catalogs objects negative
  python3 fixtures/recreate.py objects              # just re-apply object SQL
  python3 fixtures/recreate.py --dry-run all        # print statements only
"""
import argparse, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))

# --- Legacy 3-catalog fixture (gov_src / finance / sales) — runs on the SOURCE ws ---
SQL_STAGES = {
    "catalogs": ["30_catalogs.sql"],
    "objects": ["40_gov_src.sql", "41_finance.sql", "42_sales.sql"],
    "negative": ["50_negative.sql"],
}
ORDER = ["azure", "storage", "catalogs", "objects", "negative"]

# --- ai27_ucsync_testcatalog — single comprehensive bed. Source-run SQL stages ---
TC_SQL_STAGES = {
    "tc_catalog":   ["31_testcat_catalog.sql"],
    "tc_functions": ["60_testcat_functions.sql"],
    "tc_core":      ["61_testcat_core.sql"],
    "tc_governed":  ["62_testcat_governed.sql"],
    "tc_external":  ["63_testcat_external.sql"],
    "tc_acl":       ["65_testcat_acl.sql"],
    "tc_negative":  ["66_testcat_negative.sql"],
    "tc_metric":    ["67_testcat_metric_view.sql"],
}
# Target-run SQL stages (BYO shell — execute on the TARGET ws/warehouse)
TC_SQL_STAGES_TGT = {
    "tc_target_byo": ["32_testcat_target_byo.sql"],
}
# testcat meta-stage expands to this full ordered build
TC_ORDER = [
    "tc_azure", "tc_storage", "tc_catalog", "tc_target_byo",
    "tc_functions", "tc_core", "tc_governed", "tc_external", "tc_files",
    "tc_metric", "tc_acl", "tc_negative", "tc_reportonly",
]


def load_config():
    """Source config.env in bash and capture the exported vars."""
    env_path = os.path.join(HERE, "config.env")
    out = subprocess.run(["bash", "-c", f"set -a; source '{env_path}'; env"],
                         capture_output=True, text=True, check=True)
    cfg = {}
    for line in out.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            cfg[k] = v
    return cfg


def substitute(text, cfg):
    # Replace any {{KEY}} for which config.env exports KEY. Longest keys first so a
    # key that is a prefix of another (e.g. FOO vs FOO_BAR) can't partially match.
    for key in sorted(cfg, key=len, reverse=True):
        if "{{" + key + "}}" in text:
            text = text.replace("{{" + key + "}}", cfg[key])
    return text


def split_statements(text):
    """';'-split respecting $$ blocks and ' string literals (matches dbsql.py).

    Full-line ``--`` comments are stripped FIRST so a ``;`` or apostrophe inside a
    comment (e.g. "container root; each" or "account's data") never mis-splits the
    char-level scanner. The fixtures use no ``$$`` bodies, so this line-strip is safe
    (a comment line inside a dollar-quoted body would need the scanner instead).
    """
    text = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )
    stmts, buf, i, in_dollar, in_str = [], [], 0, False, False
    while i < len(text):
        ch = text[i]
        if not in_str and text[i:i + 2] == "$$":
            in_dollar = not in_dollar; buf.append("$$"); i += 2; continue
        if not in_dollar and ch == "'":
            in_str = not in_str
        if ch == ";" and not in_dollar and not in_str:
            stmts.append("".join(buf)); buf = []; i += 1; continue
        buf.append(ch); i += 1
    if "".join(buf).strip():
        stmts.append("".join(buf))
    # drop pure-comment / empty statements
    out = []
    for s in stmts:
        body = "\n".join(l for l in s.splitlines() if not l.strip().startswith("--"))
        if body.strip():
            out.append(s.strip())
    return out


def run_sql(profile, wh, statement):
    body = {"warehouse_id": wh, "statement": statement, "wait_timeout": "50s"}
    p = subprocess.run(["databricks", "api", "post", "/api/2.0/sql/statements",
                        "-p", profile, "--json", json.dumps(body)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return "CLI_ERROR", p.stderr.strip()[:500]
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
        return st.get("state"), st.get("error", {}).get("message", "")[:500]
    return "SUCCEEDED", None


def run_sql_stage(files, cfg, dry_run, profile=None, wh=None):
    profile = profile or cfg["SRC_PROFILE"]
    wh = wh or cfg["SRC_WAREHOUSE"]
    failed = 0
    for fname in files:
        path = os.path.join(HERE, fname)
        text = substitute(open(path).read(), cfg)
        print(f"\n### {fname} ({profile} / {wh}) ###")
        for stmt in split_statements(text):
            label = " ".join(stmt.split())[:95]
            if dry_run:
                print(f"  [DRY]  {label}")
                continue
            state, err = run_sql(profile, wh, stmt)
            print(f"  [{state:>10}] {label}")
            if state != "SUCCEEDED":
                failed += 1
                print(f"             ERROR: {err}")
    return failed


def run_script(script, args, cfg, dry_run):
    path = os.path.join(HERE, script)
    cmd = ["bash", path] + args
    print(f"\n### {script} {' '.join(args)} ###")
    if dry_run:
        print(f"  [DRY] would run: {' '.join(cmd)}")
        return 0
    r = subprocess.run(cmd)
    return r.returncode


def run_py_stage(script, cfg, dry_run):
    """Run a python fixture stage with config.env exported into its environment."""
    path = os.path.join(HERE, script)
    print(f"\n### {script} (python) ###")
    if dry_run:
        print(f"  [DRY] would run: python3 {path}")
        return 0
    env = {**os.environ, **cfg}
    return subprocess.run([sys.executable, path], env=env).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stages", nargs="+",
                    help="legacy: all azure storage catalogs objects negative | "
                         "testcat build: testcat (or a single tc_* stage)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--azure-scope", default="both",
                    choices=["source", "target", "both"])
    args = ap.parse_args()
    cfg = load_config()

    # Expand meta-stages
    stages = []
    for s in args.stages:
        if s == "all":
            stages += ORDER
        elif s == "testcat":
            stages += TC_ORDER
        else:
            stages.append(s)

    rc = 0
    for stage in stages:
        if stage == "azure":
            rc += run_script("10_provision_azure.sh", [args.azure_scope], cfg, args.dry_run)
        elif stage == "storage":
            rc += run_script("20_uc_storage.sh", [], cfg, args.dry_run)
        elif stage == "tc_azure":
            rc += run_script("11_provision_azure_testcat.sh", [args.azure_scope], cfg, args.dry_run)
        elif stage == "tc_storage":
            rc += run_script("21_uc_storage_testcat.sh", [], cfg, args.dry_run)
        elif stage == "tc_files":
            rc += run_script("63_testcat_files.sh", [], cfg, args.dry_run)
        elif stage == "tc_reportonly":
            rc += run_py_stage("64_testcat_reportonly.py", cfg, args.dry_run)
        elif stage in SQL_STAGES:
            rc += run_sql_stage(SQL_STAGES[stage], cfg, args.dry_run)
        elif stage in TC_SQL_STAGES:
            rc += run_sql_stage(TC_SQL_STAGES[stage], cfg, args.dry_run)
        elif stage in TC_SQL_STAGES_TGT:
            rc += run_sql_stage(TC_SQL_STAGES_TGT[stage], cfg, args.dry_run,
                                profile=cfg["TGT_PROFILE"], wh=cfg["TGT_WAREHOUSE"])
        else:
            print(f"unknown stage: {stage}", file=sys.stderr); rc += 1
    print(f"\n=== done (failures/non-zero: {rc}) ===")
    sys.exit(1 if rc else 0)


if __name__ == "__main__":
    main()
