#!/usr/bin/env python3
"""v1.2.0 bug-fix fixtures for ai27_ucsync_testcatalog (SOURCE workspace).

Three import bugs, each covered by tables AND views. The customer-shaped DDL is
kept as close to the real SHOW CREATE output as possible; identifiers derived from
customer names are replaced, and their catalog is swapped for ours.

  Bug 1 - COMMENT with an escaped apostrophe AND a ';' split the CREATE in half
          (package_import._split_statements). PARSE_SYNTAX_ERROR ... near '''.
          Tables: the 4 real tables (column comments / table comment wrapped in \'..\').
          Views:  gold.bug1_cell_flag_view (column comment), gold.bug1_site_actions_view
                  (view comment wrapped in \'..\').
  Bug 2 - TBLPROPERTIES stripping ran to the LAST ')' in the DDL
          (rewrite.strip_reserved_table_properties). A commented VIEW lost its query.
          Views:  the 2 real views (TBLPROPERTIES ('comment'=..) before WITH SCHEMA ... AS).
          Tables: gold.bug2_props_escaped_quote (property value with \' and parens),
                  gold.bug2_comment_mentions_tblproperties ('TBLPROPERTIES (' in a comment).
          Plus ~14 plain dependency tables the 2 real views read (gold/silver/bronze).
  Bug 3 - names containing '__' (or a part ending in '_') were decoded from the bundle
          file name into a 4/5-part name. REQUIRES_SINGLE_PART_NAMESPACE.
          Tables: gold.sap_pcr__mrss_d_bas_asg, gold.sap_pcr___mrss__d_chg_hism, stage_.orders
          Views:  one view over each.  All carry grants + tags (the fix covers those passes).

Statements are sent whole (no ';' splitting) because recreate.py's splitter has the
same escaped-quote weakness. After building, SHOW CREATE is run on every bug object to
confirm the source really emits the pattern. Idempotent (IF NOT EXISTS / OR REPLACE).
Run: python3 fixtures/recreate.py tc_bugfix
"""
import json, os, subprocess, sys, time

P = os.environ.get("SRC_PROFILE", "source_ws")
WH = os.environ.get("SRC_WAREHOUSE", "")
CAT = os.environ.get("TESTCAT_CATALOG", "ai27_ucsync_testcatalog")
ACCOUNT = os.environ.get("TESTCAT_SRC_ACCOUNT", "ai27ucsynctestsrc")
READER = os.environ.get("TC_USER_READ", "sanket.kelkar@databricks.com")
GROUP = os.environ.get("TC_GROUP_READER", "wsmig_acc_group")
ROOT = f"abfss://data@{ACCOUNT}.dfs.core.windows.net"


def sql(stmt):
    """Run one whole statement; return (state, error, rows)."""
    body = {"warehouse_id": WH, "statement": stmt, "wait_timeout": "50s"}
    p = subprocess.run(["databricks", "api", "post", "/api/2.0/sql/statements",
                        "-p", P, "--json", json.dumps(body)], capture_output=True, text=True)
    if p.returncode != 0:
        return "CLI_ERROR", p.stderr[:300], []
    out = json.loads(p.stdout)
    sid = out.get("statement_id")
    while out.get("status", {}).get("state") in ("PENDING", "RUNNING") and sid:
        time.sleep(2)
        g = subprocess.run(["databricks", "api", "get", f"/api/2.0/sql/statements/{sid}",
                            "-p", P], capture_output=True, text=True)
        out = json.loads(g.stdout)
    st = out.get("status", {})
    rows = (out.get("result") or {}).get("data_array") or []
    return st.get("state"), st.get("error", {}).get("message", ""), rows


SCHEMAS = [
    ("gold", "Real customer-shaped tables/views for bugs 1-3 + view dependencies"),
    ("silver", "Dependency tables read by the bug-2 views"),
    ("bronze", "Dependency tables read by the bug-2 views"),
    ("stage_", "Bug 3: a schema name ending in an underscore (encodes next to a dot as three underscores)"),
]

# Plain dependency tables the two real bug-2 views read (columns inferred from the queries).
STUBS = {
    "gold.duc_vw_rw_unitmaterials": "SolutionID INT, Period STRING, PlanMonth STRING, Activity DOUBLE, Revision STRING, PlanType STRING, UpdatedOn TIMESTAMP, StreamTag STRING, FeedFlag INT, IsActive INT, NodeTag STRING",
    "gold.duc_vw_rw_unitcapacities": "SolutionID INT, Period STRING, PlanMonth STRING, Activity DOUBLE, Revision STRING, PlanType STRING, UpdatedOn TIMESTAMP, CapacityTag STRING, FeedFlag INT, IsActive INT, NodeTag STRING",
    "gold.duc_vw_rw_purchases": "SolutionID INT, Period STRING, PeriodTag INT, PlanMonth STRING, PeriodLength DOUBLE, Revision STRING, PlanType STRING, UpdatedOn TIMESTAMP, StreamTag STRING, StreamDescription STRING, WgtActivity DOUBLE, WgtMarginalValue DOUBLE, WgtMin DOUBLE, WgtMax DOUBLE, IsActive INT, NodeTag STRING",
    "gold.rnm_mdm_vw_uom_conversion": "code STRING, multiply_by DOUBLE",
    "gold.rnm_mdm_vw_Org_Unit": "code STRING, name STRING",
    "gold.rnm_mdm_vw_material": "code STRING, name STRING, material_group_id_code STRING",
    "gold.duc_vw_rw_period": "SolutionID INT, Planmonth STRING, Period STRING, Revision STRING, PlanType STRING",
    "gold.duc_vw_rw_groupedstreams": "SolutionID INT, GroupTag STRING, StreamTag STRING, StreamDescription STRING, NodeTag STRING",
    "gold.duc_vw_rw_groupedstreamspurchases": "SolutionID INT, GroupTag STRING, Period STRING, PeriodDescription STRING, PeriodTag INT, PlanMonth STRING, Revision STRING, GroupMarginalValue DOUBLE, GroupMin DOUBLE, GroupMax DOUBLE, PlanType STRING, WgtActivity DOUBLE, UpdatedOn TIMESTAMP, IsActive INT",
    "silver.common_vw_mops": "Pims_tag STRING, api DOUBLE, bpt DOUBLE, created_on TIMESTAMP",
    "bronze.trading_term_crude_avail": "pims_tag STRING, month_value STRING, m_value DOUBLE",
    "bronze.duc_planmaster": "InputSetPeriod STRING, status STRING, Period STRING, Revision STRING",
    "bronze.Trading_Risk": "risk_id BIGINT, risk_date_time TIMESTAMP",
    "bronze.pricing_fpg_details": "pims_tag STRING, risk_id BIGINT, month_value STRING, profit_center STRING, created_on TIMESTAMP, crude_desc STRING, m_value DOUBLE, particular STRING",
}

