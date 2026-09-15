#!/usr/bin/env bash
# Provision the dedicated Azure storage stack for ai27_ucsync_testcatalog: one ADLS
# Gen2 (HNS) account + `data` container + Databricks access connector (system MI) on
# each side, with Storage Blob Data Contributor granted to each connector MI on its
# own account. Source = eastus (RG_SRC); target = eastus2 (RG_TGT).
#
# Unlike the legacy 3-catalog bundle, BOTH sides get real UC creds/ELs later
# (21_uc_storage_testcat.sh) because the testcatalog migration runs in BYO mode.
#
# Idempotent: `az ... create` is safe to re-run.
#   bash fixtures/11_provision_azure_testcat.sh          # source + target
#   bash fixtures/11_provision_azure_testcat.sh source   # source only
#   bash fixtures/11_provision_azure_testcat.sh target   # target only
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
WHICH="${1:-both}"

make_account () {  # $1 rg  $2 region  $3 account
  echo ">> storage account $3 ($2, $1)"
  az storage account create -n "$3" -g "$1" -l "$2" --subscription "$SUB" \
    --sku Standard_LRS --kind StorageV2 --hns true \
    --tags "owner=$OWNER_TAG" -o none
  echo "   container '$CONTAINER'"
  az storage container create -n "$CONTAINER" --account-name "$3" \
    --auth-mode login -o none
}

make_connector () {  # $1 rg  $2 region  $3 connector  $4 account
  echo ">> access connector $3 ($2, $1)"
  az databricks access-connector create -n "$3" -g "$1" -l "$2" \
    --subscription "$SUB" --identity-type SystemAssigned \
    --tags "owner=$OWNER_TAG" -o none
  local pid
  pid=$(az databricks access-connector show -n "$3" -g "$1" --subscription "$SUB" \
        --query 'identity.principalId' -o tsv)
  echo "   MI principalId=$pid -> Storage Blob Data Contributor on $4"
  az role assignment create --assignee-object-id "$pid" \
    --assignee-principal-type ServicePrincipal \
    --role "Storage Blob Data Contributor" \
    --scope "/subscriptions/$SUB/resourceGroups/$1/providers/Microsoft.Storage/storageAccounts/$4" \
    -o none
}

provision_source () {
  echo "=== TESTCAT SOURCE Azure ($RG_SRC / $REGION_SRC) ==="
  az group create -n "$RG_SRC" -l "$REGION_SRC" --subscription "$SUB" \
    --tags "owner=$OWNER_TAG" -o none
  make_account   "$RG_SRC" "$REGION_SRC" "$TESTCAT_SRC_ACCOUNT"
  make_connector "$RG_SRC" "$REGION_SRC" "$TESTCAT_SRC_CONNECTOR" "$TESTCAT_SRC_ACCOUNT"
}

provision_target () {
  echo "=== TESTCAT TARGET Azure ($RG_TGT / $REGION_TGT) ==="
  az group create -n "$RG_TGT" -l "$REGION_TGT" --subscription "$SUB" \
    --tags "owner=$OWNER_TAG" -o none
  make_account   "$RG_TGT" "$REGION_TGT" "$TESTCAT_TGT_ACCOUNT"
  make_connector "$RG_TGT" "$REGION_TGT" "$TESTCAT_TGT_CONNECTOR" "$TESTCAT_TGT_ACCOUNT"
}

case "$WHICH" in
  source) provision_source ;;
  target) provision_target ;;
  both)   provision_source; provision_target ;;
  *) echo "usage: $0 [source|target|both]"; exit 1 ;;
esac
echo "TESTCAT Azure provisioning complete."
