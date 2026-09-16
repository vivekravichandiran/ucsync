-- ai27_ucsync_testcatalog — fail-closed NEGATIVE fixtures.
-- They reference the out-of-scope catalog ai_27 (never migrated). On the BYO target,
-- ai_27.sec.mask_ext does NOT exist, so these must fail-closed:
--   * ext_masked  -> inline MASK to ai_27 fn -> target CREATE TABLE must FAIL
--   * abac_ext    -> ABAC policy via ai_27 fn -> created then DROPPED on target
-- ai_27 + ai_27.sec.mask_ext already exist on source (shared with the legacy bundle);
-- recreated defensively here. Storage root is the legacy gov account (ai27govd1a1e8).

CREATE CATALOG IF NOT EXISTS ai_27
  MANAGED LOCATION 'abfss://data@ai27govd1a1e8.dfs.core.windows.net/ai_27_root'
  COMMENT 'Out-of-scope catalog for fail-closed negative tests';
CREATE SCHEMA IF NOT EXISTS ai_27.sec COMMENT 'Out-of-scope UDFs';
CREATE OR REPLACE FUNCTION ai_27.sec.mask_ext(v STRING)
  RETURNS STRING COMMENT 'Out-of-scope mask (never migrated)'
  RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE '***' END;

-- ===== NEGATIVE 1: inline MASK references an un-migrated function =====
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.core_tables.ext_masked (
  id INT COMMENT 'id',
  secret STRING COMMENT 'masked by an out-of-scope ai_27 function'
) USING DELTA
COMMENT 'NEGATIVE: inline MASK -> ai_27 (not migrated) -> target CREATE TABLE must FAIL';
ALTER TABLE ai27_ucsync_testcatalog.core_tables.ext_masked
  ALTER COLUMN secret SET MASK ai_27.sec.mask_ext;

-- ===== NEGATIVE 2: ABAC policy masks via an un-migrated function =====
-- Uses the BANK_ACCOUNT tag + a TABLE-scoped policy so it does not collide with the
-- catalog EMAIL / schema PHONE policies (which reference migrated functions).
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.governed.abac_ext (
  id INT COMMENT 'id',
  secret STRING COMMENT 'masked via ABAC policy referencing ai_27'
) USING DELTA
COMMENT 'NEGATIVE: ABAC policy via ai_27 (not migrated) -> created then DROPPED fail-closed on target';
ALTER TABLE ai27_ucsync_testcatalog.governed.abac_ext
  ALTER COLUMN secret SET TAGS ('ai27_uc_pii'='BANK_ACCOUNT');
CREATE POLICY ai27_ucsync_neg_ext ON TABLE ai27_ucsync_testcatalog.governed.abac_ext
  COLUMN MASK ai_27.sec.mask_ext
  TO `account users`
  FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_pii','BANK_ACCOUNT') AS c ON COLUMN c;
