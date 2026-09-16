#!/usr/bin/env bash
# Provision the Azure storage layer the UC fixtures sit on: per-catalog ADLS Gen2
# storage accounts (HNS) + a single `data` container each + a Databricks access
# connector (system-assigned MI) per catalog, with Storage Blob Data Contributor
# granted to each connector MI on its own account.
#
# Idempotent: `az ... create` is safe to re-run. Source side is always provisioned;
# target side runs too (Azure infra for E2E), but the target UC credentials/ELs are
# created by the migration itself, not here.
#
#   bash fixtures/10_provision_azure.sh          # source + target Azure infra
#   bash fixtures/10_provision_azure.sh source   # source only
#   bash fixtures/10_provision_azure.sh target   # target only
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
  echo "=== SOURCE Azure ($RG_SRC / $REGION_SRC) ==="
  az group create -n "$RG_SRC" -l "$REGION_SRC" --subscription "$SUB" \
    --tags "owner=$OWNER_TAG" -o none
  make_account   "$RG_SRC" "$REGION_SRC" "$GOV_ACCOUNT"
  make_account   "$RG_SRC" "$REGION_SRC" "$FIN_ACCOUNT"
  make_account   "$RG_SRC" "$REGION_SRC" "$SALES_ACCOUNT"
  make_connector "$RG_SRC" "$REGION_SRC" "$GOV_CONNECTOR"   "$GOV_ACCOUNT"
  make_connector "$RG_SRC" "$REGION_SRC" "$FIN_CONNECTOR"   "$FIN_ACCOUNT"
  make_connector "$RG_SRC" "$REGION_SRC" "$SALES_CONNECTOR" "$SALES_ACCOUNT"
}

provision_target () {
  echo "=== TARGET Azure ($RG_TGT / $REGION_TGT) ==="
  az group create -n "$RG_TGT" -l "$REGION_TGT" --subscription "$SUB" \
    --tags "owner=$OWNER_TAG" -o none
  make_account   "$RG_TGT" "$REGION_TGT" "$GOV_ACCOUNT_TGT"
  make_account   "$RG_TGT" "$REGION_TGT" "$FIN_ACCOUNT_TGT"
  make_account   "$RG_TGT" "$REGION_TGT" "$SALES_ACCOUNT_TGT"
  make_connector "$RG_TGT" "$REGION_TGT" "$GOV_CONNECTOR_TGT"   "$GOV_ACCOUNT_TGT"
  make_connector "$RG_TGT" "$REGION_TGT" "$FIN_CONNECTOR_TGT"   "$FIN_ACCOUNT_TGT"
  make_connector "$RG_TGT" "$REGION_TGT" "$SALES_CONNECTOR_TGT" "$SALES_ACCOUNT_TGT"
}

case "$WHICH" in
  source) provision_source ;;
  target) provision_target ;;
  both)   provision_source; provision_target ;;
  *) echo "usage: $0 [source|target|both]"; exit 1 ;;
esac
echo "Azure provisioning complete."
