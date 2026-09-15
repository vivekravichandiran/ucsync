-- ai27_ucsync_testcatalog.external_store — external tables (Delta + Parquet) + managed
-- and external volumes. Files are seeded by 63_testcat_files.sh (nested subdirs).
-- External objects sit at <account-root>/external_store/<object> (covered by the root EL).
-- {{TESTCAT_SRC_ACCOUNT}} substituted by recreate.py.

-- ===== external Delta table =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.external_store.sales_ext
  USING DELTA
  LOCATION 'abfss://data@{{TESTCAT_SRC_ACCOUNT}}.dfs.core.windows.net/external_store/sales_ext'
  COMMENT 'External Delta table'
  AS SELECT * FROM VALUES
    (1,'US',250.00), (2,'EU',410.50), (3,'US',99.99) AS t(order_id, region, amount);

-- ===== external Parquet (non-Delta) table =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.external_store.parquet_ext
  USING PARQUET
  LOCATION 'abfss://data@{{TESTCAT_SRC_ACCOUNT}}.dfs.core.windows.net/external_store/parquet_ext'
  COMMENT 'External Parquet (non-Delta) table'
  AS SELECT * FROM VALUES
    (1,'2026-01',1000), (2,'2026-02',2000) AS t(id, period, value);

-- ===== external Delta table with inline mask (function-before-table on import) =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.external_store.invoices_ext (
  invoice_id INT, vendor STRING COMMENT 'masked by mask_account', amount DECIMAL(12,2)
) USING DELTA
  LOCATION 'abfss://data@{{TESTCAT_SRC_ACCOUNT}}.dfs.core.windows.net/external_store/invoices_ext'
  COMMENT 'External Delta table with inline column mask';
INSERT INTO ai27_ucsync_testcatalog.external_store.invoices_ext VALUES
  (1,'CloudCo-889900', 5400.00), (2,'OfficeMart-112233', 890.50);
ALTER TABLE ai27_ucsync_testcatalog.external_store.invoices_ext
  ALTER COLUMN vendor SET MASK ai27_ucsync_testcatalog.functions.mask_account;

-- ===== managed volume (+ volume-level governed tag) =====
CREATE VOLUME IF NOT EXISTS ai27_ucsync_testcatalog.external_store.docs_managed
  COMMENT 'Managed volume with nested files';
ALTER VOLUME ai27_ucsync_testcatalog.external_store.docs_managed
  SET TAGS ('ai27_uc_classification'='INTERNAL');

-- ===== external volume =====
CREATE EXTERNAL VOLUME IF NOT EXISTS ai27_ucsync_testcatalog.external_store.archive_ext
  LOCATION 'abfss://data@{{TESTCAT_SRC_ACCOUNT}}.dfs.core.windows.net/external_store/archive_ext'
  COMMENT 'External volume with nested files';
