-- ai27_ucsync_testcatalog — INCREMENTAL seed (run AFTER a baseline migration, then re-migrate).
-- Applied to the SOURCE. Covers every incremental case we've discussed + every bug we fixed.
-- Each block is tagged with its case + expected outcome on the incremental run.
-- Stage: `python3 fixtures/recreate.py tc_incremental`  (NOT part of the `testcat` meta-stage).
-- The new streaming table + event log (#6) come from a Lakeflow pipeline created out of band
-- (see fixtures/64-style pipeline: ai27_ucsync_testcat_pipeline2 → st_orders + sdp_event_log2).

-- =====================================================================================
-- PART A — NEW objects (expect CREATED_NEW on the incremental)
-- =====================================================================================

-- (#1) new table — digit-leading name (Bug #5) + a classic column mask
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.`4g_cust` (
  id INT, ssn STRING COMMENT 'classic-masked on a digit-named table'
) USING DELTA COMMENT 'incremental #1 + Bug #5' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.`4g_cust` VALUES (1,'123-45-6789');
ALTER TABLE ai27_ucsync_testcatalog.core_tables.`4g_cust`
  ALTER COLUMN ssn SET MASK ai27_ucsync_testcatalog.functions.mask_ssn;

-- (Bug #5) new table — hyphenated name
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.`ord-2024` (
  order_id INT, amount DECIMAL(12,2)
) USING DELTA COMMENT 'Bug #5 hyphen name' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.`ord-2024` VALUES (1, 100.00);

-- (Bug #7) new table — delta.dataSkippingStatsColumns + CLUSTER BY (the 7-table customer failure)
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.late_cluster_tbl (
  id BIGINT, region STRING, note STRING
) USING DELTA CLUSTER BY (note)
COMMENT 'Bug #7 dataSkippingStatsColumns must survive'
TBLPROPERTIES ('delta.dataSkippingStatsColumns'='region,note', 'ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.late_cluster_tbl VALUES (1,'US','n1'),(2,'EU','n2');

-- (Bug #9) new table — GEOMETRY + GEOGRAPHY (structure only)
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.geo_tbl (
  id INT, g GEOMETRY(4326), gg GEOGRAPHY(4326)
) USING DELTA COMMENT 'Bug #9 spatial types' TBLPROPERTIES ('ai27_uc.fixture'='true');

-- new table — delta.columnMapping.mode=name (preserved-path)
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.colmap_tbl (
  id INT, name STRING
) USING DELTA COMMENT 'columnMapping.mode=name'
TBLPROPERTIES ('delta.columnMapping.mode'='name', 'ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.colmap_tbl VALUES (1,'alpha');

-- (#2) new view — with inline -- comments (Bug #11/#14)
CREATE OR REPLACE VIEW ai27_ucsync_testcatalog.core_tables.comment_view
  COMMENT 'incremental #2 + Bug #11/#14' AS
  SELECT id,        -- employee surrogate key
         emp_name,  -- full name
         dept       -- partition column
  FROM ai27_ucsync_testcatalog.core_tables.employees;

-- (#6-part) new materialized view (report-only; the streaming table + event log come from
-- the new pipeline). Over the clean departments dim so no governance interaction.
CREATE MATERIALIZED VIEW IF NOT EXISTS ai27_ucsync_testcatalog.advanced.mv_dept_roster
  COMMENT 'incremental #6 new MV'
  AS SELECT dept_id, dept_name FROM ai27_ucsync_testcatalog.core_tables.departments;

-- (#11) new CATALOG ABAC policy + a new table whose new governed tag it covers
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.governed.abac_ssn_tbl (
  id INT, ssn STRING COMMENT 'ABAC-masked via a NEW catalog policy'
) USING DELTA COMMENT 'incremental #11 new-policy target' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.governed.abac_ssn_tbl VALUES (1,'123-45-6789');
ALTER TABLE ai27_ucsync_testcatalog.governed.abac_ssn_tbl ALTER COLUMN ssn SET TAGS ('ai27_uc_pii'='SSN');
CREATE POLICY ai27_ucsync_mask_ssn_cat ON CATALOG ai27_ucsync_testcatalog
  COLUMN MASK ai27_ucsync_testcatalog.functions.mask_ssn
  TO `account users`
  FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_pii','SSN') AS c ON COLUMN c;

-- =====================================================================================
-- PART B — MUTATIONS of baseline-migrated objects
-- =====================================================================================

-- (#3) plain column addition to an existing table (schema evolution, no governance)
ALTER TABLE ai27_ucsync_testcatalog.core_tables.employees ADD COLUMN nickname STRING COMMENT 'incremental #3';

-- (#9) new column on an existing table WITH a column mask (FEAT-3)
ALTER TABLE ai27_ucsync_testcatalog.core_tables.employees ADD COLUMN ssn2 STRING COMMENT 'incremental #9';
ALTER TABLE ai27_ucsync_testcatalog.core_tables.employees
  ALTER COLUMN ssn2 SET MASK ai27_ucsync_testcatalog.functions.mask_ssn;

-- (#10) new column on an existing table WITH a same-tag governed tag → auto-covered by the
-- EXISTING catalog mask_email policy (FEAT-3 same-tag auto-cover)
ALTER TABLE ai27_ucsync_testcatalog.governed.abac_mask_tbl ADD COLUMN email2 STRING COMMENT 'incremental #10';
ALTER TABLE ai27_ucsync_testcatalog.governed.abac_mask_tbl
  ALTER COLUMN email2 SET TAGS ('ai27_uc_pii'='EMAIL');

-- (A1 / bug 3a) mask added to a PRE-EXISTING, already-migrated column (was unmasked at baseline).
-- Expect: incremental APPLIES the mask to the existing column (Phase 1c), not a false "Updated".
ALTER TABLE ai27_ucsync_testcatalog.core_tables.employees
  ALTER COLUMN phone SET MASK ai27_ucsync_testcatalog.functions.mask_phone;

-- (A2 / bug 5) row filter added to a PRE-EXISTING, already-migrated table (had none at baseline).
ALTER TABLE ai27_ucsync_testcatalog.core_tables.orders_clustered
  SET ROW FILTER ai27_ucsync_testcatalog.functions.region_filter ON (region);

-- (#7) view DEFINITION updated on an existing view → REPLACED (add a column)
CREATE OR REPLACE VIEW ai27_ucsync_testcatalog.core_tables.emp_dynamic
  COMMENT 'Dynamic view (updated on incremental #7)' AS
  SELECT id, emp_name, dept,
         current_user() AS viewer,
         is_account_group_member('admins') AS is_admin,
         'v2' AS ver
  FROM ai27_ucsync_testcatalog.core_tables.employees;

-- (#2 / F2) FUNCTION body changed → must CREATE OR REPLACE on incremental (not no-op)
CREATE OR REPLACE FUNCTION ai27_ucsync_testcatalog.functions.mask_phone(v STRING)
  RETURNS STRING COMMENT 'INCREMENTAL body change'
  RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE concat('INCR-***-', right(v,4)) END;

-- (#4 / A3) column DELETED on source (needs columnMapping). Expect on target:
-- "Skipped — column deleted on source; not dropped on target" (non-destructive).
ALTER TABLE ai27_ucsync_testcatalog.core_tables.constraints_tbl
  SET TBLPROPERTIES ('delta.columnMapping.mode'='name');
ALTER TABLE ai27_ucsync_testcatalog.core_tables.constraints_tbl DROP COLUMN note;

-- (#5 / A4) column TYPE CHANGED on source (needs typeWidening). Expect on target:
-- "Skipped — column type changed on source; not altered on target (out of scope)".
ALTER TABLE ai27_ucsync_testcatalog.governed.filter_tbl
  SET TBLPROPERTIES ('delta.enableTypeWidening'='true');
ALTER TABLE ai27_ucsync_testcatalog.governed.filter_tbl ALTER COLUMN id TYPE BIGINT;

-- (#8) new ACLs → GRANT_ADDED
GRANT SELECT ON TABLE ai27_ucsync_testcatalog.core_tables.departments TO `idris.chakera@databricks.com`;
GRANT SELECT ON TABLE ai27_ucsync_testcatalog.governed.mask_tbl TO `wsmig_acc_group`;

-- (GRANT_REMOVED) a revoke → REPORTED only; target grant left intact (additive-only)
REVOKE SELECT ON TABLE ai27_ucsync_testcatalog.core_tables.employees FROM `sanket.kelkar@databricks.com`;

-- (change b) TYPE change on a table that ALSO has governance (classic masks) → the report
-- must read "Updated — <type change> not applied", i.e. the skip is surfaced alongside the
-- applied governance, not silent. mask_tbl has masks on ssn/email; widen its id.
ALTER TABLE ai27_ucsync_testcatalog.governed.mask_tbl
  SET TBLPROPERTIES ('delta.enableTypeWidening'='true');
ALTER TABLE ai27_ucsync_testcatalog.governed.mask_tbl ALTER COLUMN id TYPE BIGINT;

-- (Task-10 / F1) governance FAILURE on a PRE-EXISTING table must NOT drop it.
-- New mask referencing the un-migrated ai_27 function on the pre-existing secret_fin → on target
-- the mask apply fails-closed, but secret_fin pre-exists → NOT dropped, marked FAILURE, job RED.
ALTER TABLE ai27_ucsync_testcatalog.restricted.secret_fin
  ALTER COLUMN acct SET MASK ai_27.sec.mask_ext;
