-- ai27_uc_gov_src: the richest governance-coverage fixture.
-- Functions -> tables -> classic masks/row filters -> views -> volume -> tags ->
-- ABAC policies -> data -> grants. Governance mirrors the live capture
-- (fixtures/capture/): 6 masking/filter UDFs, classic + ABAC masks, 2 classic
-- row filters, tag-driven ABAC, governed column tags. {{EXPORT_SP}} substituted.

-- ---- functions (sec) ----
CREATE OR REPLACE FUNCTION ai27_uc_gov_src.sec.mask_ssn(v STRING) RETURNS STRING COMMENT 'Mask SSN' RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE 'XXX-XX-' || right(v,4) END;
CREATE OR REPLACE FUNCTION ai27_uc_gov_src.sec.mask_email(v STRING) RETURNS STRING COMMENT 'Mask email' RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE regexp_replace(v,'(^[^@]).*(@.*$)','$1***$2') END;
CREATE OR REPLACE FUNCTION ai27_uc_gov_src.sec.mask_account(v STRING) RETURNS STRING COMMENT 'Mask account' RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE concat('****', right(v,4)) END;
CREATE OR REPLACE FUNCTION ai27_uc_gov_src.sec.mask_phone(v STRING) RETURNS STRING COMMENT 'Mask phone' RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE concat('***-***-', right(v,4)) END;
CREATE OR REPLACE FUNCTION ai27_uc_gov_src.sec.dept_filter(dept STRING) RETURNS BOOLEAN COMMENT 'Row filter by dept' RETURN is_account_group_member('admins') OR dept = 'PUBLIC';
CREATE OR REPLACE FUNCTION ai27_uc_gov_src.sec.region_filter(region STRING) RETURNS BOOLEAN COMMENT 'Row filter by region' RETURN is_account_group_member('admins') OR region = 'US';

-- ---- tables ----
CREATE TABLE IF NOT EXISTS ai27_uc_gov_src.hr.employees (
  id BIGINT GENERATED ALWAYS AS IDENTITY COMMENT 'Surrogate identity key',
  emp_name STRING NOT NULL COMMENT 'Employee full name',
  ssn STRING COMMENT 'Social security number (classic-masked)',
  email STRING COMMENT 'Work email',
  salary DECIMAL(12,2) COMMENT 'Annual salary',
  hire_date DATE COMMENT 'Hire date',
  hire_year INT GENERATED ALWAYS AS (year(hire_date)) COMMENT 'Derived hire year',
  dept STRING COMMENT 'Department (partition + row-filtered)',
  phone STRING,
  CONSTRAINT pk_employees PRIMARY KEY (id))
USING DELTA PARTITIONED BY (dept)
COMMENT 'Employee master fixture (identity, generated col, PK, partition, user props)'
TBLPROPERTIES ('ai27_uc.fixture'='true','ai27_uc.domain'='hr');

CREATE TABLE IF NOT EXISTS ai27_uc_gov_src.hr.employees_secure (
  id BIGINT COMMENT 'Id',
  emp_name STRING COMMENT 'Name',
  email STRING COMMENT 'Email (ABAC-masked)',
  region STRING COMMENT 'Region (ABAC row-filtered)',
  phone STRING)
USING DELTA CLUSTER BY (region)
COMMENT 'Secure employees fixture (ABAC mask + ABAC row filter with EXCEPT)'
TBLPROPERTIES ('ai27_uc.fixture'='true');

CREATE TABLE IF NOT EXISTS ai27_uc_gov_src.finance.accounts (
  account_id BIGINT COMMENT 'Account id',
  customer_name STRING COMMENT 'Customer name',
  account_number STRING COMMENT 'Bank account number (ABAC-masked via governed tag)',
  balance DECIMAL(18,2) COMMENT 'Balance',
  routing_number STRING,
  contact_phone STRING)
USING DELTA COMMENT 'Finance accounts fixture (ABAC column mask target)'
TBLPROPERTIES ('ai27_uc.fixture'='true','ai27_uc.domain'='finance');

CREATE TABLE IF NOT EXISTS ai27_uc_gov_src.finance.ledger (
  entry_id BIGINT NOT NULL COMMENT 'Entry id',
  amount DECIMAL(18,2) COMMENT 'Amount',
  entry_type STRING COMMENT 'Type',
  counterparty_account STRING,
  settlement_account STRING,
  CONSTRAINT pk_ledger PRIMARY KEY (entry_id))
USING DELTA COMMENT 'Ledger fixture (PK + CHECK)'
TBLPROPERTIES ('ai27_uc.fixture'='true');
ALTER TABLE ai27_uc_gov_src.finance.ledger ADD CONSTRAINT chk_amount CHECK (amount >= 0);

