# API Mapping

Workspace clients call Azure Databricks REST 2.x. Prefer SDK in package code; keep raw paths for pagination/throttling control.

## Discovery

| Purpose | Method | Path |
|---------|--------|------|
| Who am I | GET | `/api/2.0/preview/scim/v2/Me` |
| Metastore assignment | GET | `/api/2.1/unity-catalog/current-metastore-assignment` |
| List metastores | GET | `/api/2.1/unity-catalog/metastores` | account admin only (403 for workspace user) |

## Hierarchy

| Object | List | Get | Create | Update | Delete |
|--------|------|-----|--------|--------|--------|
| Catalog | `GET /api/2.1/unity-catalog/catalogs` | `GET .../catalogs/{name}` | `POST .../catalogs` | `PATCH .../catalogs/{name}` | `DELETE .../catalogs/{name}` |
| Schema | `GET .../schemas?catalog_name=` | `GET .../schemas/{full_name}` | `POST .../schemas` | `PATCH ...` | `DELETE ...` |
| Table/View | `GET .../tables?catalog_name=&schema_name=` | `GET .../tables/{full_name}` | `POST .../tables` | `PATCH ...` | `DELETE ...` |
| Volume | `GET .../volumes?...` | `GET .../volumes/{full_name}` | `POST .../volumes` | `PATCH ...` | `DELETE ...` |
| Function | `GET .../functions?...` | `GET .../functions/{full_name}` | `POST .../functions` | `PATCH ...` | `DELETE ...` |

## Security & governance

| Purpose | Path |
|---------|------|
| Grants | `GET/PATCH /api/2.1/unity-catalog/permissions/{securable_type}/{full_name}` |
| Effective grants | `GET /api/2.1/unity-catalog/effective-permissions/{securable_type}/{full_name}` |
| Bindings | `GET/UPDATE /api/2.1/unity-catalog/bindings/catalog/{name}` |

## Storage / federation / sharing

| Object | Base path |
|--------|-----------|
| Storage credentials | `/api/2.1/unity-catalog/storage-credentials` |
| Credentials (unified) | `/api/2.1/unity-catalog/credentials` |
| External locations | `/api/2.1/unity-catalog/external-locations` |
| Connections | `/api/2.1/unity-catalog/connections` |
| Shares / recipients / providers | `/api/2.1/unity-catalog/shares` etc. |

## SQL assist

| Purpose | Mechanism |
|---------|-----------|
| `SHOW CREATE TABLE` | SQL Statement Execution API or notebook Spark SQL |
| Tags / masks deeper | `information_schema` + UC tag assignment APIs |
| MV refresh config | `SHOW CREATE` / Lakeflow pipeline descriptors where applicable |

## Client engineering rules

1. **Pagination:** always follow `next_page_token` even when page has zero objects (observed on target catalogs).
2. **Throttling:** configurable `max_api_workers`; exponential backoff on 429.
3. **Timestamps:** persist REST `updated_at` epoch ms → timestamp; `source_last_modified_source=REST_API`.
4. **Idempotency:** SHA-256 of canonical JSON definition (exclude volatile fields like etag).
5. **Secrets:** never log `Authorization` or secret scope values.

## Auth wiring

```
secret_scope[client_id] + secret_scope[client_secret]
  -> OAuth client credentials (Azure Databricks)
  -> WorkspaceClient(host=..., client_id=..., client_secret=...)
```

Dev fallback: CLI profile / PAT from scope key `token` (not widgets).
