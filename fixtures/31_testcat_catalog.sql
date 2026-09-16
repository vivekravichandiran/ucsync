-- ai27_ucsync_testcatalog — SOURCE catalog + 6 schemas (source_ws).
-- Managed on the dedicated ADLS account `data` container root; each schema gets an
-- explicit MANAGED LOCATION at <root>/<schema> so managed data nests and external
-- objects sit at flat <root>/<schema>/<object>.
-- {{TESTCAT_SRC_ACCOUNT}} is substituted by recreate.py from config.env.

CREATE CATALOG IF NOT EXISTS ai27_ucsync_testcatalog
  MANAGED LOCATION 'abfss://data@{{TESTCAT_SRC_ACCOUNT}}.dfs.core.windows.net/'
  COMMENT 'ai27_ucsync single comprehensive UC-migration test catalog (every object + ACL combination)';

CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.functions      MANAGED LOCATION 'abfss://data@{{TESTCAT_SRC_ACCOUNT}}.dfs.core.windows.net/functions'      COMMENT 'Mask/filter UDFs + Python UDF + TVF (created first so masks/policies resolve)';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.core_tables    MANAGED LOCATION 'abfss://data@{{TESTCAT_SRC_ACCOUNT}}.dfs.core.windows.net/core_tables'    COMMENT 'Table zoo (all shape features) + views — readable by the USE-CATALOG user';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.governed       MANAGED LOCATION 'abfss://data@{{TESTCAT_SRC_ACCOUNT}}.dfs.core.windows.net/governed'       COMMENT 'Classic masks/row filters + ABAC (catalog-inherited) mask & filter tables';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.external_store MANAGED LOCATION 'abfss://data@{{TESTCAT_SRC_ACCOUNT}}.dfs.core.windows.net/external_store' COMMENT 'External tables (Delta + Parquet) + managed & external volumes with nested files';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.advanced       MANAGED LOCATION 'abfss://data@{{TESTCAT_SRC_ACCOUNT}}.dfs.core.windows.net/advanced'       COMMENT 'Report-only zoo: MV, streaming + SDP event log, monitor, ML model, VS index, Lakebase synced, metric view';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.restricted     MANAGED LOCATION 'abfss://data@{{TESTCAT_SRC_ACCOUNT}}.dfs.core.windows.net/restricted'     COMMENT 'Sensitive tables — the no-access schema for the USE-CATALOG user';