# ---- Bug 1 views (same pattern as the real tables) ----
BUG1_VIEWS = [
    rf"""CREATE OR REPLACE VIEW {CAT}.gold.bug1_cell_flag_view (
  cell_name COMMENT 'Cell name derived as concat(ne_name, \'_\', cnum)',
  cssr_defaulting_cell_flag COMMENT 'Today\'s CSSR defaulting flag - ER: cssr_denom > 50 AND cssr < 99 -> Red; NK: vonr_attempts > 50 -> Red')
COMMENT 'Bug 1 (view): a column comment with an escaped apostrophe and a semicolon'
AS SELECT cell_name, cssr_defaulting_cell_flag
FROM {CAT}.gold.`5g_cust_exp_impr_congestion_impacted_cell_level`""",
    rf"""CREATE OR REPLACE VIEW {CAT}.gold.bug1_site_actions_view
COMMENT '\'Weekly site actions for field workflows. Built from consumption anomalies; excludes site/action pairs already actioned.\''
AS SELECT site_id, action_code, open_date
FROM {CAT}.gold.energy_site_action_details_weekly""",
]

# ---- Bug 2 tables (same stripping step; table-side variants) ----
BUG2_TABLES = [
    rf"""CREATE TABLE IF NOT EXISTS {CAT}.gold.bug2_props_escaped_quote (id INT, note STRING)
USING delta
COMMENT 'Bug 2 (table): a custom property value with an escaped quote and parentheses'
TBLPROPERTIES ('delta.enableDeletionVectors' = 'true', 'ai27.note' = 'it\'s (really) done, v2')""",
    rf"""CREATE TABLE IF NOT EXISTS {CAT}.gold.bug2_comment_mentions_tblproperties (id INT)
USING delta
COMMENT 'Bug 2 (table): this comment mentions TBLPROPERTIES (delta.minReaderVersion = 3) as plain text'
TBLPROPERTIES ('delta.enableDeletionVectors' = 'true')""",
]

# ---- Bug 3: '__' names and a schema ending in '_' (tables + views + grants + tags) ----
BUG3 = [
    f"""CREATE TABLE IF NOT EXISTS {CAT}.gold.sap_pcr__mrss_d_bas_asg (id INT, email STRING, amount DECIMAL(10,2))
USING delta COMMENT 'Bug 3: one __ in the table name'""",
    f"""CREATE TABLE IF NOT EXISTS {CAT}.gold.sap_pcr___mrss__d_chg_hism (id INT, changed_on DATE, note STRING)
USING delta COMMENT 'Bug 3: ___ and __ in the table name'""",
    f"""CREATE TABLE IF NOT EXISTS {CAT}.stage_.orders (id INT, region STRING)
USING delta COMMENT 'Bug 3: schema name ends in _'""",
    f"""INSERT INTO {CAT}.gold.sap_pcr__mrss_d_bas_asg SELECT * FROM VALUES (1, 'a@corp.com', 10.50), (2, 'b@corp.com', 20.00) t(id, email, amount)
WHERE (SELECT count(1) FROM {CAT}.gold.sap_pcr__mrss_d_bas_asg) = 0""",
    f"""CREATE OR REPLACE VIEW {CAT}.gold.sap_pcr__mrss_d_bas_asg_v COMMENT 'Bug 3: view with __ over a __ table'
AS SELECT id, amount FROM {CAT}.gold.sap_pcr__mrss_d_bas_asg""",
    f"""CREATE OR REPLACE VIEW {CAT}.gold.sap_pcr___mrss__d_chg_hism_v COMMENT 'Bug 3: view with ___ and __'
AS SELECT id, changed_on FROM {CAT}.gold.sap_pcr___mrss__d_chg_hism""",
    f"""CREATE OR REPLACE VIEW {CAT}.stage_.orders_v COMMENT 'Bug 3: view in a schema ending in _'
AS SELECT id, region FROM {CAT}.stage_.orders""",
    # governance on the bug-3 objects: the fix covers the grant / tag passes too
    f"ALTER TABLE {CAT}.gold.sap_pcr__mrss_d_bas_asg ALTER COLUMN email SET TAGS ('ai27_uc_pii' = 'EMAIL')",
    f"ALTER TABLE {CAT}.gold.sap_pcr___mrss__d_chg_hism SET TAGS ('ai27_uc_classification' = 'INTERNAL')",
    f"ALTER TABLE {CAT}.stage_.orders SET TAGS ('ai27_uc_classification' = 'INTERNAL')",
    f"GRANT SELECT ON TABLE {CAT}.gold.sap_pcr__mrss_d_bas_asg TO `{READER}`",
    f"GRANT SELECT ON TABLE {CAT}.gold.sap_pcr___mrss__d_chg_hism TO `{READER}`",
    f"GRANT SELECT ON TABLE {CAT}.stage_.orders TO `{GROUP}`",
    f"GRANT SELECT ON VIEW {CAT}.gold.sap_pcr__mrss_d_bas_asg_v TO `{GROUP}`",
    f"GRANT SELECT ON VIEW {CAT}.stage_.orders_v TO `{READER}`",
]

