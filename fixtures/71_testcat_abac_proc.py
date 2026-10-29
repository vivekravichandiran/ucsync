#!/usr/bin/env python3
"""v1.2.1 fixtures for ai27_ucsync_testcatalog (SOURCE workspace).

Two import bugs from a customer BU run, rebuilt in the shape the customer had them.
Everything lands in the existing gold / silver / bronze schemas so the BYO target
needs no change — an incremental run over the current target exercises both fixes.

  Bug 1 - CATALOG-level ABAC policies whose MATCH COLUMNS alias comes from a
          hyphenated column name (AS `mc_sales-fleet-vol-mtd`). information_schema
          returns the alias unquoted, the import replayed it unquoted →
          INVALID_IDENTIFIER, and the catalog-scoped failure dropped every fresh
          table of the run fail-closed.
          Functions: bronze.ai27_mask_bsfd_double / ai27_mask_spdi / ai27_rf_region
          Tags:      key-only governed tags (account level), one per policy
          Tables:    gold.`hyphen-daily-sales-report` (2 masked DOUBLE cols + 1 row-
                     filter col), gold.`hyphen-fortnight-report` (1 masked STRING col)
          Policies:  2 column masks + 1 row filter (USING COLUMNS takes the alias)
          Fresh:     bronze.`hyphen-plain-1..5` — untagged; NEW on the target, so with
                     the bug they are dropped fail-closed, with the fix they survive.
  Bug 2 - SQL stored procedures (CREATE PROCEDURE … AS BEGIN … END) were rebuilt as
          CREATE FUNCTION … RETURNS NULL RETURN BEGIN … → PARSE_SYNTAX_ERROR.
          silver: 4 customer-shaped procedures (BEGIN WITH …, BEGIN DECLARE …, a
          comment line before WITH) + 1 covering OUT / INOUT / DEFAULT, labels,
          WHILE, CASE statement, nested BEGIN, and ';' / END inside a string.
          Grants: EXECUTE to a group and an SPN; one owned by an SPN.

Statements are sent whole (procedure bodies contain ';'). Idempotent: OR REPLACE /
IF NOT EXISTS; the policies are dropped and recreated. After building, the source
metadata is checked for the exact shapes the bugs need.
Run: python3 fixtures/recreate.py tc_abac_proc
"""
import json, os, subprocess, sys, time

P = os.environ.get("SRC_PROFILE", "source_ws")
WH = os.environ.get("SRC_WAREHOUSE", "")
CAT = os.environ.get("TESTCAT_CATALOG", "ai27_ucsync_testcatalog")
ADMIN = os.environ.get("TC_USER_ALL", "abhishek.iyer@databricks.com")
GROUP = os.environ.get("TC_GROUP_READER", "wsmig_acc_group")
SPN_READ = os.environ.get("TC_ACC_SPN_2", "7782a88b-efd6-4a9a-9f17-92a8fe815b7a")
SPN_OWN = os.environ.get("TC_ACC_SPN_1", "bfadccbc-4805-474d-8698-ebafc58ebc5e")

TAG_DBL = "abac_colmask_ai27_ucsync_bsfd_double"
TAG_STR = "abac_colmask_ai27_ucsync_spdi"
TAG_RF = "abac_rls_ai27_ucsync_region"
DAILY = f"{CAT}.gold.`hyphen-daily-sales-report`"
FORTNIGHT = f"{CAT}.gold.`hyphen-fortnight-report`"


def sql(stmt):
    """Run one whole statement; return (state, error, rows)."""
    body = {"warehouse_id": WH, "statement": stmt, "wait_timeout": "50s"}
    p = subprocess.run(["databricks", "api", "post", "/api/2.0/sql/statements",
                        "-p", P, "--json", json.dumps(body)], capture_output=True, text=True)
    if p.returncode != 0:
        return "CLI_ERROR", p.stderr[:300], []
    out = json.loads(p.stdout)
    sid = out.get("statement_id")
    while out.get("status", {}).get("state") in ("PENDING", "RUNNING") and sid:
        time.sleep(2)
        g = subprocess.run(["databricks", "api", "get", f"/api/2.0/sql/statements/{sid}",
                            "-p", P], capture_output=True, text=True)
        out = json.loads(g.stdout)
    st = out.get("status", {})
    rows = (out.get("result") or {}).get("data_array") or []
    return st.get("state"), st.get("error", {}).get("message", ""), rows


def ensure_governed_tag(key):
    """Key-only account-level governed tag (no allowed values), like the customer's."""
    p = subprocess.run(["databricks", "api", "post", "/api/2.1/tag-policies", "-p", P,
                        "--json", json.dumps({"tag_key": key})],
                       capture_output=True, text=True)
    err = (p.stderr + p.stdout).upper()
    ok = p.returncode == 0 or "ALREADY" in err
    print(f"  [{'SUCCEEDED' if ok else 'FAILED':>10}] governed tag {key}")
    if not ok:
        print(f"             ERROR: {(p.stderr or p.stdout)[:300]}")
    return 0 if ok else 1


