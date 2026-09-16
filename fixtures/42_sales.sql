-- ai27_uc_sales: lightest fixture. One classic mask, a partitioned table, a view,
-- and an EXTERNAL volume. {{SALES_ACCOUNT}} / {{EXPORT_SP}} substituted.

-- ---- function ----
CREATE OR REPLACE FUNCTION ai27_uc_sales.sec.mask_email(v STRING) RETURNS STRING COMMENT 'Mask email' RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE regexp_replace(v,'(^[^@]).*(@.*$)','$1***$2') END;

-- ---- tables ----
CREATE TABLE IF NOT EXISTS ai27_uc_sales.crm.customers (
  customer_id BIGINT COMMENT 'id', name STRING COMMENT 'name',
  email STRING COMMENT 'email (masked)', region STRING COMMENT 'region')
USING DELTA COMMENT 'Customers (managed, in schema EL)' TBLPROPERTIES ('ai27_uc.fixture'='true');
CREATE TABLE IF NOT EXISTS ai27_uc_sales.orders.orders (
  order_id BIGINT COMMENT 'id', customer_id BIGINT COMMENT 'fk',
  amount DECIMAL(12,2) COMMENT 'amount', status STRING COMMENT 'status')
USING DELTA PARTITIONED BY (status) COMMENT 'Orders (managed, partitioned)' TBLPROPERTIES ('ai27_uc.fixture'='true');

-- ---- view ----
CREATE OR REPLACE VIEW ai27_uc_sales.orders.order_summary COMMENT 'Orders by status' AS SELECT status, count(*) n, sum(amount) total FROM ai27_uc_sales.orders.orders GROUP BY status;

-- ---- external volume ----
CREATE EXTERNAL VOLUME IF NOT EXISTS ai27_uc_sales.orders.archive
  LOCATION 'abfss://data@{{SALES_ACCOUNT}}.dfs.core.windows.net/orders/archive'
  COMMENT 'External archive volume';

-- ---- classic mask + tags ----
ALTER TABLE ai27_uc_sales.crm.customers ALTER COLUMN email SET MASK ai27_uc_sales.sec.mask_email;
ALTER CATALOG ai27_uc_sales SET TAGS ('ai27_uc_classification'='INTERNAL');

-- ---- data ----
INSERT INTO ai27_uc_sales.crm.customers VALUES (1,'Acme Corp','ops@acme.com','US'),(2,'Globex','info@globex.com','EU'),(3,'Initech','hi@initech.com','US'),(4,'Umbrella','contact@umbrella.com','APAC');
INSERT INTO ai27_uc_sales.orders.orders VALUES (100,1,2500.00,'SHIPPED'),(101,2,999.99,'PENDING'),(102,1,4200.50,'SHIPPED'),(103,3,150.00,'CANCELLED');

-- ---- grants ----
GRANT USE CATALOG ON CATALOG ai27_uc_sales TO `account users`;
GRANT USE SCHEMA ON SCHEMA ai27_uc_sales.crm TO `account users`;
GRANT SELECT ON TABLE ai27_uc_sales.crm.customers TO `account users`;
GRANT SELECT ON TABLE ai27_uc_sales.orders.orders TO `account users`;
GRANT USE CATALOG, USE SCHEMA, SELECT ON CATALOG ai27_uc_sales TO `{{EXPORT_SP}}`;
