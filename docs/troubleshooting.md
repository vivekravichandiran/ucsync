# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Metastore storage root URL does not exist` on catalog create | Target metastore has no default storage; catalog needs `MANAGED LOCATION` | Provide storage + mapping file (§ manual-actions §2–3), or pre-create the catalog and set `create_catalogs=false`. |
| `NO_SUCH_STORAGE_CREDENTIAL_EXCEPTION` on external location | Source-named credential doesn't exist on target | Provide `target_credential` in the mapping, or if the target external location already covers the path set `create_storage_credentials=false` + `create_external_locations=false`. |
| `GOVERNANCE_PREREQ_MISSING` on tags/ABAC | Governed-tag **definition** or a referenced mask/filter **function** missing on target | Create the account-level tag definition (§ manual-actions §4); ensure functions imported (`create_functions=true`). |
| `ROW_COLUMN_ACCESS_POLICIES_NOT_SUPPORTED_ON_ASSIGNED_CLUSTERS` | Masks/row filters on a single-user cluster | Use Standard (USER_ISOLATION) or serverless compute. |
| `COLUMN_MASKS_FEATURE_NOT_SUPPORTED.CHECK_CONSTRAINT` at source read | UC rejects masks on CHECK-constraint tables | Source data model issue; not fixable by migration — remove the constraint or the mask at source. |
| `SCHEMA_NOT_FOUND` on table create (only in a stateless SQL harness) | `USE CATALOG/SCHEMA` didn't persist across calls | Not an issue in notebooks (`spark.sql` is session-stateful); the utility qualifies names fully. |
| `PARSE_SYNTAX_ERROR` replaying captured DDL | Collation / delta.* property / inline policy in `SHOW CREATE` | Handled by the replay sanitizers; if it recurs, capture the DDL and extend `rewrite.py`. |
| Import shows `MANUAL_ACTION_REQUIRED` for a storage credential | Secrets are never exported | Recreate the credential by hand (§ manual-actions §5). |
| A view is `PENDING` | Referenced object not present yet | Re-run Import (incremental) after the dependency exists. |
