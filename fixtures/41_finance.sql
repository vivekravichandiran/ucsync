-- ai27_uc_finance: managed + EXTERNAL tables/volumes, one ABAC schema policy,
-- external-table governance (inline mask on invoices_ext.vendor; governed tag on
-- accounts_ext column + table). {{FIN_ACCOUNT}} / {{EXPORT_SP}} substituted.

-- ---- function ----
CREATE OR REPLACE FUNCTION ai27_uc_finance.sec.mask_account(v STRING) RETURNS STRING COMMENT 'Mask account' RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE concat('****', right(v,4)) END;

-- ---- managed tables ----
CREATE TABLE IF NOT EXISTS ai27_uc_finance.gl.accounts (
  account_id BIGINT COMMENT 'id', name STRING COMMENT 'name',
  account_number STRING COMMENT 'acct # (ABAC-masked)', balance DECIMAL(18,2) COMMENT 'balance')
USING DELTA COMMENT 'GL accounts (managed)' TBLPROPERTIES ('ai27_uc.fixture'='true');
CREATE TABLE IF NOT EXISTS ai27_uc_finance.ap.invoices (
  invoice_id BIGINT COMMENT 'id', vendor STRING COMMENT 'vendor',
  amount DECIMAL(12,2) COMMENT 'amount', due_date DATE COMMENT 'due')
USING DELTA COMMENT 'AP invoices (managed)' TBLPROPERTIES ('ai27_uc.fixture'='true');

-- ---- external tables (empty Delta at flat account paths) ----
CREATE TABLE IF NOT EXISTS ai27_uc_finance.gl.accounts_ext (
  gl_account STRING, period STRING, debit DECIMAL(18,2), credit DECIMAL(18,2))
USING DELTA COMMENT 'External GL accounts fixture'
LOCATION 'abfss://data@{{FIN_ACCOUNT}}.dfs.core.windows.net/gl/accounts_ext';
CREATE TABLE IF NOT EXISTS ai27_uc_finance.ap.invoices_ext (
  invoice_id BIGINT, vendor STRING, amount DECIMAL(18,2), status STRING)
USING DELTA COMMENT 'External invoices fixture (external-table migration test)'
LOCATION 'abfss://data@{{FIN_ACCOUNT}}.dfs.core.windows.net/ap/invoices_ext';

-- ---- volumes: managed + external ----
CREATE VOLUME IF NOT EXISTS ai27_uc_finance.gl.statements COMMENT 'Managed volume for statements';
CREATE EXTERNAL VOLUME IF NOT EXISTS ai27_uc_finance.gl.archive_ext
  LOCATION 'abfss://data@{{FIN_ACCOUNT}}.dfs.core.windows.net/gl/archive_ext'
  COMMENT 'External archive volume';

-- ---- external-table inline mask (must migrate before the table on target) ----
ALTER TABLE ai27_uc_finance.ap.invoices_ext ALTER COLUMN vendor SET MASK ai27_uc_finance.sec.mask_account;

-- ---- tags ----
ALTER CATALOG ai27_uc_finance SET TAGS ('ai27_uc_classification'='CONFIDENTIAL');
ALTER TABLE ai27_uc_finance.gl.accounts ALTER COLUMN account_number SET TAGS ('ai27_uc_pii'='BANK_ACCOUNT');
ALTER TABLE ai27_uc_finance.gl.accounts_ext ALTER COLUMN gl_account SET TAGS ('ai27_uc_pii'='BANK_ACCOUNT');
ALTER TABLE ai27_uc_finance.gl.accounts_ext SET TAGS ('ai27_uc_pii'='BANK_ACCOUNT');

-- ---- ABAC policy ----
CREATE POLICY ai27_uc_fin_mask_acct ON SCHEMA ai27_uc_finance.gl COMMENT 'Mask account numbers' COLUMN MASK ai27_uc_finance.sec.mask_account TO `account users` FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_pii','BANK_ACCOUNT') AS c ON COLUMN c;

-- ---- data ----
INSERT INTO ai27_uc_finance.gl.accounts VALUES (1,'Operating','1234567890',150000.00),(2,'Payroll','9876543210',80000.00),(3,'Reserve','5555444433',500000.00);
INSERT INTO ai27_uc_finance.ap.invoices VALUES (900,'CloudCo',12000.00,DATE'2026-09-15'),(901,'OfficeMart',450.75,DATE'2026-09-01'),(902,'LegalLLP',8800.00,DATE'2026-10-01');
INSERT INTO ai27_uc_finance.gl.accounts_ext VALUES ('1234567890','2026-08',5000.00,0.00),('9876543210','2026-08',0.00,2500.00);
INSERT INTO ai27_uc_finance.ap.invoices_ext VALUES (900,'CloudCo',12000.00,'OPEN'),(901,'OfficeMart',450.75,'PAID');

-- ---- grants ----
GRANT USE CATALOG ON CATALOG ai27_uc_finance TO `account users`;
GRANT USE SCHEMA ON SCHEMA ai27_uc_finance.gl TO `account users`;
GRANT SELECT ON TABLE ai27_uc_finance.gl.accounts TO `account users`;
GRANT SELECT ON TABLE ai27_uc_finance.ap.invoices TO `account users`;
GRANT READ VOLUME ON VOLUME ai27_uc_finance.gl.statements TO `account users`;
GRANT USE CATALOG, USE SCHEMA, SELECT ON CATALOG ai27_uc_finance TO `{{EXPORT_SP}}`;
