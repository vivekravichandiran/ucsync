#!/usr/bin/env bash
# Create the UC storage credentials + external locations for the SOURCE fixtures.
# One credential per catalog (bound to that catalog's access connector) and one
# external location per catalog rooted at the account's `data` container.
#
# Target-side credentials/ELs are intentionally NOT created here — the migration
# creates them from the mapping CSV (see mappings/ai27_target_mapping.csv). Only
# the target Azure infra (10_provision_azure.sh) is a prerequisite for E2E.
#
#   bash fixtures/20_uc_storage.sh
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
P="$SRC_PROFILE"
CID="/subscriptions/$SUB/resourceGroups/$RG_SRC/providers/Microsoft.Databricks/accessConnectors"

make_cred () {  # $1 cred-name  $2 connector-name
  echo ">> storage credential $1 -> $2"
  databricks storage-credentials create -p "$P" --json "$(cat <<JSON
{"name":"$1","azure_managed_identity":{"access_connector_id":"$CID/$2"},"comment":"ai27 fixture credential"}
JSON
)" >/dev/null 2>&1 || \
  databricks storage-credentials update "$1" -p "$P" --json \
    "{\"azure_managed_identity\":{\"access_connector_id\":\"$CID/$2\"}}" >/dev/null
}

make_el () {  # $1 el-name  $2 cred-name  $3 account
  local url="abfss://$CONTAINER@$3.dfs.core.windows.net/"
  echo ">> external location $1 -> $url"
  databricks external-locations create "$1" "$url" "$2" -p "$P" >/dev/null 2>&1 || \
  databricks external-locations update "$1" -p "$P" --json \
    "{\"url\":\"$url\",\"credential_name\":\"$2\"}" >/dev/null
}

make_cred ai27_uc_gov_src_cred "$GOV_CONNECTOR"
make_cred ai27_uc_finance_cred "$FIN_CONNECTOR"
make_cred ai27_uc_sales_cred   "$SALES_CONNECTOR"

make_el ai27_uc_gov_src_el ai27_uc_gov_src_cred "$GOV_ACCOUNT"
make_el ai27_uc_finance_el ai27_uc_finance_cred "$FIN_ACCOUNT"
make_el ai27_uc_sales_el   ai27_uc_sales_cred   "$SALES_ACCOUNT"

echo "UC storage credentials + external locations ready."
