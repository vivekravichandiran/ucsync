-- ai27_ucsync_testcatalog.core_tables — full table-shape fidelity + views.
-- Also seeds the restricted schema (no-access tables). Masks/tags on the dedicated
-- governed tables live in 62; here we mask employees.email so a view-over-masked exists.

-- ===== departments (PK, referenced by employees FK) =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.departments (
  dept_id INT NOT NULL COMMENT 'department id',
  dept_name STRING COMMENT 'department name',
  CONSTRAINT dept_pk PRIMARY KEY(dept_id)
) USING DELTA COMMENT 'Departments (PK target of employees FK)'
TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.departments VALUES
  (10,'ENGINEERING'), (20,'SALES'), (30,'PUBLIC'), (40,'FINANCE');

-- ===== employees (IDENTITY + GENERATED + PARTITIONED + PK + FK + masked email) =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.employees (
  id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL COMMENT 'surrogate identity key',
  emp_name STRING COMMENT 'full name',
  ssn STRING COMMENT 'social security number',
  email STRING COMMENT 'work email (classic-masked)',
  salary DECIMAL(12,2) COMMENT 'annual salary',
  hire_date DATE COMMENT 'hire date',
  hire_year INT GENERATED ALWAYS AS (year(hire_date)) COMMENT 'derived hire year',
  dept_id INT COMMENT 'FK -> departments',
  phone STRING COMMENT 'contact phone',
  dept STRING COMMENT 'partition column',
  CONSTRAINT emp_pk PRIMARY KEY(id),
  CONSTRAINT emp_dept_fk FOREIGN KEY(dept_id)
    REFERENCES ai27_ucsync_testcatalog.core_tables.departments(dept_id)
) USING DELTA
PARTITIONED BY (dept)
COMMENT 'Employees: identity, generated col, partitioning, PK+FK, column comments'
TBLPROPERTIES ('ai27_uc.fixture'='true');

INSERT INTO ai27_ucsync_testcatalog.core_tables.employees
  (emp_name, ssn, email, salary, hire_date, dept_id, phone, dept) VALUES
  ('Alice Chen','123-45-6789','alice@corp.com', 185000.00, DATE'2021-03-01', 10, '415-555-1001','ENGINEERING'),
  ('Bob Diaz','222-33-4444','bob@corp.com',     120000.00, DATE'2022-07-15', 20, '415-555-1002','SALES'),
  ('Carol Kim','333-22-1111','carol@corp.com',   99000.00, DATE'2020-01-20', 30, '415-555-1003','PUBLIC'),
  ('Dan Ortiz','444-55-6666','dan@corp.com',    140000.00, DATE'2023-09-05', 40, '415-555-1004','FINANCE');

ALTER TABLE ai27_ucsync_testcatalog.core_tables.employees
  ALTER COLUMN email SET MASK ai27_ucsync_testcatalog.functions.mask_email;

-- ===== orders_clustered (liquid clustering + deletion vectors + row tracking) =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.orders_clustered (
  order_id BIGINT COMMENT 'order id',
  region STRING COMMENT 'region',
  amount DECIMAL(12,2) COMMENT 'order amount',
  order_ts TIMESTAMP COMMENT 'order timestamp'
) USING DELTA
CLUSTER BY (region)
COMMENT 'Liquid-clustered table with deletion vectors + row tracking'
TBLPROPERTIES (
  'delta.enableDeletionVectors'='true',
  'delta.enableRowTracking'='true',
  'ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.orders_clustered VALUES
  (1,'US', 250.00, TIMESTAMP'2026-01-02 10:00:00'),
  (2,'EU', 410.50, TIMESTAMP'2026-01-03 11:30:00'),
  (3,'US', 99.99,  TIMESTAMP'2026-01-04 09:15:00');

-- ===== constraints_tbl (NOT NULL + DEFAULT + CHECK) =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.constraints_tbl (
  id INT NOT NULL COMMENT 'not-null id',
  qty INT DEFAULT 0 COMMENT 'defaulted quantity',
  status STRING DEFAULT 'NEW' COMMENT 'defaulted status',
  note STRING COMMENT 'free text'
) USING DELTA
COMMENT 'Constraint coverage: NOT NULL, DEFAULT, CHECK'
TBLPROPERTIES ('delta.feature.allowColumnDefaults'='supported', 'ai27_uc.fixture'='true');
ALTER TABLE ai27_ucsync_testcatalog.core_tables.constraints_tbl
  ADD CONSTRAINT chk_qty CHECK (qty >= 0);
INSERT INTO ai27_ucsync_testcatalog.core_tables.constraints_tbl (id, note) VALUES (1, 'uses defaults');
INSERT INTO ai27_ucsync_testcatalog.core_tables.constraints_tbl (id, qty, status, note) VALUES (2, 5, 'OPEN', 'explicit');

-- ===== types_tbl (STRUCT/ARRAY/MAP/DECIMAL/TIMESTAMP/BINARY/VARIANT) =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.types_tbl (
  id INT,
  s STRUCT<a:INT, b:STRING>,
  arr ARRAY<INT>,
  m MAP<STRING,INT>,
  dec DECIMAL(18,4),
  ts TIMESTAMP,
  bin BINARY,
  v VARIANT
) USING DELTA COMMENT 'All-types fixture incl VARIANT'
TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.core_tables.types_tbl
  SELECT 1, named_struct('a',1,'b','x'), array(1,2,3), map('k',1),
         CAST(12.3456 AS DECIMAL(18,4)), current_timestamp(),
         CAST('abc' AS BINARY), parse_json('{"k":1,"nested":[1,2]}');

-- ===== views =====
CREATE OR REPLACE VIEW ai27_ucsync_testcatalog.core_tables.emp_summary
  COMMENT 'Standard view' AS
  SELECT id, emp_name, dept FROM ai27_ucsync_testcatalog.core_tables.employees;

CREATE OR REPLACE VIEW ai27_ucsync_testcatalog.core_tables.emp_dynamic
  COMMENT 'Dynamic view (current_user + group membership)' AS
  SELECT id, emp_name, dept,
         current_user() AS viewer,
         is_account_group_member('admins') AS is_admin
  FROM ai27_ucsync_testcatalog.core_tables.employees;

CREATE OR REPLACE VIEW ai27_ucsync_testcatalog.core_tables.emp_over_masked
  COMMENT 'View over a masked table (email is classic-masked)' AS
  SELECT id, emp_name, email FROM ai27_ucsync_testcatalog.core_tables.employees;

-- ===== restricted schema (no-access tables; ACLs applied in 65) =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.restricted.secret_hr (
  emp_id INT, comp DECIMAL(12,2), ssn STRING
) USING DELTA COMMENT 'Sensitive HR — restricted schema'
TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.restricted.secret_hr VALUES (1, 185000.00, '123-45-6789');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.restricted.secret_fin (
  acct STRING, balance DECIMAL(14,2)
) USING DELTA COMMENT 'Sensitive finance — restricted schema'
TBLPROPERTIES ('ai27_uc.fixture'='true');
INSERT INTO ai27_ucsync_testcatalog.restricted.secret_fin VALUES ('ACME-001', 9250000.00);
