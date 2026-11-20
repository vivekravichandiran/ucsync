-- ai27_ucsync_testcatalog — v1.2.2 INCREMENTAL seed (one change of every delta kind).
-- Apply to the SOURCE only, AFTER a baseline migration, then re-run End-to-end Live.
-- Stage: `python3 fixtures/recreate.py tc_incremental_v122` (NOT part of the `testcat` meta-stage;
-- the .sh half seeds the volume-file changes). Every block is tagged #N = expected outcome:
--
--  #1  new table + new view on it              → Created
--  #2  new function                            → Created
--  #3  new table with a classic column mask    → Created, mask applied
--  #4  new table tagged ai27_uc_pii=EMAIL      → Created; the EXISTING catalog policy
--                                                ai27_ucsync_mask_email covers it
--  #5  ADD COLUMN on an existing table         → Updated (column added on target)
--  #6  CREATE OR REPLACE VIEW (new column)     → Replaced
--  #7  mask on an existing, unmasked column    → Updated (mask applied to the existing table)
--  #8  table tag on an existing table          → Updated (tag applied)
--  #9  new grant on an existing table          → grant APPLIED
--  #10 revoke on source                        → reported "Deleted in source"; NOT revoked on target
--  #11 drop a table on source                  → "Deleted in source"; target table kept
--  #12 overwrite an existing volume file       → re-copied        (.sh)
--  #13 new file in a new spaced folder         → copied           (.sh)
--  #14 everything else                         → Unchanged / grants SKIPPED / files skipped
-- Names are prefixed `incr2_` so the seed never collides with 68_testcat_incremental.sql.

-- #1 new table + new view on it
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.incr2_orders (
  order_id BIGINT COMMENT 'order id', region STRING, amount DECIMAL(12,2)
) USING DELTA COMMENT 'v1.2.2 incremental #1' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.incr2_orders VALUES (1,'US',10.00),(2,'EU',20.00);
CREATE OR REPLACE VIEW ai27_ucsync_testcatalog.core_tables.incr2_orders_v
  COMMENT 'v1.2.2 incremental #1 view' AS
  SELECT order_id, region, amount FROM ai27_ucsync_testcatalog.core_tables.incr2_orders;

-- #2 new function
CREATE OR REPLACE FUNCTION ai27_ucsync_testcatalog.functions.incr2_mask_last4(v STRING)
  RETURNS STRING COMMENT 'v1.2.2 incremental #2'
  RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE concat('****', right(v, 4)) END;

-- #3 new table with a classic column mask
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.incr2_cards (
  id INT, card_no STRING COMMENT 'classic-masked'
) USING DELTA COMMENT 'v1.2.2 incremental #3' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.incr2_cards VALUES (1,'4111111111111111');
ALTER TABLE ai27_ucsync_testcatalog.core_tables.incr2_cards
  ALTER COLUMN card_no SET MASK ai27_ucsync_testcatalog.functions.incr2_mask_last4;

-- #4 new table whose governed column tag is covered by the existing catalog ABAC policy
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.governed.incr2_contacts (
  id INT, contact_email STRING COMMENT 'ABAC-masked by ai27_ucsync_mask_email'
) USING DELTA COMMENT 'v1.2.2 incremental #4' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.governed.incr2_contacts VALUES (1,'a@example.com');
ALTER TABLE ai27_ucsync_testcatalog.governed.incr2_contacts
  ALTER COLUMN contact_email SET TAGS ('ai27_uc_pii'='EMAIL');

-- #5 ADD COLUMN on an existing (clean, ungoverned) table
ALTER TABLE ai27_ucsync_testcatalog.core_tables.departments
  ADD COLUMN cost_center STRING COMMENT 'v1.2.2 incremental #5';

-- #6 view definition changed (adds a column)
CREATE OR REPLACE VIEW ai27_ucsync_testcatalog.core_tables.emp_dynamic
  COMMENT 'Dynamic view (current_user + group membership) - v1.2.2 incremental #6' AS
  SELECT id, emp_name, dept,
         current_user() AS viewer,
         is_account_group_member('admins') AS is_admin,
         'v2' AS ver
  FROM ai27_ucsync_testcatalog.core_tables.employees;

-- #7 mask added to a pre-existing, already-migrated, unmasked column
ALTER TABLE ai27_ucsync_testcatalog.core_tables.employees
  ALTER COLUMN phone SET MASK ai27_ucsync_testcatalog.functions.mask_phone;

-- #8 table tag added to an existing table
ALTER TABLE ai27_ucsync_testcatalog.core_tables.orders_clustered
  SET TAGS ('ai27_uc_classification'='INTERNAL');

-- #9 new grant on an existing table
GRANT SELECT ON TABLE ai27_ucsync_testcatalog.core_tables.departments TO `wsmig_acc_group`;

-- #10 revoke on source (target keeps the grant — additive-only)
REVOKE SELECT ON TABLE ai27_ucsync_testcatalog.stage_.orders FROM `wsmig_acc_group`;

-- #11 table dropped on source (target keeps it)
DROP TABLE IF EXISTS ai27_ucsync_testcatalog.parallel_c.t_039;
