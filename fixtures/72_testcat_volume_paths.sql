-- v1.2.2 field bug: volume copy aborted on a folder name with a space ("URL can't contain
-- control characters"). A dedicated MANAGED volume whose files sit under folder/file names
-- with spaces and URL-special / non-ASCII characters. Files are seeded by
-- 72_testcat_volume_paths.sh (also adds the same shapes inside the EXTERNAL archive_ext).
-- With the fix every file copies to /Volumes/<mapped catalog>/external_store/<volume>/...
CREATE VOLUME IF NOT EXISTS ai27_ucsync_testcatalog.external_store.special_paths
  COMMENT 'v1.2.2: folder/file names with spaces, #, %, ?, +, & and unicode';
