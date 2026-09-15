#!/usr/bin/env bash
# Seed nested-subdir files into the managed (docs_managed) and external (archive_ext)
# volumes of ai27_ucsync_testcatalog.external_store on the SOURCE workspace, so the
# migration's volume-file inventory + (external-volume) copy path has real hierarchy.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
P="$SRC_PROFILE"
BASE="dbfs:/Volumes/$TESTCAT_CATALOG/external_store"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

put () {  # $1 local-content  $2 dbfs-dest
  local f="$TMP/$(basename "$2")"
  printf '%s\n' "$1" > "$f"
  echo ">> $2"
  databricks fs mkdir "$(dirname "$2")" -p "$P" >/dev/null 2>&1 || true
  databricks fs cp "$f" "$2" -p "$P" --overwrite >/dev/null
}

# Managed volume — nested date-partitioned docs
put "q1 2024 report body"     "$BASE/docs_managed/2024/q1/report.txt"
put "q2 2024 report body"     "$BASE/docs_managed/2024/q2/report.txt"
put "2025 annual summary"     "$BASE/docs_managed/2025/reports/summary.txt"
put "top-level readme"        "$BASE/docs_managed/README.txt"

# External volume — nested logs/archive
put "app log line 1"          "$BASE/archive_ext/2025/logs/app.log"
put "archived record"         "$BASE/archive_ext/archive/old_record.txt"
put "external volume manifest" "$BASE/archive_ext/manifest.txt"

echo "TESTCAT volume files seeded."