-- ---- classic masks + row filters ----
ALTER TABLE ai27_uc_gov_src.hr.employees ALTER COLUMN ssn SET MASK ai27_uc_gov_src.sec.mask_ssn;
ALTER TABLE ai27_uc_gov_src.hr.employees ALTER COLUMN email SET MASK ai27_uc_gov_src.sec.mask_email;
ALTER TABLE ai27_uc_gov_src.hr.employees ALTER COLUMN phone SET MASK ai27_uc_gov_src.sec.mask_phone;
ALTER TABLE ai27_uc_gov_src.hr.employees SET ROW FILTER ai27_uc_gov_src.sec.dept_filter ON (dept);
ALTER TABLE ai27_uc_gov_src.hr.employees_secure ALTER COLUMN email SET MASK ai27_uc_gov_src.sec.mask_email;
ALTER TABLE ai27_uc_gov_src.hr.employees_secure ALTER COLUMN phone SET MASK ai27_uc_gov_src.sec.mask_phone;
ALTER TABLE ai27_uc_gov_src.hr.employees_secure SET ROW FILTER ai27_uc_gov_src.sec.region_filter ON (region);
ALTER TABLE ai27_uc_gov_src.finance.accounts ALTER COLUMN account_number SET MASK ai27_uc_gov_src.sec.mask_account;
ALTER TABLE ai27_uc_gov_src.finance.accounts ALTER COLUMN routing_number SET MASK ai27_uc_gov_src.sec.mask_account;
ALTER TABLE ai27_uc_gov_src.finance.accounts ALTER COLUMN contact_phone SET MASK ai27_uc_gov_src.sec.mask_phone;

-- ---- views (analytics) ----
CREATE OR REPLACE VIEW ai27_uc_gov_src.analytics.emp_summary COMMENT 'Standard view over employees' AS SELECT id, emp_name, dept FROM ai27_uc_gov_src.hr.employees;
CREATE OR REPLACE VIEW ai27_uc_gov_src.analytics.emp_dynamic COMMENT 'Identity-aware dynamic view' AS SELECT id, emp_name, dept, current_user() AS viewer FROM ai27_uc_gov_src.hr.employees;
CREATE OR REPLACE VIEW ai27_uc_gov_src.analytics.emp_fn_masked AS SELECT id, ai27_uc_gov_src.sec.mask_email(emp_name) AS emp_name_masked, dept FROM ai27_uc_gov_src.hr.employees;
CREATE OR REPLACE VIEW ai27_uc_gov_src.analytics.emp_secure_dynamic AS SELECT id, CASE WHEN is_account_group_member('hr_admins') THEN emp_name ELSE '***' END AS emp_name, dept, current_user() AS accessed_by FROM ai27_uc_gov_src.hr.employees WHERE is_account_group_member('hr_admins') OR dept IN ('ENG','FIN');

-- ---- managed volume ----
CREATE VOLUME IF NOT EXISTS ai27_uc_gov_src.hr.emp_files COMMENT 'Managed volume fixture';

-- ---- object + column tags ----
ALTER CATALOG ai27_uc_gov_src SET TAGS ('ai27_uc_classification' = 'INTERNAL');
ALTER SCHEMA ai27_uc_gov_src.finance SET TAGS ('ai27_uc_classification' = 'CONFIDENTIAL');
ALTER TABLE ai27_uc_gov_src.hr.employees ALTER COLUMN phone SET TAGS ('ai27_uc_pii'='PHONE');
ALTER TABLE ai27_uc_gov_src.hr.employees_secure ALTER COLUMN email SET TAGS ('ai27_uc_pii'='EMAIL');
ALTER TABLE ai27_uc_gov_src.hr.employees_secure ALTER COLUMN phone SET TAGS ('ai27_uc_pii'='PHONE');
ALTER TABLE ai27_uc_gov_src.hr.employees_secure ALTER COLUMN region SET TAGS ('ai27_uc_row_access'='restricted');
ALTER TABLE ai27_uc_gov_src.finance.accounts ALTER COLUMN account_number SET TAGS ('ai27_uc_pii'='BANK_ACCOUNT');
ALTER TABLE ai27_uc_gov_src.finance.accounts ALTER COLUMN routing_number SET TAGS ('ai27_uc_pii'='BANK_ACCOUNT');
ALTER TABLE ai27_uc_gov_src.finance.accounts ALTER COLUMN contact_phone SET TAGS ('ai27_uc_pii'='PHONE');
ALTER TABLE ai27_uc_gov_src.finance.ledger ALTER COLUMN counterparty_account SET TAGS ('ai27_uc_pii'='BANK_ACCOUNT');
ALTER TABLE ai27_uc_gov_src.finance.ledger ALTER COLUMN settlement_account SET TAGS ('ai27_uc_pii'='BANK_ACCOUNT');

