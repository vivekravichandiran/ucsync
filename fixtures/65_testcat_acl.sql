-- ai27_ucsync_testcatalog — ACL matrix + ownership variety.
-- Users: {{TC_USER_ALL}} (all), {{TC_USER_READ}} (reader), {{TC_USER_USECAT}} (USE CATALOG only).
-- Groups: {{TC_GROUP_READER}} / {{TC_GROUP_ENGINEER}} / {{TC_GROUP_ANALYST}}.
-- SPNs: {{TC_SPN_WRITER}} (read+write), {{TC_SPN_READER}} (read-only). Export SP {{EXPORT_SP}}.
-- NOTE: UC has no DENY — "no access" = simply granting nothing. Catalog-level SELECT
-- cascades to all schemas (incl. restricted); the restricted no-access contrast is
-- enforced for the two USERS (idris/sanket get NO catalog SELECT).

-- ===================== CATALOG-LEVEL =====================
GRANT ALL PRIVILEGES ON CATALOG ai27_ucsync_testcatalog TO `{{TC_USER_ALL}}`;
GRANT USE CATALOG, USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON CATALOG ai27_ucsync_testcatalog TO `{{TC_SPN_WRITER}}`;
GRANT USE CATALOG, USE SCHEMA, SELECT ON CATALOG ai27_ucsync_testcatalog TO `{{TC_SPN_READER}}`;
GRANT USE CATALOG, SELECT ON CATALOG ai27_ucsync_testcatalog TO `{{TC_GROUP_READER}}`;
GRANT USE CATALOG, BROWSE ON CATALOG ai27_ucsync_testcatalog TO `{{TC_GROUP_ANALYST}}`;
GRANT USE CATALOG ON CATALOG ai27_ucsync_testcatalog TO `{{TC_USER_USECAT}}`;
GRANT USE CATALOG ON CATALOG ai27_ucsync_testcatalog TO `{{TC_USER_READ}}`;
GRANT USE CATALOG ON CATALOG ai27_ucsync_testcatalog TO `account users`;
-- Source-read SPNs need read + MANAGE (ACL reads) + EXECUTE/READ VOLUME for SHOW CREATE capture
GRANT USE CATALOG, USE SCHEMA, SELECT, EXECUTE, READ VOLUME, MANAGE ON CATALOG ai27_ucsync_testcatalog TO `{{TC_READ_SPN_1}}`;
GRANT USE CATALOG, USE SCHEMA, SELECT, EXECUTE, READ VOLUME, MANAGE ON CATALOG ai27_ucsync_testcatalog TO `{{TC_READ_SPN_2}}`;

-- ===================== SCHEMA-LEVEL (read-one / no-other contrast) =====================
-- core_tables: the USE-CATALOG user (idris) CAN read here
GRANT USE SCHEMA, SELECT ON SCHEMA ai27_ucsync_testcatalog.core_tables TO `{{TC_USER_USECAT}}`;
GRANT USE SCHEMA, SELECT ON SCHEMA ai27_ucsync_testcatalog.core_tables TO `{{TC_USER_READ}}`;
GRANT USE SCHEMA, SELECT ON SCHEMA ai27_ucsync_testcatalog.core_tables TO `{{TC_GROUP_ANALYST}}`;
-- governed / advanced / external_store: reader user + groups (NOT idris → idris blocked here)
GRANT USE SCHEMA, SELECT ON SCHEMA ai27_ucsync_testcatalog.governed TO `{{TC_USER_READ}}`;
GRANT USE SCHEMA, SELECT ON SCHEMA ai27_ucsync_testcatalog.governed TO `{{TC_GROUP_READER}}`;
GRANT USE SCHEMA, SELECT ON SCHEMA ai27_ucsync_testcatalog.advanced TO `{{TC_USER_READ}}`;
GRANT USE SCHEMA, SELECT ON SCHEMA ai27_ucsync_testcatalog.external_store TO `{{TC_USER_READ}}`;
-- functions: engineer group executes; APPLY TAG delegated on governed
GRANT USE SCHEMA, EXECUTE ON SCHEMA ai27_ucsync_testcatalog.functions TO `{{TC_GROUP_ENGINEER}}`;
GRANT APPLY TAG ON SCHEMA ai27_ucsync_testcatalog.governed TO `{{TC_GROUP_ENGINEER}}`;
-- restricted: intentionally NO grants → idris/sanket/account-users cannot see it

-- ===================== OBJECT-LEVEL (variety across types) =====================
-- Split read/write on one table
GRANT SELECT ON TABLE ai27_ucsync_testcatalog.core_tables.employees TO `{{TC_USER_READ}}`;
GRANT MODIFY ON TABLE ai27_ucsync_testcatalog.core_tables.employees TO `{{TC_SPN_WRITER}}`;
-- View grant
GRANT SELECT ON VIEW ai27_ucsync_testcatalog.core_tables.emp_summary TO `{{TC_GROUP_ANALYST}}`;
-- Function EXECUTE grant
GRANT EXECUTE ON FUNCTION ai27_ucsync_testcatalog.functions.mask_email TO `{{TC_GROUP_ENGINEER}}`;
-- Volume read/write grants
GRANT READ VOLUME ON VOLUME ai27_ucsync_testcatalog.external_store.docs_managed TO `{{TC_GROUP_READER}}`;
GRANT WRITE VOLUME ON VOLUME ai27_ucsync_testcatalog.external_store.docs_managed TO `{{TC_SPN_WRITER}}`;

-- ===================== OWNERSHIP VARIETY (deferred-transfer coverage) =====================
ALTER TABLE ai27_ucsync_testcatalog.external_store.sales_ext OWNER TO `{{TC_SPN_WRITER}}`;
ALTER VIEW ai27_ucsync_testcatalog.core_tables.emp_summary OWNER TO `{{TC_GROUP_ANALYST}}`;