# ----------------------------------------------------------------------------- Bug 1
MASK_FUNCTIONS = [
    f"""CREATE OR REPLACE FUNCTION {CAT}.bronze.ai27_mask_bsfd_double(v DOUBLE)
RETURNS DOUBLE
COMMENT 'Bug 1: DOUBLE mask used by a catalog ABAC policy with a hyphenated alias'
RETURN CASE WHEN is_account_group_member('{GROUP}') THEN v ELSE NULL END""",
    f"""CREATE OR REPLACE FUNCTION {CAT}.bronze.ai27_mask_spdi(v STRING)
RETURNS STRING
COMMENT 'Bug 1: STRING mask used by a catalog ABAC policy with a hyphenated alias'
RETURN CASE WHEN is_account_group_member('{GROUP}') THEN v ELSE '****' END""",
    f"""CREATE OR REPLACE FUNCTION {CAT}.bronze.ai27_rf_region(r STRING)
RETURNS BOOLEAN
COMMENT 'Bug 1: row filter used by a catalog ABAC policy with a hyphenated alias'
RETURN r <> 'BLOCKED' OR is_account_group_member('{GROUP}')""",
]

TAGGED_TABLES = [
    f"""CREATE TABLE IF NOT EXISTS {DAILY} (
  id INT,
  `sales-fleet-vol-mtd` DOUBLE COMMENT 'masked by catalog ABAC (hyphenated alias)',
  `sales-fleet-vol-yest` DOUBLE COMMENT 'masked by the same policy (same tag)',
  `region-code` STRING COMMENT 'row-filtered by catalog ABAC (hyphenated alias)'
) USING DELTA COMMENT 'Bug 1: hyphenated columns protected by catalog ABAC'""",
    f"INSERT OVERWRITE {DAILY} VALUES (1, 120.5, 98.25, 'WEST'), (2, 80.0, 70.75, 'BLOCKED')",
    f"""CREATE TABLE IF NOT EXISTS {FORTNIGHT} (
  `cust-id` STRING COMMENT 'masked by catalog ABAC (hyphenated alias)',
  amount DOUBLE
) USING DELTA COMMENT 'Bug 1: hyphenated column protected by catalog ABAC'""",
    f"INSERT OVERWRITE {FORTNIGHT} VALUES ('C-1001', 10.0), ('C-1002', 20.0)",
    f"ALTER TABLE {DAILY} ALTER COLUMN `sales-fleet-vol-mtd` SET TAGS ('{TAG_DBL}')",
    f"ALTER TABLE {DAILY} ALTER COLUMN `sales-fleet-vol-yest` SET TAGS ('{TAG_DBL}')",
    f"ALTER TABLE {DAILY} ALTER COLUMN `region-code` SET TAGS ('{TAG_RF}')",
    f"ALTER TABLE {FORTNIGHT} ALTER COLUMN `cust-id` SET TAGS ('{TAG_STR}')",
]

# DROP POLICY has no IF EXISTS — a not-found drop on the first run is tolerated.
DROP_POLICIES = [f"DROP POLICY {k} ON CATALOG {CAT}" for k in (TAG_DBL, TAG_STR, TAG_RF)]
POLICIES = [
    f"""CREATE POLICY {TAG_DBL} ON CATALOG {CAT}
  COLUMN MASK {CAT}.bronze.ai27_mask_bsfd_double
  TO `account users` EXCEPT `{ADMIN}`
  FOR TABLES MATCH COLUMNS has_tag('{TAG_DBL}') AS `mc_sales-fleet-vol-mtd`
  ON COLUMN `mc_sales-fleet-vol-mtd`""",
    f"""CREATE POLICY {TAG_STR} ON CATALOG {CAT}
  COLUMN MASK {CAT}.bronze.ai27_mask_spdi
  TO `account users` EXCEPT `{ADMIN}`
  FOR TABLES MATCH COLUMNS has_tag('{TAG_STR}') AS `mc_cust-id`
  ON COLUMN `mc_cust-id`""",
    f"""CREATE POLICY {TAG_RF} ON CATALOG {CAT}
  ROW FILTER {CAT}.bronze.ai27_rf_region
  TO `account users` EXCEPT `{ADMIN}`
  FOR TABLES MATCH COLUMNS has_tag('{TAG_RF}') AS `mc_region-code`
  USING COLUMNS (`mc_region-code`)""",
]