-- ---- ABAC policies (tag-driven) ----
CREATE POLICY ai27_uc_mask_phone_cat ON CATALOG ai27_uc_gov_src COMMENT 'Mask phone numbers catalog-wide via governed tag' COLUMN MASK ai27_uc_gov_src.sec.mask_phone TO `account users` FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_pii','PHONE') AS c ON COLUMN c;
CREATE POLICY ai27_uc_mask_bank ON SCHEMA ai27_uc_gov_src.finance COMMENT 'Mask bank accounts via governed tag' COLUMN MASK ai27_uc_gov_src.sec.mask_account TO `account users` FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_pii','BANK_ACCOUNT') AS c ON COLUMN c;
CREATE POLICY ai27_uc_mask_email ON TABLE ai27_uc_gov_src.hr.employees_secure COMMENT 'Mask email except exempt principal' COLUMN MASK ai27_uc_gov_src.sec.mask_email TO `account users` EXCEPT `abhishek.iyer@databricks.com` FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_pii','EMAIL') AS c ON COLUMN c;
CREATE POLICY ai27_uc_rowfilter_region ON TABLE ai27_uc_gov_src.hr.employees_secure COMMENT 'Row filter by region governed tag' ROW FILTER ai27_uc_gov_src.sec.region_filter TO `account users` EXCEPT `abhishek.iyer@databricks.com` FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_row_access','restricted') AS region USING COLUMNS (region);

-- ---- data ----
INSERT INTO ai27_uc_gov_src.hr.employees (emp_name, ssn, email, salary, hire_date, dept, phone) VALUES
 ('Alice Chen','111-22-3333','alice@corp.com',145000.00,DATE'2021-03-01','ENGINEERING','415-555-0101'),
 ('Bob Diaz','222-33-4444','bob@corp.com',98000.00,DATE'2022-07-15','SALES','415-555-0102'),
 ('Carol Kim','333-44-5555','carol@corp.com',120000.00,DATE'2020-01-10','PUBLIC','415-555-0103'),
 ('Dan Ortiz','444-55-6666','dan@corp.com',87000.00,DATE'2023-09-05','SALES','415-555-0104');
INSERT INTO ai27_uc_gov_src.hr.employees_secure VALUES
 (1,'Alice Chen','alice@corp.com','US','415-555-0101'),
 (2,'Bob Diaz','bob@corp.com','EU','415-555-0102'),
 (3,'Carol Kim','carol@corp.com','US','415-555-0103');
INSERT INTO ai27_uc_gov_src.finance.accounts VALUES
 (1,'Acme Corp','1234567890',250000.00,'021000021','415-555-0201'),
 (2,'Globex','9876543210',88000.00,'011401533','415-555-0202'),
 (3,'Initech','5555444433',12500.00,'121000248','415-555-0203');
INSERT INTO ai27_uc_gov_src.finance.ledger VALUES
 (1,1000.00,'CREDIT','1234567890','9876543210'),
 (2,250.50,'DEBIT','5555444433','1234567890'),
 (3,9999.99,'CREDIT','9876543210','5555444433');

-- ---- grants (6-way variety + export SP so SHOW CREATE can read) ----
GRANT USE CATALOG ON CATALOG ai27_uc_gov_src TO `account users`;
GRANT USE SCHEMA ON SCHEMA ai27_uc_gov_src.hr TO `account users`;
GRANT USE SCHEMA ON SCHEMA ai27_uc_gov_src.finance TO `account users`;
GRANT SELECT ON TABLE ai27_uc_gov_src.hr.employees TO `account users`;
GRANT SELECT, MODIFY ON TABLE ai27_uc_gov_src.finance.accounts TO `abhishek.iyer@databricks.com`;
GRANT SELECT ON TABLE ai27_uc_gov_src.hr.employees_secure TO `account users`;
GRANT SELECT ON VIEW ai27_uc_gov_src.analytics.emp_summary TO `account users`;
GRANT EXECUTE ON FUNCTION ai27_uc_gov_src.sec.mask_ssn TO `account users`;
GRANT READ VOLUME ON VOLUME ai27_uc_gov_src.hr.emp_files TO `account users`;
GRANT USE CATALOG, USE SCHEMA, SELECT ON CATALOG ai27_uc_gov_src TO `{{EXPORT_SP}}`;
