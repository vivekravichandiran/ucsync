# UC Sync — Bugs & Features (plain-English tracker)

Every item below has exactly three things:
1. **Bug / Feature** — what it is, in plain English (with enough situation to understand it).
2. **Solution** — what we'll change to fix it.
3. **Test approach** — how we'll prove it's fixed.

Status is in the heading: **FIXED**, **OPEN**, or **BY DESIGN** / **PROPOSED** (features).

**Sections:** Already fixed (F1–F3) · Open bugs (1–12) · By design (13) · Features (FEAT-1–FEAT-5) · Testing & certification plan (end).

---

## Already fixed and shipped

### F1. A pre-existing table could be deleted during a re-run — **FIXED** (commit `30013bc`)
1. **Bug:** The utility has a safety rule — if it *creates* a table and a later governance step (like applying a mask) fails, it deletes that table so it never leaves a half-protected table behind. The problem: on a re-run, it couldn't tell "a table I just created" apart from "a table that already existed from before." So when an *already-existing* table failed a governance step, the utility deleted it — destroying a table (and its data) it should never have touched.
2. **Solution:** Before creating a table, the utility now checks whether it already exists. If it does, it's marked "already existed" and is never eligible for deletion by the safety rule (it's flagged as failed instead).
3. **Test approach:** A test where a pre-existing table fails governance confirms it is flagged, not deleted; and a genuinely new table that fails governance is still deleted. Passing today. (A live incremental re-run to confirm end-to-end is still pending.)

### F2. Changed functions weren't updated on a re-run — **FIXED** (commit `30013bc`)
1. **Bug:** The utility created functions using "create only if it doesn't already exist." So if a masking function changed at the source and you re-ran the migration, the utility skipped it and the target kept the *old* version.
2. **Solution:** Functions are now created with "create or replace," so the latest version is always applied.
3. **Test approach:** A test confirms the generated SQL says `CREATE OR REPLACE FUNCTION`. Passing today.

### F3. Report-only objects were labeled "newly created" on the changes sheet — **FIXED** (commit `729ca55`)
1. **Bug:** Some object types (streaming tables, ML models, materialized views) are intentionally never migrated — only reported. On the "what changed" sheet they were labeled "CREATED_NEW," which was misleading.
2. **Solution:** They're now labeled "REPORT_ONLY" on that sheet.
3. **Test approach:** A test confirms report-only types show "REPORT_ONLY," not "CREATED_NEW." Passing today.

---

## Open bugs

### 1. Remove the "force full" option entirely — **FIXED** (2026-09-12)
> Removed `force_full` from `config.py`, `delta.py`, `package_import.py`, notebooks 00/03, and the three job specs. `DeltaPlan(..., force_full=...)` now raises `TypeError`; a baseline-present run is always incremental (idempotent). Reset path documented as `DROP SCHEMA … CASCADE` in the notebook/config comments (full runbook in the docs pass). Test `test_force_full_option_removed`. 255 green.
1. **Bug/decision:** The utility has a "force full" mode that re-processes every object instead of only the changed ones. It adds little value — a normal re-run is already idempotent (existing tables use `CREATE IF NOT EXISTS`, so their data is untouched). The name also invites the fear that it recreates/drops data. And it caused a spurious failure: on a force-full re-run, re-creating an already-existing **masked** external table is rejected by UC (first create is fine, a second is not) — even though the table is fine. Removing force-full eliminates that failure entirely. Decision: remove it.
2. **Solution:** Remove the `force_full` option from the utility (widget, config, and the run logic). For a genuine reset, the documented way is `DROP SCHEMA <schema> CASCADE` then recreate — **only safe before any data has been loaded into the target** (after that, it destroys real data). This goes in the runbook.
3. **Test approach:** A run has no force-full option; a normal re-run stays idempotent (existing tables/data untouched, no re-create of masked external tables); the runbook documents the DROP SCHEMA CASCADE reset with the "only before any data is loaded into the target" warning.

