-- ai27_ucsync_testcatalog — TARGET BYO shell (target_ws).
-- BYO mode: the migration replays CONTENTS but not the securable shells, so the target
-- catalog + schemas + storage credential + external location must pre-exist. This file
-- creates the empty target catalog + every schema the source bed has. The target catalog
-- name is {{TESTCAT_TGT_CATALOG}} (may differ from the source — the run then passes
-- catalog_mapping_json {"ai27_ucsync_testcatalog": "{{TESTCAT_TGT_CATALOG}}"}).
-- Storage credential/EL come from 21_uc_storage_testcat.sh (target). Also creates the
-- ops catalog the utility writes audit/state/run bundles to.
-- Runs on TGT_PROFILE / TGT_WAREHOUSE. {{...}} substituted by recreate.py.

CREATE CATALOG IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}
  MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}'
  COMMENT 'ai27_ucsync test catalog — TARGET BYO shell (contents filled by the migration)';

CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.functions      MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/functions'      COMMENT 'BYO shell';
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.core_tables    MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/core_tables'    COMMENT 'BYO shell';
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.governed       MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/governed'       COMMENT 'BYO shell';
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.external_store MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/external_store' COMMENT 'BYO shell';
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.advanced       MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/advanced'       COMMENT 'BYO shell';
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.restricted     MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/restricted'     COMMENT 'BYO shell';
-- Parallelism stress bed (backlog item 3): 3 BYO schemas for the 120-table load.
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.parallel_a     MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/parallel_a'     COMMENT 'BYO shell (parallelism stress)';
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.parallel_b     MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/parallel_b'     COMMENT 'BYO shell (parallelism stress)';
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.parallel_c     MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/parallel_c'     COMMENT 'BYO shell (parallelism stress)';
-- v1.2.0 / v1.2.1 bug-fix beds (tc_bugfix 70, tc_abac_proc 71).
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.gold           MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/gold'           COMMENT 'BYO shell (bug-fix bed)';
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.silver         MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/silver'         COMMENT 'BYO shell (bug-fix bed)';
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.bronze         MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/bronze'         COMMENT 'BYO shell (bug-fix bed)';
CREATE SCHEMA IF NOT EXISTS {{TESTCAT_TGT_CATALOG}}.stage_         MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{TESTCAT_TGT_CATALOG}}/stage_'         COMMENT 'BYO shell (bug-fix bed)';

-- Ops catalog (audit / state / volume_files tables + the runs volume for bundles/reports).
CREATE CATALOG IF NOT EXISTS {{OPS_CATALOG}}
  MANAGED LOCATION 'abfss://data@{{TESTCAT_TGT_ACCOUNT}}.dfs.core.windows.net/{{OPS_CATALOG}}'
  COMMENT 'ai27_ucsync ops catalog (target): audit/state tables + run bundles';
CREATE SCHEMA IF NOT EXISTS {{OPS_CATALOG}}.ops COMMENT 'uc_sync_audit / uc_sync_state / uc_sync_volume_files';
CREATE VOLUME IF NOT EXISTS {{OPS_CATALOG}}.ops.runs COMMENT 'run_<id>/ bundles, migrated packages, reports, config files';
