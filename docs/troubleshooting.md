# Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| 401/403 on source UC | Bad token/SP or missing USE privileges | Check secret scope + grants |
| Catalog create `INVALID_STATE` Default Storage | Missing `storage_root` | Apply `managed_storage` mapping |
| Empty catalog pages then more results | Sparse pagination | Follow `next_page_token` until absent |
| Export path missing | Volume not created | Preflight volume WRITE |
| External table import fails | Source ADLS URL | Map external location / leave MANUAL |
| Grants fail on principal | Group not in target account | Principal mapping |
| Bindings point at wrong WS | Copied source workspace_id | Workspace mapping |
| Warehouse SQL timeouts | Warehouse STOPPED | Auto-start or use REST-only path |
| Job SUCCESS but objects missing | dry_run true | Re-run with dry_run false after COMPARE |
| MCP tools unavailable | Cursor Databricks MCP error | Use REST/SDK from notebook; fix MCP separately |
| `TABLE_OR_VIEW_NOT_FOUND` on audit table | `audit_table` widget points at an unprovisioned catalog/schema | Use `classic_stable_target_vk.uc_sync_ops.uc_sync_audit`; the table itself is auto-created |
| `NOT_FOUND.UC_AZURE_CREDENTIAL_NOT_FOUND` writing audit/ops objects | Ops catalog's managed-storage access connector deleted or recreated (seen on `ril_migration`) | Point ops widgets at a catalog with working managed storage, or have an account admin reconfigure the connector |
| Code changes not reflected after deploy | Interactive notebook session reused cached `uc_sync` modules | `UC_Sync_Main` now evicts them on run; otherwise Detach & re-attach or Clear state |
| Widget still shows an old default after a code update | Databricks keeps existing widget values | Widget menu → Remove all widgets, then re-run |
