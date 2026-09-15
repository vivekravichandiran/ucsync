# Manual actions (outside the utility)

Everything the utility cannot do itself. Each step: **who / where / how / verify.**

## 1. Target metastore
- **Who:** account admin. **Where:** Databricks account console (or account API).
- **How:** create/assign a metastore in the target region and assign it to the
  target workspace. If it has no default storage root, every catalog needs a
  `MANAGED LOCATION` — provide storage via steps 2–3.
- **Verify:** `databricks metastores summary -p <target>`.

## 2. ADLS container + access connector (target region)
- **Who:** Azure admin (Contributor on the subscription/RG).
- **Where:** Azure portal or `az` CLI.
- **How:**
  ```bash
  az group create -n <rg> -l <region> --tags owner=<you>
  az storage account create -n <sa> -g <rg> -l <region> --sku Standard_LRS \
    --kind StorageV2 --hns true --tags owner=<you>
  az storage container create --account-name <sa> -n uc-root --auth-mode login
  az databricks access-connector create -g <rg> -n <connector> -l <region> \
    --identity-type SystemAssigned
  # grant the connector's managed identity access to the storage account:
  az role assignment create --assignee-object-id <connector-principalId> \
    --assignee-principal-type ServicePrincipal \
    --role "Storage Blob Data Contributor" \
    --scope <storage-account-resource-id>
  ```
- **Verify:** the role assignment lists the connector principal; propagation takes ~1 min.

## 3. UC storage credential + external location (target)
- **Who:** metastore admin. **Where:** target workspace (UC).
- **How** (or let the utility create them from the mapping file with `create_*=true`):
  ```
  POST /api/2.1/unity-catalog/storage-credentials
    {"name":"cred-target","azure_managed_identity":{"access_connector_id":"<connector id>"}}
  POST /api/2.1/unity-catalog/external-locations
    {"name":"el-target","url":"abfss://uc-root@<sa>.dfs.core.windows.net/","credential_name":"cred-target"}
  ```
- **Verify:** create a throwaway catalog `MANAGED LOCATION 'abfss://uc-root@<sa>…/probe'`
  and a table; drop it.

## 4. Governed tag definitions (account level)
- **Who:** account admin (or the workspace-migration utility). **Where:** Tag Policy API.
- **How:** `POST /api/2.1/tag-policies` with
  `{"tag_key":"<key>","description":"…","values":[{"name":"…"}]}`. Account-scoped,
  so a definition created once is visible in every region's metastore.
- **Why:** `SET TAGS` and `CREATE POLICY` **hard-fail** on an undefined governed-tag
  key/value. If Import reports `GOVERNANCE_PREREQ_MISSING`, the definition (or a
  referenced mask/filter function) is missing on the target.
- **Verify:** `GET /api/2.1/tag-policies` lists the key + allowed values.

## 5. Storage-credential secrets (non-MI credentials)
- Secrets are **never exported**. For non–managed-identity credentials, recreate the
  credential by hand on the target; the utility emits `MANUAL_ACTION_REQUIRED` with the
  DDL retained in the bundle for review.

## 6. Connections / Delta shares / recipients / providers
- **Inventory-only** — they carry remote secrets/endpoints. Recreate by hand on the
  target and re-point consumers. The report flags them `MANUAL`.

## 7. (Data-migration prerequisite, not this utility)
- If a **classic-masked** table must be Deep-Cloned by the data-migration utility,
  convert it to an **ABAC** policy on the **source** first (classic masks have no
  `EXCEPT`, so they always block a clone; ABAC exempts the cloning principal). This
  utility replicates classic masks as-found and does not convert them.
