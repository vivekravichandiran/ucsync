#!/usr/bin/env bash
# Seed files under URL-hostile folder/file names (spaces, '#', '%', '?', '+', '&', unicode,
# nested spaced dirs) into the managed `special_paths` volume and the external
# `archive_ext` volume of ai27_ucsync_testcatalog.external_store (SOURCE workspace).
# Drives the v1.2.2 Files-API path-encoding fix in volume copy. A name the CLI itself
# cannot write is reported and skipped (the stage still succeeds) so the bed records
# exactly which shapes exist.
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

for V in special_paths archive_ext; do
  put "tax advice body"          "$BASE/$V/Tax advises folder/FY 2025 advice.txt"
  put "hash folder"              "$BASE/$V/q#1 reports/summary #1.txt"
  put "percent folder"           "$BASE/$V/100% done/marker 100%.txt"
  put "question folder"          "$BASE/$V/what? why/notes?.txt"
  put "plus and ampersand"       "$BASE/$V/plus+sign & amp/x+y & z.txt"
  put "unicode folder"           "$BASE/$V/ünïcödé dir/résumé ü.txt"
  put "nested spaced dirs"       "$BASE/$V/a b/c d/e f.txt"
  put "plain sibling (control)"  "$BASE/$V/plain/plain.txt"
done
echo "TESTCAT volume special paths seeded: $ok ok, $bad failed."
exit 0