### 2. Export "permission denied" errors showed up as blank, mysterious failures — **FIXED** (2026-09-12)
> Part 1 (surface the real error, don't retry a permanent one) shipped in `7e63d2f` (executor error-code/message capture) + the `DdlCaptureError` hard-fail path in `export.py`. Part 2 (this change): added `export_read_failures()` and made notebook 02 exit non-zero when any inventoried object could not be read — the report + audit rows are written first, then the stage fails so the import never runs on a partial bundle. Test `test_export_read_failures_flags_unread_objects`.
1. **Bug:** During export, the utility reads each table's definition using a service principal. If that principal lacks permission on a table, the read fails. When this happened, the report showed a blank "statement FAILED" with no reason — so the real cause (a missing permission) was hidden and looked like a random glitch. On top of that, the export still reported overall success, and the import then ran on an *incomplete* set of objects.
2. **Solution:** Show the real error (e.g., "permission denied on table X") and stop retrying it as if it were temporary. And make the export fail loudly if it couldn't read any inventoried object, instead of quietly continuing with a partial set.
3. **Test approach:** A test confirms a permission error is shown with its real message and not retried; and that an object that can't be read causes the export to fail rather than proceed with a partial bundle.

### 3. Lakehouse Monitors aren't listed in the report — **FIXED** (2026-09-12)
> `inventory._iter_quality_monitors` now probes each in-scope table (TABLE/EXTERNAL_TABLE/MV/STREAMING_TABLE) with `GET /api/2.1/unity-catalog/tables/{full}/monitor` (the deprecated quality-monitors get, mirroring `data-quality get-monitor`); a monitored table yields a report-only `MONITOR` object, a not-found is skipped silently, and any real error is noted but never fails inventory. Rendered on the existing "Monitors" report sheet. Test `test_quality_monitor_discovered_per_object_report_only`.
1. **Bug:** A Lakehouse Monitor is a Unity Catalog object that the utility should at least list (it's reported, not migrated). In the run, a live monitor did not appear anywhere in the report — only the tables it produces showed up.
2. **Solution:** Detect monitors **per in-scope object** with the Data Quality Monitors API — `data-quality get-monitor` (object_type `table` or `schema`), which returns the monitor config or a clean not-found. There is **no bulk list** (the API's `list-monitor` is currently *unimplemented*), so the collector iterates in-scope tables/schemas and probes each; report the ones found in their own section. (The deprecated `quality-monitors get` behaves the same.)
3. **Test approach:** **Verified live (2026-09-12):** the per-object `get-monitor`/`quality-monitors get` returns a clear "cannot find monitor" for an unmonitored table (so the probe is deterministic), and `list-monitor` errors as unimplemented (confirming no bulk list). Dev test: a table with a monitor is reported in its own section; unmonitored tables are silently skipped. (Report-only, low priority.)

### 4. There's an option to finish a run without producing a report — **FIXED** (2026-09-12)
> Removed `allow_missing_report` from `config.py`, notebooks 00/03, and the three job specs. Notebook 03 now always raises on a report-write failure (no opt-out). Test `test_report_is_mandatory_no_allow_missing_report_option`.
1. **Bug/cleanup:** A setting currently lets a run complete without generating its report. There's no good reason to allow that — every run should always produce a report.
2. **Solution:** Remove the option; always require the report (a failed report write should fail the run).
3. **Test approach:** A test confirms a run whose report fails to write now fails, and the option is gone from the job configuration.

### 5. Table names that must be quoted (start with a digit, or contain a hyphen) fail — **FIXED** (2026-09-12) — *biggest issue: 66 tables*
> Root cause: `_CREATE_NAME_RE` group 2 matched an all-backticked OR an all-bare multipart name, but not a MIXED one — for `CREATE TABLE cat.schema.\`4g_cust\`` it captured only `cat.schema`, and `_qualify_create_name` then appended the target and left the backticked tail → a 4-part name → "requires a single-part namespace." Fix: the regex now matches each dot-part independently as backtick-or-bare; added a backtick-aware `_split_qualified_name` and hardened `quote_full_name` (no double-quoting, escapes embedded backticks). Tests in `tests/test_name_requalification.py` cover digit-leading, hyphen, fully-backticked, plain, mixed, and view names.
1. **Bug:** When a table name starts with a number (like `4g_cust...`, `5g_...`) or contains a hyphen, SQL requires the name to be wrapped in backticks. The utility rewrites every table's name to point at the new target catalog. Its rewriting logic doesn't handle names where *only part* is quoted (for example, the catalog and schema are plain but the table name is backticked). It grabs only the "catalog.schema" part and mangles the rest, producing a broken name that the database rejects with "requires a single-part namespace." This hit 66 tables — all the ones named `4g_…` / `5g_…` or with a hyphen.
2. **Solution:** Fix the name-rewriting logic so it correctly handles any mix of quoted and unquoted parts in a name.
3. **Test approach:** A test rewrites names that start with a digit, contain a hyphen, are fully backticked, and are plain — every one should produce the correct target name.

### 6. Lakebase-synced tables and Vector Search indexes are treated as normal tables and fail — **FIXED** (2026-09-12)
> `inventory._iter_tables` now classifies `table_type=FOREIGN` by `data_source_format`: `VECTOR_INDEX_FORMAT` → `ObjectType.VECTOR_INDEX`, anything else (incl. `POSTGRESQL_FORMAT`) → new `ObjectType.LAKEBASE_TABLE`, both flagged `in_scope_for_migration=false`. `export._capture_object_ddl` short-circuits a `_REPORT_ONLY_NO_DDL_TYPES` set (no SHOW CREATE, no synthesis, so no `INVALID_DATASOURCE_FORMAT` / unreadable-VS-index failure). Added a "Lakebase Tables" report section (VS already had one) and `LAKEBASE_TABLE` to delta's report-only set. Tests `test_foreign_tables_classified_report_only`, `test_foreign_types_never_capture_ddl`.
1. **Bug:** The catalog contains two kinds of objects that *look* like tables but aren't real migratable tables:
   - **Lakebase-synced tables** — a copy of a Delta table that lives in Postgres, shown in Unity Catalog only for governance visibility.
   - **Vector Search indexes** — search indexes derived from a Delta table.
   Neither can be recreated with ordinary table SQL. The utility doesn't recognize them as special, so it treats them as ordinary tables and tries to create them: the Lakebase ones fail with "INVALID_DATASOURCE_FORMAT," and the Vector Search ones fail even at export because their definition can't be read. (Note: these are **not** in a different workspace — that was a misunderstanding from how they appear in Catalog Explorer. They belong to the source; the reason to skip them is simply their *type*.)
2. **Solution:** Recognize these by `table_type = FOREIGN` plus `data_source_format` (both already collected at inventory): `POSTGRESQL_FORMAT` → Lakebase-synced, `VECTOR_INDEX_FORMAT` → Vector Search index. Give each type its own report section and mark them "skipped — out of scope" instead of trying to create them.
3. **Test approach:** **Verified live (2026-09-12) on real objects:** a Lakebase table returned `table_type=FOREIGN, data_source_format=POSTGRESQL_FORMAT`; a Vector Search index returned `table_type=FOREIGN, data_source_format=VECTOR_INDEX_FORMAT`. Dev test: rows with those formats are sorted into their own types, appear in report-only sections, and are never sent to SHOW CREATE / CREATE.

### 7. The utility strips ALL "delta.*" table settings — this breaks some tables and quietly changes others — **FIXED** (2026-09-12) — *12 failures + hidden fidelity loss*
> `strip_reserved_table_properties` now drops only `_UNREPLAYABLE_TABLE_PROPERTY_KEYS` (the two auto-generated `delta.rowTracking.materialized*` column names + `delta.minReaderVersion`/`minWriterVersion`); every other property — `dataSkippingStatsColumns`, `feature.allowColumnDefaults`, deletion vectors, row tracking, auto-optimize, compression, `databricks.*`, … — is preserved. The two DDL-synthesis paths (`import_engine`, `sql_ddl`) route through the shared `is_replayable_table_property` helper for the same behavior. Updated the collation/property tests and added `test_replayable_delta_properties_survive_bug7` (late-column clustering + column DEFAULTs preserved).
1. **Bug:** When copying a table's definition, the utility cleans up some table properties that can't be replayed. To be safe, it currently removes **every** property that starts with "delta." — which is far too broad. It removes important, replayable settings too:
   - Removing the "data-skipping stats columns" setting breaks tables that cluster on a late column — **7 tables failed** ("clustering column missing stats").
   - Removing the "allow column defaults" feature breaks tables that use column DEFAULT values — **5 tables failed** ("column defaults feature not enabled").
   - And on *every* table that "succeeded," its delta settings (deletion vectors, row tracking, auto-optimize, compression, etc.) were silently dropped — so the tables exist but don't match the source's actual configuration.
2. **Solution:** Only remove the handful of settings that genuinely can't be replayed (the auto-generated row-tracking column names, and the minimum reader/writer protocol versions). Keep everything else.
3. **Test approach:** A test confirms the important settings are preserved and only the truly un-replayable ones are removed; and that tables using late-column clustering and column defaults now create successfully.

### 8. Pipeline event-log tables fail — they shouldn't be migrated at all — **FIXED** (2026-09-12)
> `inventory._iter_tables` reads the top-level `pipeline_id` (already fetched from the tables API) and reclassifies a plain pipeline-managed TABLE/EXTERNAL_TABLE to the new report-only `ObjectType.PIPELINE_TABLE` (`in_scope_for_migration=false`); pipeline-output MVs/streaming tables stay report-only via their own types. Export skips it (added to `_REPORT_ONLY_NO_DDL_TYPES` + a general `in_scope_for_migration is False` guard), delta labels it report-only, and it gets a "Pipeline Tables" report section. Test `test_pipeline_managed_table_classified_report_only`.
1. **Bug:** Data pipelines (DLT / SDP / Kafka ingestion) create "event log" tables to record their own activity. These belong to the pipeline, not to governance migration. The utility tries to migrate them and they fail in two ways:
   - One table carries internal "owned by a pipeline" markers, so Unity Catalog demands a managing pipeline and rejects it ("No pipeline was present").
   - 17 tables live in dedicated event-log schemas (`dlt_event_logs`, `kafka_event_logs`) that don't exist in the target, so they fail with "schema not found."
2. **Solution:** At inventory, treat any table whose Unity Catalog metadata has a non-null **`pipeline_id`** as pipeline-managed and skip it (report-only). This is the deterministic signal — verified live: a pipeline event-log table is otherwise an ordinary `MANAGED`/`DELTA` table with no distinguishing properties, but the tables API (`GET /api/2.1/unity-catalog/tables/{full_name}`, which inventory already calls) returns a top-level `pipeline_id` that is populated for event-log tables and for pipeline outputs, and empty for normal tables/views. No new API call and no fragile property/column-name matching needed. The pipeline recreates its own event logs on the target; a one-time historical data copy can be done separately if needed.
3. **Test approach:** A unit test confirms a table with a non-null `pipeline_id` is classified report-only and never sent to SHOW CREATE / CREATE, while a plain table (`pipeline_id` empty) is migrated normally. (Verified live 2026-09-12: created a real pipeline; its event-log table and its MV both returned `pipeline_id` set, a plain table and a plain view returned `pipeline_id` = null.)

### 9. Tables with a GEOMETRY column fail on older compute — **OPEN** (configuration, not code)
1. **Bug:** Two tables have a GEOMETRY column. Spatial data types are only supported on a recent runtime (DBR / serverless 17.1+). The import ran on older compute, so those two tables failed with "unsupported data type."
2. **Solution:** Run the import on newer compute — a serverless SQL warehouse, a Pro warehouse on the "Current" channel, or a job cluster on DBR 17.1+ (e.g. 17.3 LTS). This is a settings change, not a code change. (Do not use the Preview channel — it isn't needed and isn't meant for production.)
3. **Test approach:** **Verified live (2026-09-12):** on a serverless SQL warehouse, `CREATE TABLE ... (shape GEOMETRY(4326))` succeeded and `SELECT st_astext(to_geography('{"type":"Point","coordinates":[3,4]}'))` returned `POINT(3 4)`. So current/serverless compute handles spatial; the fix is purely to run the import on it.

### 10. A temporary error was retried 5 times and its real reason hidden — **FIXED** (2026-09-12) (the one view failure)
> The statement-FAILED path already surfaced code+message and failed fast (`7e63d2f`). This change fixes the **submit/poll-time HTTP** path in `RestSqlExecutor`: it now parses the HTTP status and, via `_submit_error_is_retryable`, retries only the transient set `{403,408,429,500,502,503,504}` (with the existing exponential backoff+jitter) and fails FAST on any other explicit status (400/401/404/…) with the real status+body — no more "statement failed after N attempts" masking a permanent error. Tests `test_permanent_http_submit_error_fails_fast`, `test_transient_403_submit_error_is_retried`.

### 9. Tables with a GEOMETRY column fail on older compute — **ADDRESSED** (compute config; see FEAT-1)
> Not a code change — the import must run on current/serverless compute (verified live 2026-09-12). FEAT-1 (single serverless SQL warehouse for all replay) makes this the default execution path, so GEOMETRY works without version fiddling. Nothing to unit-test in the utility; validated at deploy/run time.
1. **Bug:** One view failed to import with an HTTP 403 "Invalid request" error. We reproduced the exact same call afterward (same service principal, same warehouse, same SQL) and it **succeeded** — so this was a one-time, temporary rejection by the Databricks control plane, not a real problem with the view, permissions, or SQL. **Re-running the import fixes it.** The real bug worth fixing is how the utility *reported* it: it treats every error at submit time — even a permanent one — as temporary, retries it 5 times, then reports "statement failed after 5 attempts" while hiding the actual HTTP status and message. That's what made it hard to diagnose.
2. **Solution:** Show the real HTTP status and message; fail immediately on clearly-permanent errors (400 / 401 / 404); only retry genuinely temporary ones (403 / 429 / 503), with a longer wait so a brief blip is ridden out.
3. **Test approach:** A test confirms a 400/401/404 fails immediately with the real message, and a 403/429/503 is retried with backoff.

### 11. Inline "--" comments could silently break a statement — **FIXED** (2026-09-12) (was latent)
> Rewrote `_split_statements` as a character-level, comment/quote/dollar-aware scanner: a `;` inside a string literal, quoted identifier, `--` line comment, `/* */` block comment, or `$$` block is no longer a boundary, and comments are preserved (the engine understands them natively) instead of the old line-drop. Tests `test_split_statements_preserves_comments_bug11`, `test_split_statements_inline_semicolon_in_string_not_split` (existing `$$`-block test still green).
1. **Bug:** A SQL "--" comment runs to the end of the line. The utility's statement splitter only removes comment lines that *start* with "--", not "--" comments in the middle of a line. Today the view definitions come back on multiple lines and work fine. But if a statement ever arrived on a single line (flattened), or ended with an inline "--" comment, the splitter would silently mangle it.
2. **Solution:** Stop deleting the user's comments. Feed each complete statement to the SQL engine as-is — the engine understands `--` comments natively, so a captured view/function keeps its author's comments intact. The utility's only real job is to separate multiple statements in a file; do that with a comment-and-quote-aware splitter (one that knows a `;` inside a string literal or a `--`/`/* */` comment is not a statement boundary), instead of the current trick of dropping comment lines.
3. **Test approach:** **Verified live (2026-09-12):** created a view with inline and full-line `--` comments; `SHOW CREATE` returned the definition with those comments preserved (the engine keeps them; only a comment at the very end was trimmed). This confirms feeding the whole statement to the engine preserves the author's comments — the current line-drop is what loses them. Dev test: a `;` inside a string/comment does not split a statement, and a view's comments survive migration.

### 12. Skipped objects are labeled "SUCCESS," which inflates the numbers — **FIXED via FEAT-5** (2026-09-12)
> Report-only objects now read "SKIPPED (no target object)" (never "SUCCESS (REPORT_ONLY)") and are counted in a separate skipped bucket, outside success, in the Summary outcome roll-up. See FEAT-5. Test `test_report_only_reads_skipped_and_counted_outside_success`.
1. **Bug:** Some objects are intentionally not migrated, only reported — for example, 257 streaming tables. In the report their status reads "SUCCESS (REPORT_ONLY)." The word "SUCCESS" makes it look like they were migrated, when in fact nothing was applied. They're also counted in the headline "1049 successful" number, which inflates it — only 783 objects were actually created; the other 266 were skipped or already existed.
2. **Solution:** Label these "SKIPPED (REPORT_ONLY)" and count them in a separate "skipped" total, not in "success." Things that were genuinely applied (like tags) stay as success.
3. **Test approach:** A test confirms a report-only object shows "SKIPPED" and adds to the skipped count, not the success count.

---

## Bugs found during live testing (Run 1, 2026-09-13)

### 16. FEAT-4 incremental control table didn't persist (ran on job-cluster Spark, not the warehouse) — **FIXED** (2026-09-13)
1. **Bug:** Found in live Run 3/4 — the volume copy worked (files + content verified on target), but the incremental control table `uc_sync_volume_files` never persisted, so a re-run re-copied every file. Root cause: it was written via `SparkVolumeCopyControl(spark, …)` — the job-cluster Spark session — which silently failed on the classic USER_ISOLATION import cluster (a one-off diagnostic proved the same code works on serverless, and the MERGE/DDL work on the warehouse). This contradicted FEAT-1 (all SQL should run on the import warehouse).
2. **Solution:** New `WarehouseVolumeCopyControl` persists via the import **warehouse** executor (`RestSqlExecutor`) with plain `CREATE IF NOT EXISTS` + a batched `MERGE ... USING (VALUES …)` (no Spark DataFrame API). Notebook 03 now builds the control on `warehouse_executor`. In-memory remains the last-resort fallback.
3. **Test approach:** `test_warehouse_control_persists_and_skips_across_instances` (291 green). Live re-validation on the follow-up run.

### 15. FEAT-4 import stage couldn't authenticate to source in a plaintext-secret env — **FIXED** (2026-09-13)
1. **Bug:** Found while wiring FEAT-4 live — notebook 03 read only `source_secret_scope`/`source_secret_key` for source auth, but this env authenticates with a plaintext `source_client_secret` (no secret scope), so the volume-copy stage had no way to reach the source. Notebook 02 already supports both; notebook 03 didn't.
2. **Solution:** Mirror notebook 02 in notebook 03 — add the `source_client_secret` widget + `from_sources` entry (and `${source_client_secret}` on the e2e import task), so the import stage supports both plaintext-secret and secret-scope source auth.
3. **Test approach:** 290 unit tests green; FEAT-4 live copy validated on the follow-up run.

### 14. View names not re-qualified when a comment header precedes CREATE — **FIXED** (2026-09-13)
1. **Bug:** Found in live Run 1 — all 7 views failed with `[SCHEMA_NOT_FOUND] catalog_ws_oesdot.analytics` (the warehouse's default catalog). Root cause: the bug #11 comment-preserving splitter now leaves the utility's `-- VIEW … / -- source= …` header lines at the START of each statement. `_CREATE_NAME_RE` and `_normalize_create_statement` were anchored on `^\s*CREATE`, so they stopped matching and the 2-part view name (SHOW CREATE VIEW emits `schema.view`, no catalog) was never re-qualified to 3-part → it resolved against the warehouse default catalog. Tables were unaffected (SHOW CREATE TABLE emits an absolute 3-part name). Exposed by FEAT-1 (all views now run on the single warehouse).
2. **Solution:** Both the CREATE-name matcher and the normalizer now skip a leading run of `--` / `/* */` comment lines (via `_LEADING_COMMENTS`) while preserving them, so the view name is re-qualified and OR REPLACE is injected as before.
3. **Test approach:** `test_qualify_2part_view_name_behind_comment_header`, `test_normalize_or_replace_behind_comment_header` (290 green). Live re-validation on the next run.

## By design (not a bug — noted so it isn't mistaken for one)

### 13. External volumes need a storage-path mapping file
1. **Situation:** External volumes and tables point at cloud storage (ADLS) paths. To recreate them in the target, the utility needs a file that maps each source storage path to its target path. This run intentionally did not provide that file, so external volumes were skipped — 11 of them show "manual action required." This is expected behavior, not a failure.
2. **Solution:** Nothing to fix. Just make sure the report clearly explains that a storage-mapping file is required, so these aren't mistaken for failures.
3. **Test approach:** The report shows external volumes as "manual action required" with a clear explanation, and does not count them as failures.

---

## Features

### FEAT-1. Run everything through one SQL warehouse (drop the dual-compute split) — **DONE** (2026-09-12)
> Notebook 03 now builds one `RestSqlExecutor` on `import_warehouse_id` and passes it as BOTH the main and the ABAC/view executor, so every replay statement (tables, functions, masks, row filters, views, MVs, ABAC policies, tags, grants) runs on the single serverless warehouse — nothing on the job cluster's Spark session. `import_warehouse_id` is now required for the import stage. Inventory reads unchanged. This makes bug #9 (GEOMETRY) work by default and removes the second auth path behind #10. Test `test_feat1_single_warehouse_runs_ddl_and_abac_on_one_executor`.
1. **Feature:** Today the import runs most DDL on a classic Spark job cluster but sends ABAC policies and views to a separate SQL warehouse — because a classic cluster can't do those. This split causes inconsistent behavior (e.g. GEOMETRY works on one path, not the other; two auth paths, which is what produced the one-off view-403). Standardize: **run all DDL and governance through a single serverless SQL warehouse; nothing runs on the job cluster's Spark session.**
2. **Approach:** point every replay statement (tables, functions, masks, row filters, views, materialized views, ABAC policies, tags, grants) at one serverless SQL warehouse. Benefits: one always-current runtime (fixes GEOMETRY, item #9, with no version fiddling), one auth path (removes the class behind item #10), and consistent, explainable behavior. Inventory reads stay as-is; only the replay path unifies.
3. **Test approach:** **Verified live on a serverless warehouse (2026-09-12):** CREATE TABLE, CREATE FUNCTION (mask), ALTER TABLE SET MASK, **CREATE VIEW over a masked table**, row-filter function + SET ROW FILTER, and CREATE MATERIALIZED VIEW all succeeded; ABAC `CREATE POLICY` was accepted and compiled (failed only on a semantic tag-value check, not on being unsupported) — i.e. the warehouse supports every statement type the job cluster couldn't. Dev test: run a full import with the warehouse as the sole executor and confirm zero "unsupported on this compute" failures.

### FEAT-2. Create governed tags before applying them — **DONE** (2026-09-12)
> Inventory reads the source account's tag-policies (`read_governed_tag_policies` → `GET /api/2.1/tag-policies`) and emits a `GOVERNED_TAG` object (with allowed values) for each governed key actually assigned on an in-scope object. Export writes `governed_tags/*.sql` (`CREATE GOVERNED TAG \`key\` VALUES (…)`). Import adds **Phase 0** (`_create_governed_tags`, gated by `apply_tags`) that runs before any SET TAGS and is idempotent — an already-existing tag (same-account target) is `SKIP_EXISTING`, not a failure. Tests in `tests/test_governed_tags_feat2.py` cover the DDL builder, policy read, inventory capture (only used keys), and Phase-0 create + skip-existing.
1. **Feature:** The utility applies governed tags to columns/securables but assumes the governed-tag definition already exists on the target (today it treats a missing tag policy as a prerequisite failure owned outside the utility). It should instead **create the governed tag (and its allowed values) first, then apply it.** So: whatever governed tags it finds on source columns, create the tag policy on the target if missing, then assign it.
2. **Approach:** At inventory, additionally (a) mark which tag assignments are *governed* (their key is a registered tag policy) vs free-form, and (b) capture each governed tag's **allowed-value list**. Add an early **Phase 0 — create governed tags** that runs before any `SET TAGS`: for each governed tag key, `SHOW GOVERNED TAGS` to check existence and create only if missing (`CREATE GOVERNED TAG <key> VALUES ('v1','v2')`) — idempotent, because same-account targets already have the tag. Then the existing tag phases assign values.
3. **Test approach:** **Verified live (2026-09-12):** `CREATE GOVERNED TAG name VALUES (...)` succeeds as a **workspace admin** (no account admin needed); the tag created in `source_ws` was immediately visible and usable in `target_ws` — a **different metastore in the same account, with no import/binding step** (governed tags are account-wide); an allowed value assigned fine and a disallowed value was rejected (`UC_TAG_POLICY_VALUE_NOT_ALLOWED`), confirming allowed values must be captured and replicated. Dev test: a governed tag on a source column is created on the target (if absent), skipped (if present), and then applied.

### FEAT-3. Governed schema-evolution — replay new source columns + their governance — **DONE** (2026-09-12)
> New import Phase 1b (`_replay_schema_evolution`, incremental-only): for each `CHANGED` pre-existing table it diffs the source columns (bundle) against the live target columns (`DESCRIBE TABLE`), `ALTER TABLE … ADD COLUMN` each missing column with the exact source `type_text` (+comment), then applies that column's classic mask (and a row filter that references a new column) idempotently. Governed tags on the new column are applied by the tag phase (its fingerprint changed, and Phase 0 already created the tag); a same-tag new column is auto-covered by the existing ABAC policy (no new policy). Drops/renames/type-changes are out of scope. Tests in `tests/test_schema_evolution_feat3.py` (add-with-exact-type+mask, no-op when present, full-run never evolves).
1. **Feature:** After the initial migration, a source table can gain a new column (schema evolution). The utility must replay that new column and its governance to the target so the target stays in sync. Two cases: (a) new column with a **column mask / row filter** → on target, `ALTER TABLE ADD COLUMN` + create the mask/filter function + apply it; (b) new column with a **governed tag + ABAC policy** → on target, `ALTER TABLE ADD COLUMN` + apply the tag (create the governed tag first if new, per FEAT-2; the **same tag needs no new policy** — the existing ABAC policy matches by tag and auto-covers the new column).
2. **Approach:** on an incremental run, detect columns present on source but not on target; `ALTER TABLE ADD COLUMN <c> <exact source type>`; then apply that column's governance (mask/filter function, or tag — creating the governed tag first if new). Scope is column *additions* only — drops / renames / type changes are out (flag them).
3. **Test approach:** **Verified live (2026-09-12):** adding a new column tagged with an existing governed tag is **auto-covered by the schema's existing ABAC policy** (no new policy needed). Dev test: a source table gains a new column with a mask/filter or a governed tag+policy; on the incremental run the utility adds the column to the target with the exact source type and applies its governance; a same-tag new column reuses the existing policy.

### FEAT-4. Copy volume data (files) source → target — **DONE** (2026-09-12)
> New `volume_copy.py` (`VolumeDataCopier`) copies files for managed + external volumes via the Files API (recursive walk → download from source, upload to target), with: a `copy_volume_data` toggle (default off, widgets on 00/03 + e2e/import job specs); incremental via an injected control store (`InMemoryVolumeCopyControl` for tests, `SparkVolumeCopyControl` Delta table `uc_sync_volume_files` for cross-run); a >5 GB file reported `SKIPPED_TOO_LARGE` (not dropped); per-file failure recorded, run continues; throttling via the client's REST retry/backoff. Added `WorkspaceClient.list_directory/download_file/upload_file`. Notebook 03 runs it best-effort after import (needs source auth in direct mode; skips cleanly in airgap). Tests in `tests/test_volume_copy_feat4.py` (recursive tree, incremental skip/recopy, >5 GB reported, per-file failure isolation).
1. **Feature:** The utility creates volume *securables* (managed + external) but not their *files*. Add an optional step to copy the actual files from source volumes to the corresponding target volumes, for **both managed and external** volumes, cross-region. (Re-pointing isn't an option — this is a cross-region migration, so the target gets a new external location and the bytes must move.)
2. **Approach:** a **toggle widget** (default off). Enumerate source volume files (Files API list) and copy each to the target volume via the **Files API** (read from source, write to target through the running compute), recreating the directory tree. **Incremental:** a control table (volume + path + last-copied mtime) so only **new or modified** files are copied on a re-run. **Throttling:** reuse the existing REST retry + backoff. **>5 GB files exceed the Files API limit → the file fails and is recorded in the report** (accepted scope, not a bug). Runs from the **job's classic compute**, which holds the cross-workspace API reach under front-end private link. *(Perf optimization, not v1: for external volumes, cluster-side `dbutils.fs.cp` copies ADLS↔ADLS at the data plane — no 5 GB cap, higher throughput — via target-metastore external locations over both ADLS accounts; the Files API is the uniform baseline.)*
3. **Test approach:** **Verified live (2026-09-12):** created managed *and* external volumes on both `source_ws` and `target_ws` (different metastores/regions, same account), put a file in each source volume, and copied it cross-workspace via the Files API (download from source, upload to target) — **content matched on the target for both volume types, identical mechanic**. So the API route is safe and uniform for managed and external; the only differences are throughput and the 5 GB cap. Dev test: recursive directory copy; incremental run skips unchanged files and copies new/modified ones; a >5 GB file is *reported* (not silently dropped); throttling is retried with backoff.

### FEAT-5. Match the workspace-migration (wsmig) report format — **DONE** (2026-09-12) (absorbs #12)
> `report.py` now carries wsmig's `_STATUS_STYLE` label+colour map and `_SUMMARY_ORDER` (failures first), a `_wsmig_status_key` mapper, and the wsmig palette/fonts (navy `1E3A5F` header band, slate `334155` section, DB-red `FF3621`, alt-row `F1F5F9`, Calibri). The Summary is an **Outcome roll-up** — a title bar with workspace URL + generated timestamp, per-status counts with failures first, colour-coded status cells, a TOTAL row, an applied-vs-skipped honest split, and a Manual-steps section. Report-only rows read "Skipped (no target object)", counted outside success (#12). Tests in `tests/test_report_wsmig_feat5.py`. **Remaining polish (not blocking):** the HTML surface (`reporting.py`) still uses raw statuses, and a golden-file diff against an actual wsmig sample workbook was not run (no sample loaded) — the xlsx operator report carries the vocabulary/layout/palette.
1. **Feature:** Make our reports use the **same look and vocabulary as the workspace-migration utility** (`wsmig`), which itself matches the customer's own inventory script — so operators see a familiar report. This is presentation only; the migration engine is untouched. It also **supersedes item #12** — wsmig has no misleading "SUCCESS (REPORT_ONLY)"; it uses a status vocabulary where report-only reads as a *Skipped* variant.
2. **Approach (all in `report.py` + `reporting.py`, no engine changes):** (a) adopt wsmig's **status label + colour map** and map our internal statuses onto it — `created`→"Created" (green), `created_with_warning`→"Created (warning)" (amber), `updated`→"Updated" (blue), `SKIP_EXISTING`→"Adopted (pre-existing)" (cyan), `skipped`→"Skipped (unchanged)" (gray), report-only→"Skipped (no target object)" (violet), filter-excluded→"Deferred (not selected)" (slate), `manual`→"Manual step" (amber), `deleted_in_source`→"Deleted in source" (rose), `failed`→"FAILED" (red); (b) match the **Summary layout** — an "Outcome roll-up" by status, **failures listed first**, a manual-steps section, a Deleted-in-source cell, a TOTAL row, and a title bar with workspace URL + generated timestamp; (c) swap the **palette/fonts** to wsmig's (navy `1E3A5F`, slate `334155`, Databricks-red `FF3621`, alt-row `F1F5F9`, Calibri) and colour-code status cells; (d) align per-unit **columns** to Import Status / Action Taken / Target Id / Note; (e) keep **structural parity** across inventory/export/import (one sheet per asset type) and the "Migration Plan" checklist + dry-run filename convention.
3. **Test approach:** Reference verified by reading the wsmig code (`src/exporters/excel_generator.py`, `src/reports/import_report.py`) — status map (`_STATUS_STYLE`), summary/roll-up layout, palette, and one-sheet-per-asset-type structure are all captured above. Dev test: a golden-file comparison of our generated workbook (sheet names, header order, status labels + fill colours, summary roll-up) against a wsmig sample; assert no row reads "SUCCESS (REPORT_ONLY)" and report-only rows read as a Skipped variant counted outside success.

---

## Testing & certification plan

This section defines how an independent QA tester certifies the utility is production-ready.
Every fixed item (F1–F3), open bug (1–12), by-design behavior (13), and feature (FEAT-1–4) maps
to a concrete test case below, run against the real `source_ws` / `target_ws` environment.

### Tester role (system prompt)

> You are an expert QA tester who is supposed to test and certify this utility is prod ready and
> good to go to the customer. You have to run the utility exactly as a human would do. Create the
> git folder, run the `notebooks/00_Install_Jobs.py` notebook by filling relevant widget values,
> this will create the job which you have to run. Monitor the run and at the end of the run, you
> have to verify the output on the target side by doing actual API calls and comparing it with the
> reports created. Ensure every test case is passing. If there are any bugs found, you need to add
> it to the `plans/bugfix-and-prod-readiness.md` plan. You also have to create a validation report
> which shows every bug or feature is validated. You can then seed the source side for incremental
> stuff to be tested and run the job on the target again and do the same validation process. Since
> you have to ensure it's prod ready, you will have to plan run 1 which has all baseline features
> for testing and then run 2 before which you'll seed incremental stuff on source. At the end every
> existing and new feature developed + bugfixes done need to pass. Only thing you are allowed to do
> directly is — any setup required before the runs start, and seedings on source before the runs
> start. At any point if you are stuck you are not allowed to take any decision, you always have to
> stop and ask. You are expected to test in direct mode and expected to test the mode after schema
> and catalog are created, everything inside needs to be replicated and do add external tables and
> volumes to the list.

### Environment
- **Source:** `source_ws` (eastus, metastore `d1ffebbb…`), catalogs under `ai27_uc_*`; serverless warehouse `75e259b9a920fb7b`.
- **Target:** `target_ws` (eastus2, metastore `fc10db6f…`, same account `ccb842e7…`); serverless warehouse `84bef830cd844b1d`; external locations `ai27val_*_el`.
- **Modes to certify (both):** (a) **direct mode**; (b) **existing-catalog / BYO mode** — catalog + schemas pre-created, everything *inside* replicated. Both must include **external tables and external volumes**.

### Ground rules (from the role)
- Run exactly as a human: create the git folder → run `00_Install_Jobs.py` with widget values → run the created job → monitor → verify the target with **real API calls** and compare against the generated reports.
- **Only** direct actions allowed: pre-run **setup** and **source-side seeding** before each run. Everything else observed, not changed.
- If blocked or a result is ambiguous, **stop and ask** — never decide alone.
- Log any bug found into this plan; produce a **validation report** asserting each item validated.
- **Two runs:** Run 1 (baseline) then Run 2 (after seeding incremental changes on source).

### Source seed fixture — build before Run 1 (one object per scenario)
| # | Seed on source | Exercises |
|---|---|---|
| S1 | Managed table (plain) | baseline create (structure + governance) |
| S2 | External table on an external location | external-table replicate |
| S3 | Table with column `DEFAULT` + `delta.feature.allowColumnDefaults` | #7 |
| S4 | Table `CLUSTER BY` a late column + `delta.dataSkippingStatsColumns` | #7 |
| S5 | Table named `4g_test` (digit-leading → backticked) | #5 |
| S6 | Table with a `GEOMETRY(4326)` column | #9, FEAT-1 |
| S7 | Table + classic column mask (function) | governance replicate |
| S8 | Table + row filter | governance replicate |
| S9 | Table + governed tag + tag-driven ABAC column-mask policy | FEAT-2, FEAT-3 |
| S10 | View with inline `--` comments; and a view over a masked table | #11, FEAT-1 |
| S11 | Function (mask UDF) | F2 |
| S12 | Streaming table; materialized view; registered model | #12 (report-only labels) |
| S13 | Lakehouse Monitor on a table | #3 |
| S14 | Pipeline event-log table (has `pipeline_id`) | #8 (skip) |
| S15 | Lakebase-synced FOREIGN table (`POSTGRESQL_FORMAT`) | #6 (skip) |
| S16 | Vector Search index (`VECTOR_INDEX_FORMAT`) | #6 (skip) |
| S17 | Managed volume with files; external volume with files | FEAT-4 |
| S18 | Grants + governed tags + free-form tags across the above | grants/tags replicate |

### Run 1 — baseline: expected result + how to validate (per item)
Fill `00_Install_Jobs` widgets (direct mode; scope the seeded catalog/schemas; set `source_warehouse_id`, `import_warehouse_id`, `run_as_spn`, `external_locations_path`, ops catalog/schema/volume). Run the source Inventory+Export job, then the target Import job. Then validate on target via API and compare to the report:

| Item | Expected in report | Target API validation |
|---|---|---|
| S1/S2, F-baseline | `CREATED` | `tables get` exists; target schema/columns match source |
| #5 (S5) | `4g_test` `CREATED` (not `REQUIRES_SINGLE_PART_NAMESPACE`) | `tables get 4g_test` exists |
| #7 (S3,S4) | `CREATED`; delta props preserved | `SHOW TBLPROPERTIES` shows `dataSkippingStatsColumns` / `allowColumnDefaults` retained |
| #9/FEAT-1 (S6) | `CREATED` on serverless warehouse | `tables get`; spatial query works |
| governance (S7,S8,S9) | mask/filter/ABAC `SUCCESS` | mask/filter defined on the column; `column_tags` present; ABAC policy present and masks on read for a non-privileged user |
| FEAT-2 (S9) | governed tag created then applied | `SHOW GOVERNED TAGS`; `column_tags` = expected value |
| #11 (S10) | view `CREATED` with comments | `SHOW CREATE` shows comments preserved |
| F2 (S11) | function `CREATED` (`OR REPLACE`) | `functions get`; body matches source |
| #12 (S12) | streaming table / model → **`SKIPPED (REPORT_ONLY)`**, counted as skipped not success | not present on target (report-only); summary skipped-count correct |
| #3 (S13) | monitor listed in its own section | `data-quality get-monitor` on the source table returns a monitor |
| #8 (S14) | pipeline event-log **`SKIPPED`** (not attempted) | not created on target; report shows skip reason |
| #6 (S15,S16) | FOREIGN/VS in their own report-only sections, **`SKIPPED (out of scope)`** | not created; no SHOW CREATE attempted |
| #13 (S2 external vol w/o mapping) | `MANUAL_ACTION_REQUIRED` with mapping-file hint (not a failure) | target external volume absent until mapping supplied |
| FEAT-4 (S17) | volume files copied (toggle on) | `fs ls` on target volume matches source; content matches; >5 GB file **reported** |
| #10 | any submit 4xx surfaced with real status/body; no "after 5 attempts" mystery | inspect run logs / report error text |

Also verify **BYO mode**: pre-create catalog+schemas on target, run with create toggles off, confirm everything *inside* (tables, external tables, functions, views, governance, external volumes) replicates and the report shows schemas/catalog as `SKIPPED (pre-existing)`.

### Incremental seed — build before Run 2 (on source)
| # | Seed on source | Exercises |
|---|---|---|
| I1 | Add a new column + governed tag to an existing table (same tag) | FEAT-3 (no new policy) |
| I2 | Add a new column + classic mask to an existing table | FEAT-3 |
| I3 | Change a function body | F2 |
| I4 | Add new files + modify an existing file in a volume | FEAT-4 incremental |
| I5 | Add a brand-new table (+ governance) | incremental create |
| I6 | Pre-existing target table + a newly-failing governance step | F1 (must NOT drop) |
| I7 | Add a new governed tag / new allowed value | FEAT-2 incremental |

### Run 2 — incremental: expected result + validation
Re-run the target Import job (incremental auto-detected from `uc_sync_state`). Validate:

| Item | Expected | Target API validation |
|---|---|---|
| FEAT-3 (I1,I2) | new column added on target + its tag/mask applied | `columns` list has the new column (exact type); `column_tags`/mask present on it |
| F2 (I3) | function updated | body on target = new source body |
| FEAT-4 (I4) | only new/modified files re-copied (control table) | `fs ls` + mtimes; unchanged files not recopied |
| I5 | new table created + governed | `tables get`; governance present |
| **F1 (I6)** | pre-existing table **flagged FAILED, NOT dropped** | `tables get` still exists on target; report shows `GOVERNANCE_FAILED` not a drop |
| FEAT-2 (I7) | new tag/value created then applied | `SHOW GOVERNED TAGS`; assignment succeeds |

### Edge cases checklist
- No `force_full` widget exists anymore (#1); a plain re-run is idempotent (no data loss, no re-create of masked external tables).
- Report terminology: report-only rows read `SKIPPED (REPORT_ONLY)`, not `SUCCESS`; summary skipped vs success counts are honest (#12).
- View comments survive migration (#11); `delta.*` tuning preserved on every created table (#7).
- A `>5 GB` volume file is reported, not silently dropped; throttling is retried with backoff (FEAT-4).
- A transient submit 4xx is surfaced with its real HTTP status + body (#10).
- Governed tags resolve cross-metastore with no import step (FEAT-2); a new same-tag column is auto-covered by the existing ABAC policy (FEAT-3).
- Both **direct** and **BYO** modes certified; external tables and external volumes included in both.

### Exit criteria (certification)
Every row in the Run 1 and Run 2 tables passes; the validation report lists each F/#/FEAT item with its API-verified result; no open Sev-1/Sev-2; any new bug is filed here. Only then is the utility certified prod-ready (implementation + documentation being the remaining non-test work).