# ---- Bug 1 tables + Bug 2 views: the real SHOW CREATE outputs (scrubbed) ----
CUSTOMER_TABLES = [
    r'''CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.gold.`5g_cust_exp_impr_congestion_impacted_cell_level_temp` (   r4gstate STRING COLLATE UTF8_BINARY COMMENT 'State associated with the 4G network coverage area relevant to the 5G cell',   circle_code STRING COLLATE UTF8_BINARY COMMENT 'Geographic circle / region code where the cell is located (partition column)',   ops_centre STRING COLLATE UTF8_BINARY COMMENT 'Operational centre managing this cell',   ne_name STRING COLLATE UTF8_BINARY COMMENT 'Network element name uniquely identifying the cell within the network',   cnum STRING COLLATE UTF8_BINARY COMMENT 'Cell number derived from the last two characters of cellname',   band STRING COLLATE UTF8_BINARY COMMENT 'Frequency band the cell operates on (3500_1 / 3500_2 / 700) derived from cnum',   cell_name STRING COLLATE UTF8_BINARY COMMENT 'Cell name derived as concat(ne_name, \'_\', cnum)',   avg_tput_mbps_bbh DECIMAL(20,4) COMMENT 'BBH avg DL throughput Mbps: NK 5g_nk_rlc_layer_thpt / ER avg_dl_tput_user_overall_mbps_pm',   avg_tput_mbps_nom_bbh DECIMAL(20,4) COMMENT 'BBH avg DL throughput numerator: NK 5g_nk_rlc_layer_thpt_nom / ER avg_dl_tput_user_overall_mbps_pm_nom',   avg_tput_mbps_denom_bbh DECIMAL(20,4) COMMENT 'BBH avg DL throughput denominator: NK 5g_nk_rlc_layer_thpt_denom / ER avg_dl_tput_user_overall_mbps_pm_denom',   prb_util_dl_bbh DECIMAL(20,4) COMMENT 'BBH DL PRB utilization %: NK prb_util_dl_overall_pm / ER prb_util_dl_overall_including_pdcch_pm',   prb_util_dl_nom_bbh DECIMAL(20,4) COMMENT 'BBH DL PRB utilization numerator (NK / ER)',   prb_util_dl_denom_bbh DECIMAL(20,4) COMMENT 'BBH DL PRB utilization denominator (NK / ER)',   cell_flags STRING COLLATE UTF8_BINARY COMMENT '9-char bitmap (y=Red, n=Green) tracking Congestion_defaulting_flag over the last 9 partitions',   Congestion_defaulting_flag STRING COLLATE UTF8_BINARY COMMENT 'Today\'s congestion defaulting flag: Red when bbh tput/PRB cross band-specific thresholds, else Green',   Congestion_defaulting_consistency_flag STRING COLLATE UTF8_BINARY COMMENT 'Red when today\'s flag is Red and bitmap shows >= threshold Red days in last 9 partitions, else Green',   cssr_daily DECIMAL(20,4) COMMENT 'Daily CSSR: NK 157804_5g_nk_vonr_setup_success_rate_excluding_fallback_ongoing_xn_handover / ER cssr_accessibility_voice_pm',   cssr_nom_daily DECIMAL(20,4) COMMENT 'Daily CSSR numerator (NK / ER)',   cssr_denom_daily DECIMAL(20,4) COMMENT 'Daily CSSR denominator (NK / ER)',   cssr_cell_flags STRING COLLATE UTF8_BINARY COMMENT '9-char bitmap (y=Red, n=Green) tracking cssr_defaulting_cell_flag over the last 9 partitions',   cssr_defaulting_cell_flag STRING COLLATE UTF8_BINARY COMMENT 'Today\'s CSSR defaulting flag - ER: cssr_denom > 50 AND cssr < 99 -> Red; NK: vonr_attempts > 50 AND 157804 cssr < 99 -> Red',   cssr_defaulting_cell_consistency_flag STRING COLLATE UTF8_BINARY COMMENT 'Red when today\'s CSSR flag is Red and bitmap shows >= threshold Red days in last 9 partitions, else Green',   `5g_to_4g_hosr_daily` DECIMAL(20,4) COMMENT 'Daily 5G to 4G HOSR: NK 156549_5g_nk_nr_to_lte_ho_success_rate / ER vonr_to_volte_hosr_overall_pm',   `5g_to_4g_hosr_nom_daily` DECIMAL(20,4) COMMENT 'Daily 5G to 4G HOSR numerator (NK / ER)',   `5g_to_4g_hosr_denom_daily` DECIMAL(20,4) COMMENT 'Daily 5G to 4G HOSR denominator (NK / ER)',   `5g_to_5g_hosr_daily` DECIMAL(20,4) COMMENT 'Daily 5G to 5G HOSR: NK 156542_5g_nk_Intra_Inter_freq_vonr_ho_sr / ER 5g_to_5g_vonr_hosr_pm',   `5g_to_5g_hosr_nom_daily` DECIMAL(20,4) COMMENT 'Daily 5G to 5G HOSR numerator (NK / ER)',   `5g_to_5g_hosr_denom_daily` DECIMAL(20,4) COMMENT 'Daily 5G to 5G HOSR denominator (NK / ER)',   vonr_call_attempts_daily DECIMAL(20,4) COMMENT 'NK 156516_nk_vonr_call_attempts_excluding_fallback; null for Ericsson cells',   hosr_cell_flags STRING COLLATE UTF8_BINARY COMMENT '9-char bitmap (y=Red, n=Green) tracking hosr_defaulting_cell_flag over the last 9 partitions',   hosr_defaulting_cell_flag STRING COLLATE UTF8_BINARY COMMENT 'Today\'s HOSR defaulting flag - ER: cssr_denom > 50 AND 5g_to_5g_vonr_hosr_pm < 99 AND vonr_to_volte_hosr_overall_pm < 98.5 -> Red; NK: vonr_attempts > 50 AND 156542 < 99 AND 156549 < 98.5 -> Red',   hosr_defaulting_cell_consistency_flag STRING COLLATE UTF8_BINARY COMMENT 'Red when today\'s HOSR flag is Red and bitmap shows >= threshold Red days in last 9 partitions, else Green',   partition_date DATE COMMENT 'Date for which the data snapshot is recorded') USING delta PARTITIONED BY (partition_date, circle_code) TBLPROPERTIES (   'delta.autoOptimize.autoCompact' = 'true',   'delta.autoOptimize.optimizeWrite' = 'true',   'delta.enableChangeDataFeed' = 'true',   'delta.enableDeletionVectors' = 'true',   'delta.enableRowTracking' = 'true',   'delta.feature.appendOnly' = 'supported',   'delta.feature.changeDataFeed' = 'supported',   'delta.feature.deletionVectors' = 'supported',   'delta.feature.domainMetadata' = 'supported',   'delta.feature.invariants' = 'supported',   'delta.feature.rowTracking' = 'supported',   'delta.minReaderVersion' = '3',   'delta.minWriterVersion' = '7',   'delta.parquet.compression.codec' = 'ZSTD',   'delta.tuneFileSizesForRewrites' = 'true')''',
    r'''CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.gold.`5g_cust_exp_impr_congestion_impacted_cell_level` (   r4gstate STRING COLLATE UTF8_BINARY COMMENT 'Indicates the state associated with the 4G network coverage area relevant to the 5G cell data',   circle_code STRING COLLATE UTF8_BINARY COMMENT 'Denotes the geographic circle or region code where the network cell is located to help with regional analysis',   ops_centre STRING COLLATE UTF8_BINARY COMMENT 'Specifies the operational center or hub managing the particular cell, useful for administrative and support purposes',   vendor STRING COLLATE UTF8_BINARY COMMENT 'Identifies the manufacturer or provider of the cell hardware, which can be used for vendor-specific performance assessments',   ne_name STRING COLLATE UTF8_BINARY COMMENT 'Represents the network element name, a unique identifier for the cell within the network infrastructure',   cnum STRING COLLATE UTF8_BINARY COMMENT 'Cell number serving as a unique identification code for the specific network cell',   band STRING COLLATE UTF8_BINARY COMMENT 'Specifies the frequency band on which the cell operates, relevant for spectrum and interference analysis',   user_throughput DOUBLE COMMENT 'Measures the average data throughput experienced by users connected to the cell, indicating the quality of service',   prb_util DOUBLE COMMENT 'Shows the Physical Resource Block utilization percentage, reflecting cell resource usage and potential congestion levels',   cell_flags STRING COLLATE UTF8_BINARY COMMENT '9-char bitmap (y=Red, n=Green) tracking Congestion_defaulting_flag over the last 9 partitions',   cell_defaulting_flag STRING COLLATE UTF8_BINARY COMMENT 'Indicates whether the cell is currently defaulting or experiencing performance problems',   cell_defaulting_consistency_flag STRING COLLATE UTF8_BINARY COMMENT 'Shows the consistency status of the cell defaulting condition over time, useful for stability analysis',   cssr_cell_flags STRING COLLATE UTF8_BINARY COMMENT '9-char bitmap (y=Red, n=Green) tracking cssr_defaulting_cell_flag over the last 9 partitions',   cssr_defaulting_cell_flag STRING COLLATE UTF8_BINARY COMMENT 'Today\'s CSSR defaulting flag - ER: cssr_denom > 50 AND cssr < 99 -> Red; NK: vonr_attempts > 50 AND 157804 cssr < 99 -> Red',   cssr_defaulting_cell_consistency_flag STRING COLLATE UTF8_BINARY COMMENT 'Red when today\'s CSSR flag is Red and bitmap shows >= threshold Red days in last 9 partitions, else Green',   hosr_cell_flags STRING COLLATE UTF8_BINARY COMMENT '9-char bitmap (y=Red, n=Green) tracking hosr_defaulting_cell_flag over the last 9 partitions',   hosr_defaulting_cell_flag STRING COLLATE UTF8_BINARY COMMENT 'Today\'s HOSR defaulting flag - ER: cssr_denom > 50 AND 5g_to_5g_vonr_hosr_pm < 99 AND vonr_to_volte_hosr_overall_pm < 98.5 -> Red; NK: vonr_attempts > 50 AND 156542 < 99 AND 156549 < 98.5 -> Red',   hosr_defaulting_cell_consistency_flag STRING COLLATE UTF8_BINARY COMMENT 'Red when today\'s HOSR flag is Red and bitmap shows >= threshold Red days in last 9 partitions, else Green',   partition_date DATE COMMENT 'The date for which the data snapshot is recorded, allowing for chronological analysis of network conditions',   cell_name STRING COLLATE UTF8_BINARY COMMENT 'Cell identifier for impacted 5G cells.',   avg_tput_mbps_bbh DECIMAL(20,4) COMMENT 'Average throughput in Mbps for backhaul.',   avg_tput_mbps_nom_bbh DECIMAL(20,4) COMMENT 'Average throughput in Mbps, nominal, per baseband hotel.',   avg_tput_mbps_denom_bbh DECIMAL(20,4) COMMENT 'Average throughput denominator for BBH in 5G cells.',   prb_util_dl_bbh DECIMAL(20,4) COMMENT 'Downlink PRB utilization at BBH, measuring 5G cell congestion.',   prb_util_dl_nom_bbh DECIMAL(20,4) COMMENT 'Downlink PRB utilization metric for baseband hardware.',   prb_util_dl_denom_bbh DECIMAL(20,4) COMMENT 'Downlink PRB utilization denominator for baseband hardware.',   Congestion_defaulting_flag STRING COLLATE UTF8_BINARY COMMENT 'Indicates cell congestion defaulting status.',   Congestion_defaulting_consistency_flag STRING COLLATE UTF8_BINARY COMMENT 'Indicates congestion consistency status for impacted cells.',   cssr_daily DECIMAL(20,4) COMMENT 'Daily CSSR (Cell Satisfaction Ratio) metric for 5G cell congestion impact assessment.',   cssr_nom_daily DECIMAL(20,4) COMMENT 'Daily CSSR metric for cell congestion impact assessment.',   cssr_denom_daily DECIMAL(20,4) COMMENT 'Daily CSSR denominator for congestion impact calculation.',   `5g_to_4g_hosr_daily` DECIMAL(20,4) COMMENT 'Daily 5G to 4G handover success rate.',   `5g_to_4g_hosr_nom_daily` DECIMAL(20,4) COMMENT 'Daily 5G to 4G handover success rate.',   `5g_to_4g_hosr_denom_daily` DECIMAL(20,4) COMMENT 'Daily 5G to 4G handover density ratio.',   `5g_to_5g_hosr_daily` DECIMAL(20,4) COMMENT '5G handover success rate metric.',   `5g_to_5g_hosr_nom_daily` DECIMAL(20,4) COMMENT '5G handover success rate metric.',   `5g_to_5g_hosr_denom_daily` DECIMAL(20,4) COMMENT '5G handover success rate denominator for daily congestion analysis.',   vonr_call_attempts_daily DECIMAL(20,4) COMMENT 'Daily voice call attempts in congested 5G cells.') USING delta PARTITIONED BY (partition_date, circle_code) COMMENT 'This table stores customer experience improvement data for congestion-impacted cells in the 5G network, providing insights into cell-level performance metrics such as user throughput and PRB utilization. The data is organized by geographic location, vendor, and network equipment, and is partitioned by date for efficient analysis and reporting.' TBLPROPERTIES (   'delta.autoOptimize.autoCompact' = 'true',   'delta.autoOptimize.optimizeWrite' = 'true',   'delta.enableChangeDataFeed' = 'true',   'delta.enableDeletionVectors' = 'true',   'delta.enableRowTracking' = 'true',   'delta.feature.appendOnly' = 'supported',   'delta.feature.changeDataFeed' = 'supported',   'delta.feature.deletionVectors' = 'supported',   'delta.feature.domainMetadata' = 'supported',   'delta.feature.invariants' = 'supported',   'delta.feature.rowTracking' = 'supported',   'delta.minReaderVersion' = '3',   'delta.minWriterVersion' = '7',   'delta.parquet.compression.codec' = 'ZSTD',   'delta.tuneFileSizesForRewrites' = 'true')''',
    r'''CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.gold.energy_site_action_details_feedback_master_daily (   id BIGINT COMMENT 'Unique feedback record identifier',   state_code STRING COLLATE UTF8_BINARY COMMENT 'State or circle code associated with the site',   maintenance_point STRING COLLATE UTF8_BINARY COMMENT 'Maintenance point linked to the site',   ops_center_id STRING COLLATE UTF8_BINARY COMMENT 'Operations center identifier for the site',   site_id STRING COLLATE UTF8_BINARY COMMENT 'Site/SAP identifier for which the action feedback applies',   action_code STRING COLLATE UTF8_BINARY COMMENT 'Energy action code corresponding to the recommended remediation',   open_date DATE COMMENT 'Date when the action was opened for the site',   priority INT COMMENT 'Priority ranking of the action',   status STRING COLLATE UTF8_BINARY COMMENT 'Current status of the action (e.g. open, resolved)',   action_code_header STRING COLLATE UTF8_BINARY COMMENT 'Short header category for the action',   action_code_description STRING COLLATE UTF8_BINARY COMMENT 'Brief description of the action',   action_notes STRING COLLATE UTF8_BINARY COMMENT 'Field notes or remarks captured for the action',   data_week INT COMMENT 'ISO/data week number associated with the feedback record',   insert_date TIMESTAMP COMMENT 'Timestamp when the feedback record was inserted',   resolved_date TIMESTAMP COMMENT 'Timestamp when the action was marked resolved',   src_record_id STRING COLLATE UTF8_BINARY COMMENT 'Source system identifier for the feedback record',   partition_date DATE COMMENT 'Ingest partition date from bronze.energy_site_action_details_raw') USING delta COMMENT '\'Master table of energy site action details feedback from Oracle/JPW CDC. Upserted on site_id, action_code, data_week; delete op_type removes the matching row. Used by weekly site action and NHQ/SHQ summary jobs to exclude or count already-actioned/resolved sites.\'' TBLPROPERTIES (   'delta.autoOptimize.autoCompact' = 'true',   'delta.autoOptimize.optimizeWrite' = 'true',   'delta.enableDeletionVectors' = 'true',   'delta.enableRowTracking' = 'true',   'delta.feature.appendOnly' = 'supported',   'delta.feature.deletionVectors' = 'supported',   'delta.feature.domainMetadata' = 'supported',   'delta.feature.invariants' = 'supported',   'delta.feature.rowTracking' = 'supported',   'delta.minReaderVersion' = '3',   'delta.minWriterVersion' = '7',   'delta.parquet.compression.codec' = 'ZSTD',   'delta.tuneFileSizesForRewrites' = 'true')''',
    r'''CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.gold.energy_site_action_details_weekly (   state_code STRING COLLATE UTF8_BINARY COMMENT 'State or circle code associated with the site',   maintenance_point STRING COLLATE UTF8_BINARY COMMENT 'Maintenance point linked to the site',   ops_center_id STRING COLLATE UTF8_BINARY COMMENT 'Operations center identifier for the site',   site_id STRING COLLATE UTF8_BINARY COMMENT 'Site/SAP identifier for which the action applies',   action_code STRING COLLATE UTF8_BINARY COMMENT 'Energy action code recommended for remediation',   action_category_name STRING COLLATE UTF8_BINARY COMMENT 'Human-readable action category name',   action_category STRING COLLATE UTF8_BINARY COMMENT 'High-level action category code',   action_code_problem STRING COLLATE UTF8_BINARY COMMENT 'Problem statement for the field technician',   step_by_step_fix STRING COLLATE UTF8_BINARY COMMENT 'Step-by-step remediation instructions',   open_date DATE COMMENT 'Date when the action was opened (partition key)',   priority INT COMMENT 'Priority ranking of the action from action master',   status STRING COLLATE UTF8_BINARY COMMENT 'Current status of the action (e.g. open)',   action_code_header STRING COLLATE UTF8_BINARY COMMENT 'Short header category for the action',   action_code_description STRING COLLATE UTF8_BINARY COMMENT 'Brief description of the action',   week_cost_sum DOUBLE COMMENT 'Weekly energy cost sum for the site',   week_kwh_sum DOUBLE COMMENT 'Weekly energy consumption in kilowatt-hours',   week_traffic_gb_sum DOUBLE COMMENT 'Weekly data traffic in gigabytes',   rs_per_unit_rank INT COMMENT 'Dense rank of site by Rs/unit within JC (lower cost efficiency = higher rank)',   jpw_tag STRING COLLATE UTF8_BINARY COMMENT 'JPW push flag: push when rs_per_unit_rank <= 10, else not push',   date_week INT COMMENT 'ISO week number derived from open_date') USING delta PARTITIONED BY (open_date) COMMENT '\'Weekly site-level energy optimization actions recommended for field/JPW workflows. Built from consumption anomalies, EB billing actions, and action-master guidance; excludes site/action pairs already present in daily feedback. Ranked by Rs/unit within JC for push prioritization.\'' TBLPROPERTIES (   'delta.autoOptimize.autoCompact' = 'true',   'delta.autoOptimize.optimizeWrite' = 'true',   'delta.enableDeletionVectors' = 'true',   'delta.enableRowTracking' = 'true',   'delta.feature.appendOnly' = 'supported',   'delta.feature.deletionVectors' = 'supported',   'delta.feature.domainMetadata' = 'supported',   'delta.feature.invariants' = 'supported',   'delta.feature.rowTracking' = 'supported',   'delta.minReaderVersion' = '3',   'delta.minWriterVersion' = '7',   'delta.parquet.compression.codec' = 'ZSTD',   'delta.tuneFileSizesForRewrites' = 'true')''',
]

