-- Fail-closed negative-test fixtures. These reference an OUT-OF-SCOPE catalog
-- `ai_27` (deliberately NOT part of the migration) so the target import fails
-- closed:
--   * hr.ext_masked  -> inline MASK ai_27.sec.mask_ext  => target CREATE TABLE must FAIL
--   * hr.abac_ext    -> ABAC policy masks via ai_27.sec.mask_ext => created then DROPPED on target
-- Both live in ai27_uc_gov_src.hr and require ai_27.sec.mask_ext to exist on the
-- SOURCE so the inline mask / policy can be created here.

-- ---- prerequisite: out-of-scope ai_27 catalog + masking function ----
-- No MANAGED LOCATION: ai_27 holds only a function (no data), so it uses the
-- metastore default storage. If the metastore has no default root, give ai_27 a
-- MANAGED LOCATION on any available external location.
CREATE CATALOG IF NOT EXISTS ai_27 COMMENT 'Out-of-scope catalog for fail-closed negative tests';
CREATE SCHEMA IF NOT EXISTS ai_27.sec COMMENT 'Out-of-scope UDFs';
CREATE OR REPLACE FUNCTION ai_27.sec.mask_ext(v STRING) RETURNS STRING RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE '***' END;

-- ---- ext_masked: inline MASK to an un-migrated catalog function ----
CREATE TABLE IF NOT EXISTS ai27_uc_gov_src.hr.ext_masked (
  id INT COMMENT 'id', secret STRING COMMENT 'secret masked by an out-of-scope catalog function')
USING DELTA COMMENT 'NEGATIVE TEST: inline MASK references ai_27 (NOT migrated) -> target CREATE TABLE must fail';
ALTER TABLE ai27_uc_gov_src.hr.ext_masked ALTER COLUMN secret SET MASK ai_27.sec.mask_ext;

-- ---- abac_ext: ABAC policy masks via an un-migrated catalog function ----
CREATE TABLE IF NOT EXISTS ai27_uc_gov_src.hr.abac_ext (
  id INT COMMENT 'id', email STRING COMMENT 'email')
USING DELTA COMMENT 'NEGATIVE TEST: ABAC policy masks via ai_27 (NOT migrated) -> created then DROPPED fail-closed on target';
ALTER TABLE ai27_uc_gov_src.hr.abac_ext ALTER COLUMN email SET TAGS ('ai27_uc_pii'='EMAIL');
CREATE POLICY ai27_uc_mask_ext_neg ON TABLE ai27_uc_gov_src.hr.abac_ext COLUMN MASK ai_27.sec.mask_ext TO `account users` FOR TABLES MATCH COLUMNS has_tag_value('ai27_uc_pii', 'EMAIL') AS c ON COLUMN c;
