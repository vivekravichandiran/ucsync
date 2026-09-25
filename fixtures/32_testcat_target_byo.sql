-- ai27_ucsync_testcatalog — TARGET BYO shell (target_ws).
-- BYO mode: the migration replays CONTENTS but not the securable shells, so the target
-- catalog + schemas + storage credential + external location must pre-exist. This file
-- creates the empty catalog + 6 schemas (same names as source; cross-metastore, no
-- collision). Storage credential/EL come from 21_uc_storage_testcat.sh (target).
-- Runs on TGT_PROFILE / TGT_WAREHOUSE. {{TESTCAT_TGT_ACCOUNT}} substituted by recreate.py.

CREATE CATALOG IF NOT EXISTS ai27_ucsync_testcatalog
  MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/'
  COMMENT 'ai27_ucsync test catalog — TARGET BYO shell (contents filled by the migration)';

CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.functions      MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/functions'      COMMENT 'BYO shell';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.core_tables    MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/core_tables'    COMMENT 'BYO shell';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.governed       MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/governed'       COMMENT 'BYO shell';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.external_store MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/external_store' COMMENT 'BYO shell';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.advanced       MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/advanced'       COMMENT 'BYO shell';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.restricted     MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/restricted'     COMMENT 'BYO shell';
-- Parallelism stress bed (backlog item 3): 3 BYO schemas for the 120-table load.
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a     MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/parallel_a'     COMMENT 'BYO shell (parallelism stress)';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b     MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/parallel_b'     COMMENT 'BYO shell (parallelism stress)';
CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c     MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/parallel_c'     COMMENT 'BYO shell (parallelism stress)';
