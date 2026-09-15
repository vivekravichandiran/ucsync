-- ai27_ucsync_testcatalog — INCREMENTAL seed (run AFTER a baseline migration, then re-migrate).
-- Applied to the SOURCE. Do NOT run this before the baseline: it must mutate objects the
-- baseline already migrated. Each block notes the bug it exercises + the expected outcome
-- on the incremental run. Stage: `python3 fixtures/recreate.py tc_incremental`.
-- (Deliberately NOT part of the `testcat` meta-stage.)

-- =====================================================================================
-- PART A — NEW objects that fill static-coverage gaps (expect CREATED on the incremental)
-- =====================================================================================

-- Bug #5: digit-leading table name (must be backtick-quoted; SHOW CREATE emits mixed quoting)
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.`4g_cust` (
  id INT, ssn STRING COMMENT 'classic-masked on a digit-named table'
) USING DELTA COMMENT 'Bug #5: digit-leading name' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.`4g_cust` VALUES (1,'123-45-6789');
ALTER TABLE ai27_ucsync_testcatalog.core_tables.`4g_cust`
  ALTER COLUMN ssn SET MASK ai27_ucsync_testcatalog.functions.mask_ssn;

-- Bug #5: hyphenated table name
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.`ord-2024` (
  order_id INT, amount DECIMAL(12,2)
) USING DELTA COMMENT 'Bug #5: hyphenated name' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.`ord-2024` VALUES (1, 100.00);

-- Bug #7: delta.dataSkippingStatsColumns + CLUSTER BY (the 7-table customer failure — must be preserved)
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.late_cluster_tbl (
  id BIGINT, region STRING, note STRING
) USING DELTA CLUSTER BY (note)
COMMENT 'Bug #7: dataSkippingStatsColumns must survive'
TBLPROPERTIES ('delta.dataSkippingStatsColumns'='region,note', 'ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.late_cluster_tbl VALUES (1,'US','n1'),(2,'EU','n2');

-- Bug #9: GEOMETRY + GEOGRAPHY columns (works on serverless/current runtime)
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.geo_tbl (
  id INT, g GEOMETRY(4326), gg GEOGRAPHY(4326)
) USING DELTA COMMENT 'Bug #9: spatial types' TBLPROPERTIES ('ai27_uc.fixture'='true');
-- structure-only (tool migrates structure, not data); spatial constructors vary by runtime.

-- delta.columnMapping.mode = name (preserved-path; common in customer estates)
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.colmap_tbl (
  id INT, name STRING
) USING DELTA COMMENT 'columnMapping.mode=name (preserved)'
TBLPROPERTIES ('delta.columnMapping.mode'='name', 'ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.colmap_tbl VALUES (1,'alpha');

-- Bug #11/#14: a view whose stored SQL carries inline -- comments (comment-preserving splitter +
-- 2-part-name requalification on the tool's export path)
CREATE OR REPLACE VIEW ai27_ucsync_testcatalog.core_tables.comment_view
  COMMENT 'Bug #11/#14: view with inline comments' AS
  SELECT id,          -- employee surrogate key
         emp_name,    -- full name
         dept         -- partition column
  FROM ai27_ucsync_testcatalog.core_tables.employees;

-- =====================================================================================
-- PART B — MUTATIONS of baseline objects (exercise incremental governance / schema-evolution)
-- =====================================================================================

-- A1 (bug 3a): add a column mask to an ALREADY-MIGRATED column (was unmasked at baseline).
-- Expect: incremental applies the mask to the pre-existing column (Phase 1c), fail-closed.
ALTER TABLE ai27_ucsync_testcatalog.core_tables.employees
  ALTER COLUMN phone SET MASK ai27_ucsync_testcatalog.functions.mask_phone;

-- A2 (bug 5): add a row filter to an ALREADY-MIGRATED table (had none at baseline).
-- Expect: incremental applies the row filter to the pre-existing table.
ALTER TABLE ai27_ucsync_testcatalog.core_tables.orders_clustered
  SET ROW FILTER ai27_ucsync_testcatalog.functions.region_filter ON (region);

-- FEAT-3: schema evolution + governance replay.
--   (a) new column with a CLASSIC mask (expect ADD COLUMN + mask on incremental).
ALTER TABLE ai27_ucsync_testcatalog.core_tables.employees ADD COLUMN ssn2 STRING COMMENT 'incremental col';
ALTER TABLE ai27_ucsync_testcatalog.core_tables.employees
  ALTER COLUMN ssn2 SET MASK ai27_ucsync_testcatalog.functions.mask_ssn;
--   (b) new SAME-TAG column auto-covered by the existing CATALOG ABAC mask_email policy.
ALTER TABLE ai27_ucsync_testcatalog.governed.abac_mask_tbl ADD COLUMN email2 STRING COMMENT 'incremental col';
ALTER TABLE ai27_ucsync_testcatalog.governed.abac_mask_tbl
  ALTER COLUMN email2 SET TAGS ('ai27_uc_pii'='EMAIL');

-- #2 / F2: change a FUNCTION body. Expect: incremental REPLACES it (CREATE OR REPLACE), not no-op.
CREATE OR REPLACE FUNCTION ai27_ucsync_testcatalog.functions.mask_phone(v STRING)
  RETURNS STRING COMMENT 'INCREMENTAL body change'
  RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE concat('INCR-***-', right(v,4)) END;

-- A3 (item 2): DROP a column on source (needs columnMapping). Expect on target:
-- "Skipped — column deleted on source; not dropped on target" (non-destructive).
ALTER TABLE ai27_ucsync_testcatalog.core_tables.constraints_tbl
  SET TBLPROPERTIES ('delta.columnMapping.mode'='name');
ALTER TABLE ai27_ucsync_testcatalog.core_tables.constraints_tbl DROP COLUMN note;

-- A4 (item 4): CHANGE a column type on source (needs typeWidening). Expect on target:
-- "Skipped — column type changed on source; not altered on target (out of scope)".
ALTER TABLE ai27_ucsync_testcatalog.governed.filter_tbl
  SET TBLPROPERTIES ('delta.enableTypeWidening'='true');
ALTER TABLE ai27_ucsync_testcatalog.governed.filter_tbl ALTER COLUMN id TYPE BIGINT;

-- Task-1: GRANT_ADDED (new grant → applied on incremental).
GRANT SELECT ON TABLE ai27_ucsync_testcatalog.core_tables.departments TO `idris.chakera@databricks.com`;
-- Task-1: GRANT_REMOVED (revoked on source → REPORTED only; target grant left intact, additive-only).
REVOKE SELECT ON TABLE ai27_ucsync_testcatalog.core_tables.employees FROM `sanket.kelkar@databricks.com`;

-- Task-10 / F1: governance failure on a PRE-EXISTING table must NOT drop it.
-- New mask referencing the un-migrated ai_27 function → on target the mask apply fails-closed,
-- but departments pre-exists → NOT dropped, marked FAILURE, job RED.
ALTER TABLE ai27_ucsync_testcatalog.core_tables.departments
  ALTER COLUMN dept_name SET MASK ai_27.sec.mask_ext;

-- #18: the ai_27 negatives (core_tables.ext_masked, governed.abac_ext) are unchanged here and
-- stay FAILURE on the incremental (re-attempted, never flipped to SUCCESS) — no seed needed.
