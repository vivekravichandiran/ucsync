-- ai27_ucsync_testcatalog.governed — classic masks/row filters + governed tags +
-- ABAC policies at CATALOG (inherited), SCHEMA (with EXCEPT), and TABLE scope.
-- Account-level governed tags ai27_uc_pii / ai27_uc_row_access / ai27_uc_classification
-- already exist. NOTE: CREATE POLICY has no IF NOT EXISTS — this stage is first-run clean
-- (drop the policies before re-running the governed stage on an existing catalog).

-- ===== classic column masks =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.governed.mask_tbl (
  id INT, ssn STRING COMMENT 'classic mask_ssn', email STRING COMMENT 'classic mask_email'
) USING DELTA COMMENT 'Classic column masks' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.governed.mask_tbl VALUES
  (1,'123-45-6789','alice@corp.com'), (2,'222-33-4444','bob@corp.com');
ALTER TABLE ai27_ucsync_testcatalog.governed.mask_tbl
  ALTER COLUMN ssn SET MASK ai27_ucsync_testcatalog.functions.mask_ssn;
ALTER TABLE ai27_ucsync_testcatalog.governed.mask_tbl
  ALTER COLUMN email SET MASK ai27_ucsync_testcatalog.functions.mask_email;

-- ===== classic row filter =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.governed.filter_tbl (
  id INT, name STRING, dept STRING COMMENT 'row-filtered by dept_filter'
) USING DELTA COMMENT 'Classic row filter' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.governed.filter_tbl VALUES
  (1,'Alice','ENGINEERING'), (2,'Carol','PUBLIC'), (3,'Dan','FINANCE');
ALTER TABLE ai27_ucsync_testcatalog.governed.filter_tbl
  SET ROW FILTER ai27_ucsync_testcatalog.functions.dept_filter ON (dept);

-- ===== ABAC mask target (email=catalog, phone=schema+EXCEPT, acct=table scope) =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.governed.abac_mask_tbl (
  id INT,
  email STRING COMMENT 'ABAC mask via catalog policy (tag EMAIL)',
  phone STRING COMMENT 'ABAC mask via schema policy (tag PHONE, EXCEPT admin)',
  acct STRING COMMENT 'ABAC mask via table policy (tag BANK_ACCOUNT)'
) USING DELTA COMMENT 'ABAC column-mask target' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.governed.abac_mask_tbl VALUES
  (1,'alice@corp.com','415-555-1001','ACCT-99887766'),
  (2,'bob@corp.com','415-555-1002','ACCT-11223344');
ALTER TABLE ai27_ucsync_testcatalog.governed.abac_mask_tbl ALTER COLUMN email SET TAGS ('ai27_uc_pii'='EMAIL');
ALTER TABLE ai27_ucsync_testcatalog.governed.abac_mask_tbl ALTER COLUMN phone SET TAGS ('ai27_uc_pii'='PHONE');
ALTER TABLE ai27_ucsync_testcatalog.governed.abac_mask_tbl ALTER COLUMN acct  SET TAGS ('ai27_uc_pii'='BANK_ACCOUNT');

-- ===== ABAC row-filter target (region tag restricted, catalog policy) =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.governed.abac_filter_tbl (
  id INT, name STRING, region STRING COMMENT 'ABAC row filter via catalog policy (tag restricted)'
) USING DELTA COMMENT 'ABAC row-filter target' TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.governed.abac_filter_tbl VALUES
  (1,'Alice','US'), (2,'Bob','EU'), (3,'Carol','US');
ALTER TABLE ai27_ucsync_testcatalog.governed.abac_filter_tbl ALTER COLUMN region SET TAGS ('ai27_uc_row_access'='restricted');

-- ===== governed tags at catalog / schema / table level =====
ALTER CATALOG ai27_ucsync_testcatalog SET TAGS ('ai27_uc_classification'='INTERNAL');
ALTER SCHEMA ai27_ucsync_testcatalog.governed SET TAGS ('ai27_uc_classification'='CONFIDENTIAL');
ALTER TABLE ai27_ucsync_testcatalog.governed.abac_mask_tbl SET TAGS ('ai27_uc_classification'='RESTRICTED');

-- ===== ABAC policies: catalog (inherited), schema (EXCEPT), table scope =====
CREATE POLICY ai27_ucsync_mask_email ON CATALOG ai27_ucsync_testcatalog
  COLUMN MASK ai27_ucsync_testcatalog.functions.mask_email
  TO `account users`
  FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_pii','EMAIL') AS c ON COLUMN c;

CREATE POLICY ai27_ucsync_mask_phone ON SCHEMA ai27_ucsync_testcatalog.governed
  COLUMN MASK ai27_ucsync_testcatalog.functions.mask_phone
  TO `account users` EXCEPT `abhishek.iyer@databricks.com`
  FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_pii','PHONE') AS c ON COLUMN c;

CREATE POLICY ai27_ucsync_mask_acct ON TABLE ai27_ucsync_testcatalog.governed.abac_mask_tbl
  COLUMN MASK ai27_ucsync_testcatalog.functions.mask_account
  TO `account users`
  FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_pii','BANK_ACCOUNT') AS c ON COLUMN c;

CREATE POLICY ai27_ucsync_rowfilter_restricted ON CATALOG ai27_ucsync_testcatalog
  ROW FILTER ai27_ucsync_testcatalog.functions.region_filter
  TO `account users`
  FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_row_access','restricted') AS region USING COLUMNS (region);
