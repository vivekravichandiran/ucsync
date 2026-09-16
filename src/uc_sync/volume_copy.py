"""Volume data copy — source → target UC Volume files (FEAT-4).

Creates volume *securables* is the import engine's job; this module copies the
actual *files* for both managed and external volumes, cross-region, through the
running compute via the Files API (download from source, upload to target). It is a
toggle (default off).

* **Incremental:** an injected control store keyed by (volume, path) records each
  file's last-copied source mtime, so a re-run copies only new / modified files.
* **Size cap:** the Files API rejects a file larger than 5 GB; such a file is
  recorded FAILED (SKIPPED_TOO_LARGE) in the report, never silently dropped.
* **Throttling:** the WorkspaceClient's REST retry + backoff rides out 429/5xx.

The control store is an interface (``needs_copy`` / ``record``) so the notebook can
back it with a Delta control table while tests use an in-memory dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Optional, Protocol

# The Files API per-file limit. A file at or under this is copied via the API; a
# larger one is reported (accepted scope, not a bug).
FILES_API_MAX_BYTES = 5 * 1024 * 1024 * 1024

COPIED = "COPIED"
SKIPPED_UNCHANGED = "SKIPPED_UNCHANGED"
SKIPPED_TOO_LARGE = "SKIPPED_TOO_LARGE"
FAILED = "FAILED"

# Columns added to the control table beyond the original (volume, path, source_mtime):
# status/message + a created_at/updated_at split (C1). Appended in place on a
# pre-existing table so an older control table (which had only copied_at) gains them.
_VOLUME_CONTROL_UPGRADE_COLUMNS = [
    ("status", "STRING"),
    ("message", "STRING"),
    ("created_at", "TIMESTAMP"),
    ("updated_at", "TIMESTAMP"),
]


def _upgrade_volume_control(exec_fn: Any, table: str) -> None:
    """Add the C1 columns to a pre-existing control table (idempotent). Each ADD COLUMN
    is attempted independently; an 'already exists' error is swallowed so a table that
    already has the column is fine. ``exec_fn`` runs one SQL statement (spark.sql or an
    executor's execute)."""
    for name, sql_type in _VOLUME_CONTROL_UPGRADE_COLUMNS:
        try:
            exec_fn(f"ALTER TABLE {table} ADD COLUMNS ({name} {sql_type})")
        except Exception:  # noqa: BLE001 - column already present (or unsupported) → skip
            pass


class VolumeCopyControl(Protocol):
    """Records the per-file outcome for incremental copies. ``needs_copy`` returns True
    when the file is new or modified. ``record`` persists the outcome (``status`` /
    ``message``); only a COPIED outcome advances the incremental mtime watermark, so a
    FAILED / too-large file is retried on the next run rather than silently skipped."""

    def needs_copy(self, volume: str, path: str, source_mtime: int) -> bool: ...
    def record(
        self, volume: str, path: str, source_mtime: int,
        status: str = COPIED, message: str = "",
    ) -> None: ...


class InMemoryVolumeCopyControl:
    """A dict-backed control store (tests, and a default when no Delta table is set)."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], int] = {}

    def needs_copy(self, volume: str, path: str, source_mtime: int) -> bool:
        prior = self._seen.get((volume, path))
        return prior is None or prior != source_mtime

    def record(
        self, volume: str, path: str, source_mtime: int,
        status: str = COPIED, message: str = "",
    ) -> None:
        # Only a successful copy advances the incremental watermark (so a failure /
        # too-large file is retried next run). status/message are not persisted in-memory.
        if status == COPIED:
            self._seen[(volume, path)] = source_mtime


class SparkVolumeCopyControl:
    """A Delta-table-backed control store for cross-run incremental copies. Loads the
    prior (volume, path) → mtime map once, records in memory during the run, and
    ``flush()`` upserts the touched rows at the end (one MERGE, not per file)."""

    def __init__(self, spark: Any, table: str) -> None:
        self.spark = spark
        self.table = table
        spark.sql(
            f"CREATE TABLE IF NOT EXISTS {table} (volume STRING, path STRING, "
            "source_mtime BIGINT, status STRING, message STRING, "
            "created_at TIMESTAMP, updated_at TIMESTAMP) USING DELTA"
        )
        _upgrade_volume_control(lambda sql: spark.sql(sql), table)
        # (volume, path) -> (mtime, status, message) recorded this run; only COPIED
        # advances the watermark used by needs_copy.
        self._seen: dict[tuple[str, str], int] = {}
        self._dirty: dict[tuple[str, str], tuple[int, str, str]] = {}
        try:
            for r in spark.sql(
                f"SELECT volume, path, source_mtime, status FROM {table}"
            ).collect():
                # Only a prior COPIED sets the incremental watermark — a FAILED / skipped
                # row must not suppress a retry.
                if str(r["status"] or COPIED) == COPIED:
                    self._seen[(r["volume"], r["path"])] = int(r["source_mtime"] or 0)
        except Exception:  # noqa: BLE001 - empty/new table → no prior state
            pass

    def needs_copy(self, volume: str, path: str, source_mtime: int) -> bool:
        return self._seen.get((volume, path)) != source_mtime

    def record(
        self, volume: str, path: str, source_mtime: int,
        status: str = COPIED, message: str = "",
    ) -> None:
        if status == COPIED:
            self._seen[(volume, path)] = source_mtime
        # A non-COPIED outcome must not persist the new mtime (else needs_copy would
        # skip the retry) — store 0 so the next run re-evaluates the file.
        self._dirty[(volume, path)] = (
            source_mtime if status == COPIED else 0, status, message[:500],
        )

    def flush(self) -> None:
        """Upsert every recorded (volume, path) outcome into the control table.
        created_at is set once (on insert); updated_at is refreshed every merge."""
        if not self._dirty:
            return
        rows = [
            (vol, path, mtime, status, message)
            for (vol, path), (mtime, status, message) in self._dirty.items()
        ]
        df = self.spark.createDataFrame(
            rows, ["volume", "path", "source_mtime", "status", "message"]
        )
        df.createOrReplaceTempView("_uc_sync_volume_updates")
        self.spark.sql(
            f"MERGE INTO {self.table} t USING ("
            "  SELECT volume, path, source_mtime, status, message, "
            "         current_timestamp() AS now FROM _uc_sync_volume_updates) s "
            "ON t.volume = s.volume AND t.path = s.path "
            "WHEN MATCHED THEN UPDATE SET t.source_mtime = s.source_mtime, "
            "  t.status = s.status, t.message = s.message, t.updated_at = s.now "
            "WHEN NOT MATCHED THEN INSERT (volume, path, source_mtime, status, message, "
            "  created_at, updated_at) "
            "  VALUES (s.volume, s.path, s.source_mtime, s.status, s.message, s.now, s.now)"
        )
        self._dirty.clear()


class WarehouseVolumeCopyControl:
    """Cross-run control store persisted through the import **SQL warehouse**
    executor (FEAT-1: all replay/persistence SQL runs on the one serverless
    warehouse, never the job-cluster Spark session). Uses plain SELECT + a batched
    MERGE — no Spark DataFrame API — so it is reliable on the same compute as the
    rest of the import. ``executor`` is any object with ``execute(sql) -> rows``
    (e.g. ``RestSqlExecutor``)."""

    def __init__(self, executor: Any, table: str) -> None:
        self.executor = executor
        self.table = table
        executor.execute(
            f"CREATE TABLE IF NOT EXISTS {table} (volume STRING, path STRING, "
            "source_mtime BIGINT, status STRING, message STRING, "
            "created_at TIMESTAMP, updated_at TIMESTAMP) USING DELTA"
        )
        _upgrade_volume_control(lambda sql: executor.execute(sql), table)
        self._seen: dict[tuple[str, str], int] = {}
        self._dirty: dict[tuple[str, str], tuple[int, str, str]] = {}
        try:
            for row in executor.execute(
                f"SELECT volume, path, source_mtime, status FROM {table}"
            ) or []:
                if str((row[3] if len(row) > 3 else COPIED) or COPIED) == COPIED:
                    self._seen[(str(row[0]), str(row[1]))] = int(row[2] or 0)
        except Exception:  # noqa: BLE001 - empty/new table → no prior state
            pass

    def needs_copy(self, volume: str, path: str, source_mtime: int) -> bool:
        return self._seen.get((volume, path)) != source_mtime

    def record(
        self, volume: str, path: str, source_mtime: int,
        status: str = COPIED, message: str = "",
    ) -> None:
        if status == COPIED:
            self._seen[(volume, path)] = source_mtime
        self._dirty[(volume, path)] = (
            source_mtime if status == COPIED else 0, status, message[:500],
        )

    @staticmethod
    def _lit(text: str) -> str:
        return "'" + str(text).replace("'", "''") + "'"

    def flush(self) -> None:
        """Upsert the recorded (volume, path) outcomes via one MERGE per chunk.
        created_at is set once (on insert); updated_at is refreshed every merge."""
        if not self._dirty:
            return
        items = list(self._dirty.items())
        for start in range(0, len(items), 200):
            chunk = items[start:start + 200]
            values = ", ".join(
                f"({self._lit(vol)}, {self._lit(path)}, CAST({int(mtime)} AS BIGINT), "
                f"{self._lit(status)}, {self._lit(message)})"
                for (vol, path), (mtime, status, message) in chunk
            )
            self.executor.execute(
                f"MERGE INTO {self.table} t "
                f"USING (SELECT * FROM VALUES {values} "
                "AS s(volume, path, source_mtime, status, message)) s "
                "ON t.volume = s.volume AND t.path = s.path "
                "WHEN MATCHED THEN UPDATE SET t.source_mtime = s.source_mtime, "
                "  t.status = s.status, t.message = s.message, "
                "  t.updated_at = current_timestamp() "
                "WHEN NOT MATCHED THEN INSERT (volume, path, source_mtime, status, "
                "  message, created_at, updated_at) "
                "  VALUES (s.volume, s.path, s.source_mtime, s.status, s.message, "
                "  current_timestamp(), current_timestamp())"
            )
        self._dirty.clear()


@dataclass
class VolumeCopyResult:
    volume: str
    source_path: str
    target_path: str
    status: str
    bytes_copied: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "volume": self.volume,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "status": self.status,
            "bytes_copied": self.bytes_copied,
            "message": self.message,
        }


@dataclass
class VolumeDataCopier:
    """Copies files from a source volume subtree to the matching target subtree."""

    source_client: Any
    target_client: Any
    control: VolumeCopyControl = field(default_factory=InMemoryVolumeCopyControl)
    max_bytes: int = FILES_API_MAX_BYTES

    def copy_volume(
        self, volume: str, source_root: str, target_root: str
    ) -> list[VolumeCopyResult]:
        """Recursively copy every file under ``source_root`` to the same relative
        path under ``target_root``. ``volume`` labels the control-store rows and the
        report. Directory structure is recreated implicitly by the file paths."""
        source_root = source_root.rstrip("/")
        target_root = target_root.rstrip("/")
        results: list[VolumeCopyResult] = []
        for entry in self._walk(source_root):
            rel = entry["path"][len(source_root):].lstrip("/")
            target_path = f"{target_root}/{rel}" if rel else target_root
            results.append(self._copy_one(volume, entry, target_path))
        return results

    def _walk(self, directory: str) -> Iterator[dict[str, Any]]:
        """Depth-first yield of FILE entries (not directories) under ``directory``."""
        for item in self.source_client.list_directory(directory):
            if item.get("is_directory"):
                yield from self._walk(str(item.get("path") or "").rstrip("/"))
            else:
                yield item

    def _copy_one(
        self, volume: str, entry: dict[str, Any], target_path: str
    ) -> VolumeCopyResult:
        source_path = str(entry.get("path") or "")
        size = int(entry.get("file_size") or 0)
        mtime = int(entry.get("last_modified") or entry.get("modification_time") or 0)
        if size > self.max_bytes:
            msg = f"file is {size} bytes > Files API 5 GB limit — copy it out of band"
            # Record the too-large outcome so the control table shows WHY it wasn't
            # copied (C1); mtime not advanced, so it is re-evaluated next run.
            self.control.record(volume, source_path, mtime, SKIPPED_TOO_LARGE, msg)
            return VolumeCopyResult(
                volume, source_path, target_path, SKIPPED_TOO_LARGE, 0, msg,
            )
        if not self.control.needs_copy(volume, source_path, mtime):
            # Already copied (watermark matches) — leave its prior COPIED control row.
            return VolumeCopyResult(
                volume, source_path, target_path, SKIPPED_UNCHANGED, 0,
                "unchanged since last copy",
            )
        try:
            data = self.source_client.download_file(source_path)
            self.target_client.upload_file(target_path, data, overwrite=True)
        except Exception as exc:  # noqa: BLE001 - one file's failure never aborts the rest
            msg = str(exc)[:300]
            # Record the failure (C1: "know if something failed"); mtime NOT advanced,
            # so the file is retried on the next run rather than silently skipped.
            self.control.record(volume, source_path, mtime, FAILED, msg)
            return VolumeCopyResult(
                volume, source_path, target_path, FAILED, 0, msg,
            )
        self.control.record(volume, source_path, mtime, COPIED)
        return VolumeCopyResult(volume, source_path, target_path, COPIED, len(data))


def copy_summary(results: list[VolumeCopyResult]) -> dict[str, int]:
    """Roll up results by status for the report / run log."""
    summary: dict[str, int] = {}
    for r in results:
        summary[r.status] = summary.get(r.status, 0) + 1
    return summary