# Untagged, hyphen-named like the customer's dropped bronze tables. New on the target
# in the incremental run → in the catalog policies' fail-closed scope.
FRESH_TABLES = []
for n in range(1, 6):
    t = f"{CAT}.bronze.`hyphen-plain-{n}`"
    FRESH_TABLES += [
        f"CREATE TABLE IF NOT EXISTS {t} (id INT, note STRING) USING DELTA "
        f"COMMENT 'Bug 1: untagged fresh table in a catalog-ABAC catalog'",
        f"INSERT OVERWRITE {t} VALUES ({n}, 'row {n}')",
    ]

# ----------------------------------------------------------------------------- Bug 2
SRC = f"{CAT}.silver.proc_dashboard_src"
LOG = f"{CAT}.silver.proc_audit_log"
PROC_TABLES = [
    f"""CREATE TABLE IF NOT EXISTS {SRC} (
  emailid STRING, stage_name STRING, site_code STRING, state STRING, region STRING,
  rocode STRING, form_id STRING, created_on TIMESTAMP
) USING DELTA COMMENT 'Bug 2: read by the silver procedures'""",
    f"""INSERT OVERWRITE {SRC} VALUES
  ('a@corp.com', 'EOI', 'S1', 'MH', 'WEST', 'RO1', 'F1', current_timestamp()),
  ('a@corp.com', 'LOI', 'S2', 'GJ', 'WEST', 'RO1', 'F2', current_timestamp()),
  ('b@corp.com', 'EOI', 'S3', 'KA', 'SOUTH', 'RO2', 'F3', current_timestamp())""",
    f"""CREATE TABLE IF NOT EXISTS {LOG} (
  operation_mode STRING, src_object_key BIGINT, batch_id STRING, status STRING,
  logged_at TIMESTAMP
) USING DELTA COMMENT 'Bug 2: written by the audit-logging procedure'""",
]

PROCEDURES = [
    # BEGIN WITH … (the customer's get_* shape: a result-set query in a compound body)
    f"""CREATE OR REPLACE PROCEDURE {CAT}.silver.get_dashboard_counts(p_emailid STRING)
LANGUAGE SQL
SQL SECURITY INVOKER
COMMENT 'Bug 2: BEGIN WITH … result-set procedure'
AS BEGIN
    WITH base AS (
        SELECT stage_name, site_code FROM {SRC} WHERE emailid = p_emailid
    ),
    counts AS (
        SELECT stage_name, count(DISTINCT site_code) AS sites FROM base GROUP BY stage_name
    )
    SELECT * FROM counts ORDER BY stage_name;
END""",
    f"""CREATE OR REPLACE PROCEDURE {CAT}.silver.get_stage_sites(p_emailid STRING, p_stage_name STRING)
LANGUAGE SQL
SQL SECURITY INVOKER
AS BEGIN
    WITH s AS (
        SELECT site_code, state, region FROM {SRC}
        WHERE emailid = p_emailid AND stage_name = p_stage_name
    )
    SELECT state, region, collect_list(site_code) AS sites FROM s GROUP BY state, region;
END""",
    # BEGIN DECLARE … (the customer's sp_*audit_logging shape)
    f"""CREATE OR REPLACE PROCEDURE {CAT}.silver.sp_audit_logging(
    in_operation_mode STRING, in_src_object_key BIGINT, in_batch_id STRING)
LANGUAGE SQL
SQL SECURITY INVOKER
COMMENT 'Bug 2: BEGIN DECLARE … audit-logging procedure'
AS BEGIN
    DECLARE v_status STRING DEFAULT 'STARTED';
    DECLARE v_now TIMESTAMP DEFAULT current_timestamp();
    IF in_operation_mode = 'END' THEN
        SET v_status = 'COMPLETED';
    ELSEIF in_operation_mode = 'FAIL' THEN
        SET v_status = 'FAILED';
    END IF;
    INSERT INTO {LOG} VALUES (in_operation_mode, in_src_object_key, in_batch_id, v_status, v_now);
END""",
    # a comment line between BEGIN and WITH (the customer error was at line 6)
    f"""CREATE OR REPLACE PROCEDURE {CAT}.silver.sp_nudge_recipients(p_form_id STRING, p_rocode STRING)
LANGUAGE SQL
SQL SECURITY INVOKER
AS BEGIN
  -- recipients for a form; region head first; END of comment;
  WITH r AS (
    SELECT emailid, region FROM {SRC} WHERE form_id = p_form_id OR rocode = p_rocode
  )
  SELECT DISTINCT emailid, region FROM r;
END""",
    # OUT / INOUT / DEFAULT + label, WHILE, CASE stmt, nested BEGIN, CASE expr, ';' in string
    f"""CREATE OR REPLACE PROCEDURE {CAT}.silver.sp_loop_and_modes(
    IN p_n INT, OUT p_total INT, INOUT p_note STRING, IN p_step INT DEFAULT 1)
LANGUAGE SQL
SQL SECURITY INVOKER
COMMENT 'Bug 2: OUT/INOUT/DEFAULT params and nested scripting blocks'
AS outer_blk: BEGIN
    DECLARE i INT DEFAULT 0;
    SET p_total = 0;
    WHILE i < p_n DO
        SET i = i + p_step;
        SET p_total = p_total + i;
    END WHILE;
    CASE
        WHEN p_total > 10 THEN SET p_note = concat(p_note, '; big; END');
        ELSE SET p_note = concat(p_note, '; small');
    END CASE;
    inner_blk: BEGIN
        SET p_note = CASE WHEN p_note IS NULL THEN 'none' ELSE p_note END;
    END inner_blk;
END outer_blk""",
]