CUSTOMER_VIEWS = [
    r'''CREATE OR REPLACE VIEW ai27_ucsync_testcatalog.gold.`dsv-pno-dpv-cracker-feed-mix-dmd-view` (
  SolutionID COMMENT 'SolutionID is a unique integer identifier for a solution in the feed mix data.',
  SiteCode COMMENT 'SiteCode represents a unique site identifier as a string value.',
  Division COMMENT 'Division represents a specific geographic or operational area within an organization where certain activities or plans are being implemented or tracked.',
  Period COMMENT 'Period represents a specific month and year in a abbreviated format such as month abbreviation and year`s last two digits.',
  PlanMonth COMMENT 'PlanMonth represents a specific month and year for planning purposes in a format like month-year.',
  Revision COMMENT 'Revision indicates a specific version of a plan or solution with a unique identifier.',
  FeedStream COMMENT 'FeedStream represents the name of the feed stream used in the planning process, typically indicating the type of feedstock or material being utilized.',
  Stream COMMENT 'Stream represents a void or null data type indicating absence of a specific stream value.',
  Activity COMMENT 'Activity represents a numeric value of type double indicating some level of action or operation related to feed stream planning.',
  PlanType COMMENT 'PlanType represents a short code indicating the type of plan associated with a feed stream.',
  UpdatedOn COMMENT 'UpdatedOn represents the timestamp when the record was last updated.')
COMMENT 'Data represents planning and feed stream information for various sites and divisions with details such as solution identifiers, site codes, periods, plan months, revisions, feed streams, activities, plan types, and update timestamps.'
TBLPROPERTIES (
  'comment' = 'Data represents planning and feed stream information for various sites and divisions with details such as solution identifiers, site codes, periods, plan months, revisions, feed streams, activities, plan types, and update timestamps.')
WITH SCHEMA COMPENSATION
AS WITH
/*
Objective  : 
Created By  : <author>
Created On     : 27-Aug-2024
Version No  : 1.0

## Change History ######
Revision By     : 
Revision On  : 
Change Details  : 
*/
temp
AS (
   SELECT DISTINCT
    SolutionID,
       Period,
       PlanMonth,
       (Activity*U.multiply_by) AS Activity,   -- to convert ktpd to tpd
       Revision,
    null AS stream, 
       PlanType,
       UpdatedOn,
       'DMD' AS SiteCode,
       'Propane JMD' AS FeedStream
   FROM ai27_ucsync_testcatalog.gold.duc_vw_rw_unitmaterials t1
   INNER JOIN ai27_ucsync_testcatalog.gold.rnm_mdm_vw_uom_conversion U ON 1=1 
   WHERE StreamTag in ( 'PRH' ) and FeedFlag = 1 and t1.IsActive = 1
         --and Revision like 'RP%'
   and PlanType = 'RP' and NodeTag = 'D' AND U.code = 'KTPDTOTPD'
UNION ALL
   SELECT DISTINCT
       SolutionID,
    Period,
       PlanMonth,
       (Activity*U.multiply_by) AS Activity, -- to convert ktpd to tpd
       Revision,
    null AS stream,
       PlanType,
       UpdatedOn,
       'DMD' AS SiteCode,
       'Ethane' AS FeedStream
   FROM ai27_ucsync_testcatalog.gold.duc_vw_rw_unitcapacities t1
   INNER JOIN ai27_ucsync_testcatalog.gold.rnm_mdm_vw_uom_conversion U ON 1=1 
   WHERE CapacityTag in ( 'ETD' ) and FeedFlag = 1 and t1.IsActive = 1
         --and Revision like 'RP%'
   and PlanType = 'RP' and NodeTag = 'D' AND U.code = 'KTPDTOTPD'
UNION ALL
   SELECT DISTINCT
    SolutionID,
       Period,
       PlanMonth,
       SUM(WgtActivity*U.multiply_by) AS Activity, -- to convert ktpd to tpd
       Revision,
    null AS stream,
       PlanType,
       UpdatedOn,
       'DMD' AS SiteCode,
       'Propane Import' AS FeedStream
   FROM ai27_ucsync_testcatalog.gold.duc_vw_rw_purchases
   INNER JOIN ai27_ucsync_testcatalog.gold.rnm_mdm_vw_uom_conversion U ON 1=1
   WHERE StreamTag in ( 'PRN','C3F' ) and IsActive = 1
         --and Revision like 'RP%'
   and PlanType = 'RP' and NodeTag = 'D' AND U.code = 'KTPDTOTPD'
   GROUP BY Period,PlanMonth, Revision,PlanType,SolutionID, UpdatedOn--stream
   )
SELECT DISTINCT SolutionID,
SiteCode,
t2.name AS Division,
Period,
PlanMonth,
Revision,
FeedStream,
Stream,
Activity,
PlanType,
UpdatedOn 
FROM temp t1
LEFT OUTER JOIN ai27_ucsync_testcatalog.gold.rnm_mdm_vw_Org_Unit t2 ON t1.SiteCode = t2.code''',
    r'''CREATE OR REPLACE VIEW ai27_ucsync_testcatalog.gold.`dsv-pno-dpv-crude-sourcing-plan-target-ranking-negative-marginal-value-crude-view` (
  SolutionID COMMENT 'SolutionID is a unique identifier for a solution in the crude sourcing plan target ranking negative marginal value crude view.',
  CrudeName COMMENT 'CrudeName represents the name of a crude oil type or blend in the crude sourcing plan.',
  Period COMMENT 'Period represents a time frame in a specific format such as month and year.',
  PeriodTag COMMENT 'PeriodTag represents a numeric identifier associated with a specific period in the crude sourcing plan.',
  PlanMonth COMMENT 'PlanMonth represents the month for which a crude sourcing plan is being made, in a format of three-letter month abbreviation followed by four-digit year.',
  Revision COMMENT 'Revision represents a specific version or iteration of the crude sourcing plan, identified by a unique label indicating the plan type and revision number.',
  Volume COMMENT 'Volume represents the amount of crude oil in a specific plan.',
  MarginalValue COMMENT 'MarginalValue represents a double value indicating the marginal value of a crude oil in a sourcing plan.',
  LandedCost COMMENT 'LandedCost represents the total cost of acquiring and transporting crude oil to a specific location, expressed as a double value.',
  PlanType COMMENT 'PlanType represents the type of plan associated with crude sourcing targets, typically indicated by a short code such as RP.',
  Rank COMMENT 'Rank represents a prioritization or ordering of crude sourcing plans based on their marginal value.',
  Color COMMENT 'Color represents a visual categorization of crude sourcing plan data, indicated by a specific color name, such as Light Blue.',
  RankBasedOn COMMENT 'RankBasedOn indicates the basis for ranking crude sources such as ROBUST_CRUDE.',
  UpdatedOn COMMENT 'UpdatedOn represents the timestamp when the record was last updated.')
COMMENT 'Crude oil sourcing plan data represents target rankings of crude types based on negative marginal value with associated attributes such as volume landed cost and plan type.'
TBLPROPERTIES (
  'comment' = 'Crude oil sourcing plan data represents target rankings of crude types based on negative marginal value with associated attributes such as volume landed cost and plan type.')
WITH SCHEMA COMPENSATION
AS WITH M3TOBBL AS (
    SELECT multiply_by AS M3TOBBL
    FROM ai27_ucsync_testcatalog.gold.rnm_mdm_vw_uom_conversion
    WHERE code = 'M3TOBBL'
),
KTTOKBBL AS (
    SELECT multiply_by AS KTTOKBBL
    FROM ai27_ucsync_testcatalog.gold.rnm_mdm_vw_uom_conversion
    WHERE code = 'KTTOKBBL'
),
crude AS (
    SELECT Pims_tag AS code, api, bpt, m.name  
    FROM ai27_ucsync_testcatalog.silver.common_vw_mops mp
    INNER JOIN ai27_ucsync_testcatalog.gold.rnm_mdm_vw_material m ON mp.pims_tag = m.code
    WHERE m.material_group_id_code = 'CRD'
      AND created_on = (SELECT MAX(created_on) FROM ai27_ucsync_testcatalog.silver.common_vw_mops)
),
Termavails AS (
    SELECT pims_tag, c.name, DATE_FORMAT(to_date(month_value, 'dd-MMM-yyyy'), 'MMM-yy') AS Period, m_value
    FROM ai27_ucsync_testcatalog.bronze.trading_term_crude_avail a
    INNER JOIN crude c ON a.pims_tag = c.code
    WHERE m_value > 0
),
Landed_Cost AS (
    SELECT SolutionID,crude_desc, Planmonth, Period, Revision, pims_tag, SUM(m_value) AS Landed_Cost_WRT_Dubai
    FROM (
        SELECT
            t1.SolutionID,
            crude_desc,
            m_value,
            t1.Planmonth,
            t1.Period,
            t1.Revision,
            t4.pims_tag,
            t1.PlanType,
            ROW_NUMBER() OVER (PARTITION BY t4.pims_tag,t4.risk_id, t4.month_value, t4.profit_center ORDER BY t4.created_on DESC) AS RN
        FROM ai27_ucsync_testcatalog.gold.duc_vw_rw_period t1
        INNER JOIN (
            SELECT *, CAST(SUBSTRING(InputSetPeriod, 1, 24) AS TIMESTAMP) AS Input_Set_Period
            FROM ai27_ucsync_testcatalog.bronze.duc_planmaster
            WHERE LENGTH(InputSetPeriod) > 18 AND status = 'Approved'
        ) t2 ON t2.Period = t1.PlanMonth AND t1.Revision = t2.Revision
        INNER JOIN ai27_ucsync_testcatalog.bronze.Trading_Risk T3 ON T3.risk_date_time = t2.Input_Set_Period
        INNER JOIN (
            SELECT *, DATE_FORMAT(to_date(month_value, 'dd-MMM-yyyy'), 'MMM-yy') AS Period
            FROM ai27_ucsync_testcatalog.bronze.pricing_fpg_details
            WHERE profit_center = 'DTA'
        ) t4 ON t4.Period = t1.Period AND T3.risk_id = t4.risk_id
        WHERE particular = 'LANDED_COST_DOLLAR_BBL_WRT_DUBAI'
    ) a
    WHERE RN = 1
    GROUP BY SolutionID,crude_desc, Planmonth, Period, Revision, PlanType,pims_tag
),
CTE AS (
    SELECT
        p1.SolutionID,
        p1.StreamDescription AS `Crude Name`,
        p1.Period,
        p1.PeriodTag,
        p1.PlanMonth,
        p1.Revision,
        (p1.WgtActivity * p1.PeriodLength * c.bpt) AS Volume,
        (p1.WgtMarginalValue / c.bpt) AS MV,
        L.Landed_Cost_WRT_Dubai,
        p1.PlanType,
        CASE  
            WHEN p1.WgtMin > 0 THEN 'Light Blue'
            WHEN p1.WgtActivity BETWEEN p1.WgtMin AND p1.WgtMax THEN 'Yellow'
            ELSE 'Yellow'
        END AS Color,
        p1.UpdatedOn
    FROM ai27_ucsync_testcatalog.gold.duc_vw_rw_purchases p1
    INNER JOIN ai27_ucsync_testcatalog.gold.duc_vw_rw_purchases p2
        ON p1.SolutionID = p2.SolutionID
        AND p1.StreamTag = p2.StreamTag
        AND p1.PlanMonth = p2.PlanMonth
        AND p1.Period = p2.Period
        AND p1.Revision = p2.Revision
    LEFT JOIN Landed_Cost L
        ON L.pims_tag = p1.streamtag
        AND L.PlanMonth = p1.Planmonth
        AND L.Period = p1.Period
        AND L.Revision = p1.Revision
    INNER JOIN crude c
        ON c.code = p1.STreamTag
    INNER JOIN M3TOBBL m ON 1 = 1
    INNER JOIN KTTOKBBL k ON 1 = 1
    WHERE p1.PlanType = 'RP'
      AND p1.WgtMarginalValue < 0
      AND p1.NodeTag = '.'
      AND p2.NodeTag = 'A'
      AND p1.WgtActivity > 0 AND p1.IsActive = 1
    --   AND P1.planmonth='Apr-2025'
    --   and P1.Revision like '%Rev1%'
),
CTE1 AS (
    SELECT
        p1.SolutionID,
        p1.StreamDescription AS `Crude Name`,
        p1.Period,
        p1.PeriodTag,
        p1.PlanMonth,
        p1.Revision,
        (p1.WgtActivity * p1.PeriodLength * c.bpt) AS Volume,
        (p1.WgtMarginalValue / c.bpt) AS MV,
        L.Landed_Cost_WRT_Dubai,
        p1.PlanType,
        CASE
            WHEN ta.m_value > 0 THEN 'Light Green'
            ELSE 'NA'
        END AS Color,
        p1.UpdatedOn
    FROM ai27_ucsync_testcatalog.gold.duc_vw_rw_purchases p1
    LEFT JOIN Landed_Cost L
        ON L.pims_tag = p1.streamtag
        AND L.PlanMonth = p1.Planmonth
        AND L.Period = p1.Period
        AND L.Revision = p1.Revision
    INNER JOIN ai27_ucsync_testcatalog.gold.duc_vw_rw_purchases p2
        ON p1.SolutionID = p2.SolutionID
        AND p1.StreamTag = p2.StreamTag
        AND p1.PlanMonth = p2.PlanMonth
        AND p1.Period = p2.Period
        AND p1.Revision = p2.Revision
    LEFT JOIN Termavails ta
        ON ta.pims_tag = p1.STreamTag
        AND ta.Period = p1.Period
    INNER JOIN crude c
        ON c.code = p1.STreamTag
    INNER JOIN M3TOBBL m ON 1 = 1
    INNER JOIN KTTOKBBL k ON 1 = 1
    WHERE p1.PlanType = 'RP'
      AND p1.WgtMarginalValue < 0
      AND p1.NodeTag = '.'
      AND p2.NodeTag = 'A'
      AND p1.WgtActivity > 0 AND p1.IsActive = 1
    --    AND P1.planmonth='Apr-2025'
    --   and P1.Revision like '%Rev1%'
),
GroupTags AS (
    SELECT DISTINCT SolutionID,GroupTag, a.streamtag, c.bpt, a.StreamDescription
    FROM ai27_ucsync_testcatalog.gold.duc_vw_rw_groupedstreams a
    INNER JOIN Crude c ON c.code = a.STreamTag
    where a.NodeTag='.'  -- Added on 03-02-2025
),
GroupMin AS (
    SELECT
        c.SolutionID,
        g.StreamDescription AS `Crude Name`,
        c.Period,
        c.PeriodTag,
        c.PlanMonth,
        c.Revision,
        -- SUM(c.WgtActivity * c.PeriodLength * g.bpt) AS Volume,
        MAX(p2.WgtActivity * p2.PeriodLength * g.bpt) AS Volume,
        SUM(c.GroupMarginalValue / g.bpt) AS MV,
        L.Landed_Cost_WRT_Dubai,
        c.PlanType,
        CASE  
            WHEN c.GroupMin > 0 THEN 'Light Blue'
            WHEN c.WgtActivity BETWEEN c.GroupMin AND c.GroupMax THEN 'Yellow'
            ELSE 'Yellow'
        END AS Color,
        c.UpdatedOn
    FROM ai27_ucsync_testcatalog.gold.duc_vw_rw_groupedstreamspurchases c
    INNER JOIN GroupTags g ON c.GroupTag = g.GroupTag and c.SolutionID=g.SolutionID
    LEFT JOIN Landed_Cost L
        ON L.pims_tag = g.streamtag
        AND L.PlanMonth = c.Planmonth
        AND L.Period = c.Period
        AND L.Revision = c.Revision
    INNER JOIN ai27_ucsync_testcatalog.gold.duc_vw_rw_purchases p2
        ON c.SolutionID = p2.SolutionID
        AND g.StreamTag = p2.StreamTag
        AND g.SolutionID = p2.SolutionID
        AND c.PlanMonth = p2.PlanMonth
        AND c.PeriodDescription = p2.Period
        AND c.Revision = p2.Revision
    WHERE p2.WgtActivity > 0  /*Check individual tag's volume in purchase table .it should be >0*/
    -- c.WgtActivity > 0
      AND p2.nodetag = '.' AND c.IsActive = 1
    --   AND c.planmonth='Apr-2025'
    --   AND c.Revision like '%Rev1%'
    GROUP BY
        c.SolutionID,
        g.StreamDescription,
        c.Period,
        c.PeriodTag,
        c.PlanMonth,
        c.Revision,
        c.GroupMin,
        c.GroupMax,
        L.Landed_Cost_WRT_Dubai,
        c.PlanType,
        c.WgtActivity,
        c.UpdatedOn
),
union_cte AS (
    SELECT * FROM CTE
    UNION
    SELECT * FROM CTE1
    UNION
    SELECT * FROM GroupMin
),
final AS (
    SELECT DISTINCT
        SolutionID,
        `Crude Name`,
        Period,
        PeriodTag,
        PlanMonth,
        Revision,
        Volume,
        MV,
        Landed_Cost_WRT_Dubai,
        PlanType,
        DENSE_RANK() OVER (PARTITION BY SolutionID, PlanMonth, Period, Revision ORDER BY MV ASC) AS Rank,
        color,
        UpdatedOn
    FROM union_cte
   -- WHERE Color IN (SELECT Color FROM colors_table)  
--    where color is not null
    where color in ('Yellow','Light Blue','Light Green')
    and MV < 0
),
final2 AS (
    SELECT
        SolutionID,
        `Crude Name`,
        Period,
        PeriodTag,
        PlanMonth,
        Revision,
        Volume,
        MV,
        Landed_Cost_WRT_Dubai,
        PlanType,
        Rank,
        COUNT(Rank) OVER (PARTITION BY SolutionID, `Crude Name`, Period, PeriodTag, PlanMonth, Revision ORDER BY Period) AS cnt,
        color,
        UpdatedOn
    FROM final
)
SELECT DISTINCT
    SolutionID,
    CASE
        WHEN `Crude Name` IN ('Iraqi SRFO-2', 'IRAQI SRFO 2') THEN 'Iraqi SRFO-2'
        WHEN `Crude Name` IN ('Ryazan SRFO', 'Ryazan Fuel Oil') THEN 'Ryazan SRFO'
        ELSE `Crude Name`
    END AS `CrudeName`,
    Period,
    PeriodTag,
    PlanMonth,
    Revision,
    Volume,
    MV AS `MarginalValue`,
    Landed_Cost_WRT_Dubai AS `LandedCost`,
    PlanType,
    Rank,
    CASE
        WHEN cnt = 2 THEN 'Light Green'
        WHEN cnt = 1 AND color = 'Yellow' THEN 'Yellow'
        ELSE 'Light Blue'
    END AS Color,
    'ROBUST_CRUDE' AS RankBasedOn,
    UpdatedOn
FROM final2
WHERE Rank <= 10''',
]


