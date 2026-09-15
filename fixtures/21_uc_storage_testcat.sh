#!/usr/bin/env bash
# UC storage credentials + external locations for ai27_ucsync_testcatalog, BOTH sides.
#   SOURCE (source_ws): cred + EL rooted at the source account `data` container.
#   TARGET (target_ws): cred + EL rooted at the target account — a BYO PREREQUISITE.
#     The testcatalog migration runs with create_storage_credentials/external_locations
#     = false, so these must exist before import.
#
# Idempotent: create, else update.
#   bash fixtures/21_uc_storage_testcat.sh          # source + target
#   bash fixtures/21_uc_storage_testcat.sh source   # source only
#   bash fixtures/21_uc_storage_testcat.sh target   # target only
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
WHICH="${1:-both}"

# $1 profile  $2 cred-name  $3 connector-resource-id
make_cred () {
  echo ">> [$1] storage credential $2 -> $3"
  databricks storage-credentials create -p "$1" --json "$(cat <<JSON
{"name":"$2","azure_managed_identity":{"access_connector_id":"$3"},"comment":"ai27_ucsync testcatalog credential"}
JSON
)" >/dev/null 2>&1 || \
  databricks storage-credentials update "$2" -p "$1" --json \
    "{\"azure_managed_identity\":{\"access_connector_id\":\"$3\"}}" >/dev/null
}

# $1 profile  $2 el-name  $3 cred-name  $4 account
make_el () {
  local url="abfss://$CONTAINER@$4.dfs.core.windows.net/"
  echo ">> [$1] external location $2 -> $url"
  databricks external-locations create "$2" "$url" "$3" -p "$1" >/dev/null 2>&1 || \
  databricks external-locations update "$2" -p "$1" --json \
    "{\"url\":\"$url\",\"credential_name\":\"$3\"}" >/dev/null
}

storage_source () {
  local cid="/subscriptions/$SUB/resourceGroups/$RG_SRC/providers/Microsoft.Databricks/accessConnectors/$TESTCAT_SRC_CONNECTOR"
  echo "=== TESTCAT SOURCE UC storage ($SRC_PROFILE) ==="
  make_cred "$SRC_PROFILE" "$TESTCAT_SRC_CRED" "$cid"
  make_el   "$SRC_PROFILE" "$TESTCAT_SRC_EL" "$TESTCAT_SRC_CRED" "$TESTCAT_SRC_ACCOUNT"
}

storage_target () {
  local cid="/subscriptions/$SUB/resourceGroups/$RG_TGT/providers/Microsoft.Databricks/accessConnectors/$TESTCAT_TGT_CONNECTOR"
  echo "=== TESTCAT TARGET UC storage — BYO prereq ($TGT_PROFILE) ==="
  make_cred "$TGT_PROFILE" "$TESTCAT_TGT_CRED" "$cid"
  make_el   "$TGT_PROFILE" "$TESTCAT_TGT_EL" "$TESTCAT_TGT_CRED" "$TESTCAT_TGT_ACCOUNT"
}

case "$WHICH" in
  source) storage_source ;;
  target) storage_target ;;
  both)   storage_source; storage_target ;;
  *) echo "usage: $0 [source|target|both]"; exit 1 ;;
esac
echo "TESTCAT UC storage credentials + external locations ready."
