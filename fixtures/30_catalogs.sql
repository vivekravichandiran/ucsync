-- Catalogs + schemas for the 3 source fixtures.
-- Each catalog is MANAGED on its storage account's `data` container root; each
-- schema gets an explicit MANAGED LOCATION at <account-root>/<schema> so managed
-- data nests cleanly and external objects sit at flat <root>/<schema>/<object>.
-- {{GOV_ACCOUNT}} / {{FIN_ACCOUNT}} / {{SALES_ACCOUNT}} are substituted by recreate.py.

-- ===== ai27_uc_gov_src =====
CREATE CATALOG IF NOT EXISTS ai27_uc_gov_src
  MANAGED LOCATION 'abfss://data@{{GOV_ACCOUNT}}.dfs.core.windows.net/'
  COMMENT 'ai27_uc governance-migration source fixture catalog';
CREATE SCHEMA IF NOT EXISTS ai27_uc_gov_src.hr        MANAGED LOCATION 'abfss://data@{{GOV_ACCOUNT}}.dfs.core.windows.net/hr'        COMMENT 'HR fixture schema';
CREATE SCHEMA IF NOT EXISTS ai27_uc_gov_src.finance   MANAGED LOCATION 'abfss://data@{{GOV_ACCOUNT}}.dfs.core.windows.net/finance'   COMMENT 'Finance fixture schema';
CREATE SCHEMA IF NOT EXISTS ai27_uc_gov_src.sec       MANAGED LOCATION 'abfss://data@{{GOV_ACCOUNT}}.dfs.core.windows.net/sec'       COMMENT 'Security UDFs';
CREATE SCHEMA IF NOT EXISTS ai27_uc_gov_src.analytics MANAGED LOCATION 'abfss://data@{{GOV_ACCOUNT}}.dfs.core.windows.net/analytics' COMMENT 'Views';

-- ===== ai27_uc_finance =====
CREATE CATALOG IF NOT EXISTS ai27_uc_finance
  MANAGED LOCATION 'abfss://data@{{FIN_ACCOUNT}}.dfs.core.windows.net/'
  COMMENT 'ai27_uc finance domain (external-location backed)';
CREATE SCHEMA IF NOT EXISTS ai27_uc_finance.gl  MANAGED LOCATION 'abfss://data@{{FIN_ACCOUNT}}.dfs.core.windows.net/gl'  COMMENT 'General ledger';
CREATE SCHEMA IF NOT EXISTS ai27_uc_finance.ap  MANAGED LOCATION 'abfss://data@{{FIN_ACCOUNT}}.dfs.core.windows.net/ap'  COMMENT 'Accounts payable';
CREATE SCHEMA IF NOT EXISTS ai27_uc_finance.sec MANAGED LOCATION 'abfss://data@{{FIN_ACCOUNT}}.dfs.core.windows.net/sec' COMMENT 'UDFs';

-- ===== ai27_uc_sales =====
CREATE CATALOG IF NOT EXISTS ai27_uc_sales
  MANAGED LOCATION 'abfss://data@{{SALES_ACCOUNT}}.dfs.core.windows.net/'
  COMMENT 'ai27_uc sales domain (external-location backed)';
CREATE SCHEMA IF NOT EXISTS ai27_uc_sales.crm    MANAGED LOCATION 'abfss://data@{{SALES_ACCOUNT}}.dfs.core.windows.net/crm'    COMMENT 'CRM';
CREATE SCHEMA IF NOT EXISTS ai27_uc_sales.orders MANAGED LOCATION 'abfss://data@{{SALES_ACCOUNT}}.dfs.core.windows.net/orders' COMMENT 'Orders';
CREATE SCHEMA IF NOT EXISTS ai27_uc_sales.sec    MANAGED LOCATION 'abfss://data@{{SALES_ACCOUNT}}.dfs.core.windows.net/sec'    COMMENT 'UDFs';