PROC_GRANTS = [
    f"GRANT EXECUTE ON PROCEDURE {CAT}.silver.get_dashboard_counts TO `{GROUP}`",
    f"GRANT EXECUTE ON PROCEDURE {CAT}.silver.get_stage_sites TO `{SPN_READ}`",
    f"GRANT EXECUTE ON PROCEDURE {CAT}.silver.sp_loop_and_modes TO `{GROUP}`",
    f"ALTER PROCEDURE {CAT}.silver.sp_audit_logging OWNER TO `{SPN_OWN}`",
]


def run(label, statements, tolerate_missing=False):
    bad = 0
    for stmt in statements:
        state, err, _ = sql(stmt)
        head = " ".join(stmt.split())[:95]
        if state != "SUCCEEDED" and tolerate_missing and "NOT_FOUND" in err.upper():
            state = "ABSENT"
        print(f"  [{state:>10}] {head}")
        if state not in ("SUCCEEDED", "ABSENT"):
            bad += 1
            print(f"             ERROR: {err[:300]}")
    print(f"{label}: {len(statements) - bad}/{len(statements)} ok")
    return bad


def verify():
    """Confirm the source metadata carries the exact shapes each bug needs."""
    bad = 0
    print("\n=== source metadata check ===")
    state, err, rows = sql(
        f"SELECT policy_name, match_columns FROM {CAT}.information_schema."
        f"abac_policy_definitions WHERE policy_name IN ('{TAG_DBL}','{TAG_STR}','{TAG_RF}')")
    found = {r[0]: r[1] for r in rows}
    for name, alias in ((TAG_DBL, "mc_sales-fleet-vol-mtd"), (TAG_STR, "mc_cust-id"),
                        (TAG_RF, "mc_region-code")):
        mc = found.get(name) or f"<missing: {state} {err[:100]}>"
        ok = alias in mc
        bad += 0 if ok else 1
        print(f"  Bug 1  {'SHAPE OK' if ok else 'MISSING '}  {name}: {mc}")
    state, err, rows = sql(
        f"SELECT routine_name, routine_type, data_type FROM {CAT}.information_schema.routines "
        "WHERE routine_schema = 'silver' AND routine_type = 'PROCEDURE' ORDER BY 1")
    names = [r[0] for r in rows]
    for want in ("get_dashboard_counts", "get_stage_sites", "sp_audit_logging",
                 "sp_nudge_recipients", "sp_loop_and_modes"):
        ok = want in names
        bad += 0 if ok else 1
        print(f"  Bug 2  {'PROCEDURE' if ok else 'MISSING  '}  silver.{want}")
    state, err, rows = sql(f"CALL {CAT}.silver.get_dashboard_counts('a@corp.com')")
    ok = state == "SUCCEEDED" and len(rows) == 2
    bad += 0 if ok else 1
    print(f"  Bug 2  {'CALL OK  ' if ok else 'CALL FAIL'}  get_dashboard_counts -> {rows or err[:200]}")
    return bad


if __name__ == "__main__":
    print("governed tags (account level, key-only)")
    bad = sum(ensure_governed_tag(k) for k in (TAG_DBL, TAG_STR, TAG_RF))
    bad += run("bug 1 mask / filter functions", MASK_FUNCTIONS)
    bad += run("bug 1 tagged tables", TAGGED_TABLES)
    bad += run("bug 1 drop prior policies", DROP_POLICIES, tolerate_missing=True)
    bad += run("bug 1 catalog ABAC policies", POLICIES)
    bad += run("bug 1 fresh untagged tables", FRESH_TABLES)
    bad += run("bug 2 procedure tables", PROC_TABLES)
    bad += run("bug 2 procedures", PROCEDURES)
    bad += run("bug 2 procedure grants / owner", PROC_GRANTS)
    bad += verify()
    print(f"=== abac-alias + procedure stage done (problems: {bad}) ===")
    sys.exit(1 if bad else 0)
