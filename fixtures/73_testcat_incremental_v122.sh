#!/usr/bin/env bash
# Volume-file half of the v1.2.2 incremental seed (see 73_testcat_incremental_v122.sql).
#  #12 overwrite an existing file (new mtime)      → re-copied on the next run
#  #13 new file under a NEW folder with spaces     → copied on the next run
set -uo pipefail
cd "$(dirname "$0")"
source ./config.env
P="$SRC_PROFILE"
BASE="dbfs:/Volumes/$TESTCAT_CATALOG/external_store"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
ok=0; bad=0

put () {  # $1 content  $2 dbfs-dest
  local f="$TMP/payload_$RANDOM.txt"
  printf '%s\n' "$1" > "$f"
  databricks fs mkdir "$(dirname "$2")" -p "$P" >/dev/null 2>&1 || true
  if databricks fs cp "$f" "$2" -p "$P" --overwrite >/dev/null 2>"$TMP/err"; then
    echo "  [ SUCCEEDED] $2"; ok=$((ok+1))
  else
    echo "  [    FAILED] $2 :: $(head -c 200 "$TMP/err")"; bad=$((bad+1))
  fi
}

put "README v2 - overwritten by the v1.2.2 incremental seed (#12)" "$BASE/docs_managed/README.txt"
put "run 2 new file (#13)" "$BASE/special_paths/Run 2 drop/new file 2.txt"
echo "TESTCAT incremental v1.2.2 files seeded: $ok ok, $bad failed."
[ "$bad" -eq 0 ]