def run(label, statements):
    bad = 0
    for stmt in statements:
        state, err, _ = sql(stmt)
        head = " ".join(stmt.split())[:95]
        print(f"  [{state:>10}] {head}")
        if state != "SUCCEEDED":
            bad += 1
            print(f"             ERROR: {err[:300]}")
    print(f"{label}: {len(statements) - bad}/{len(statements)} ok")
    return bad


def show_create(name):
    state, err, rows = sql(f"SHOW CREATE TABLE {name}")
    return rows[0][0] if state == "SUCCEEDED" and rows else f"<{state}: {err[:200]}>"


def verify():
    """Confirm the source's own SHOW CREATE output carries each bug's pattern."""
    g = f"{CAT}.gold"
    checks = [
        ("Bug 1", f"{g}.`5g_cust_exp_impr_congestion_impacted_cell_level_temp`", lambda d: "\\'" in d),
        ("Bug 1", f"{g}.`5g_cust_exp_impr_congestion_impacted_cell_level`", lambda d: "\\'" in d),
        ("Bug 1", f"{g}.energy_site_action_details_feedback_master_daily", lambda d: "COMMENT '\\'" in d),
        ("Bug 1", f"{g}.energy_site_action_details_weekly", lambda d: "COMMENT '\\'" in d),
        ("Bug 1", f"{g}.bug1_cell_flag_view", lambda d: "\\'" in d),
        ("Bug 1", f"{g}.bug1_site_actions_view", lambda d: "COMMENT '\\'" in d),
        ("Bug 2", f"{g}.`dsv-pno-dpv-cracker-feed-mix-dmd-view`",
         lambda d: 0 <= d.find("TBLPROPERTIES") < d.find("WITH SCHEMA")),
        ("Bug 2", f"{g}.`dsv-pno-dpv-crude-sourcing-plan-target-ranking-negative-marginal-value-crude-view`",
         lambda d: 0 <= d.find("TBLPROPERTIES") < d.find("WITH SCHEMA")),
        ("Bug 2", f"{g}.bug2_props_escaped_quote", lambda d: "it\\'s (really)" in d),
        ("Bug 2", f"{g}.bug2_comment_mentions_tblproperties",
         lambda d: "mentions TBLPROPERTIES (" in d),
        ("Bug 3", f"{g}.sap_pcr__mrss_d_bas_asg", lambda d: "sap_pcr__mrss" in d),
        ("Bug 3", f"{g}.sap_pcr___mrss__d_chg_hism", lambda d: "sap_pcr___mrss__" in d),
        ("Bug 3", f"{CAT}.stage_.orders", lambda d: "stage_" in d),
        ("Bug 3", f"{g}.sap_pcr__mrss_d_bas_asg_v", lambda d: "sap_pcr__mrss" in d),
        ("Bug 3", f"{g}.sap_pcr___mrss__d_chg_hism_v", lambda d: "sap_pcr___mrss__" in d),
        ("Bug 3", f"{CAT}.stage_.orders_v", lambda d: "stage_" in d),
    ]
    bad = 0
    print("\n=== SHOW CREATE pattern check ===")
    for bug, name, ok in checks:
        ddl = show_create(name)
        passed = (not ddl.startswith("<")) and ok(ddl)
        bad += 0 if passed else 1
        print(f"  {bug}  {'PATTERN OK' if passed else 'MISSING   '}  {name}")
        if not passed:
            print(f"           {ddl[:300]}")
    return bad


if __name__ == "__main__":
    bad = run("schemas", [
        f"CREATE SCHEMA IF NOT EXISTS {CAT}.{s} MANAGED LOCATION '{ROOT}/{s}' COMMENT '{c}'"
        for s, c in SCHEMAS])
    bad += run("bug-2 view dependencies", [
        f"CREATE TABLE IF NOT EXISTS {CAT}.{t} ({cols}) USING delta COMMENT 'Dependency of a bug-2 view'"
        for t, cols in STUBS.items()])
    bad += run("bug 1 tables (real DDL)", CUSTOMER_TABLES)
    bad += run("bug 1 views", BUG1_VIEWS)
    bad += run("bug 2 views (real DDL)", CUSTOMER_VIEWS)
    bad += run("bug 2 tables", BUG2_TABLES)
    bad += run("bug 3 tables + views + governance", BUG3)
    bad += verify()
    print(f"=== bug-fix stage done (problems: {bad}) ===")
    sys.exit(1 if bad else 0)
