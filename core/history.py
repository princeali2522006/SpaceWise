"""Transactional persistence for completed SpaceWise scan snapshots."""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from threading import Event

from core.scanner import (
    CategorySummary,
    DriveUsage,
    ScannedFile,
    ScanResult,
    SubfolderSummary,
)


class HistoryLoadCancelled(Exception):
    """Raised when a saved scan load is cancelled before reconstruction completes."""


@dataclass(frozen=True)
class ScanHistorySummary:
    scan_id: int
    started_at: str
    completed_at: str
    saved_at: str
    root_path: str
    scan_scope: str
    scan_roots: tuple[str, ...]
    requested_roots: tuple[str, ...]
    file_count: int
    total_size: int
    elapsed_seconds: float
    inaccessible_items: int
    inaccessible_directories: int
    skipped_directories: int
    hard_link_paths: int
    skipped_files: int
    subfolder_count: int
    drive_usage: DriveUsage | None
    load_error: str | None = None


@dataclass(frozen=True)
class ScanHistoryStats:
    scan_count: int
    database_bytes: int


def default_history_path() -> str:
    """Return a database location outside the folders normally scanned by SpaceWise."""
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        if not root:
            root = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        root = os.environ.get("XDG_STATE_HOME")
        if not root:
            root = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(root, "SpaceWise", "scan_history.sqlite3")


def _file_dict(file: ScannedFile) -> dict[str, str | int]:
    return {
        "name": file.name,
        "path": file.path,
        "size": file.size,
        "category": file.category,
    }


def _file_from_dict(data: dict[str, object]) -> ScannedFile:
    return ScannedFile(
        name=str(data["name"]),
        path=str(data["path"]),
        size=int(data["size"]),
        category=str(data["category"]),
    )


class ScanHistoryStore:
    """Stores only completed scan metadata in an app-owned SQLite database."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = os.fspath(path) if path is not None else default_history_path()

    def _connect(self) -> sqlite3.Connection:
        database_path = Path(self.path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
        try:
            self._initialize(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > self.SCHEMA_VERSION:
            raise ValueError(
                f"Scan history database version {version} is newer than this application supports."
            )
        scans_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'scans'"
        ).fetchone()
        if version == self.SCHEMA_VERSION:
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            if scans_table is None:
                connection.execute(
                    """
                    CREATE TABLE scans (
                        scan_id INTEGER PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        saved_at TEXT NOT NULL,
                        root_path TEXT NOT NULL,
                        scan_scope TEXT NOT NULL,
                        scan_roots_json TEXT NOT NULL,
                        requested_roots_json TEXT NOT NULL,
                        file_count INTEGER NOT NULL,
                        total_size INTEGER NOT NULL,
                        elapsed_seconds REAL NOT NULL,
                        inaccessible_items INTEGER NOT NULL,
                        inaccessible_directories INTEGER NOT NULL,
                        skipped_directories INTEGER NOT NULL,
                        hard_link_paths INTEGER NOT NULL,
                        skipped_files INTEGER NOT NULL,
                        subfolder_count INTEGER NOT NULL,
                        drive_total INTEGER,
                        drive_used INTEGER,
                        drive_free INTEGER,
                        categories_json TEXT NOT NULL,
                        subfolders_json TEXT NOT NULL,
                        largest_files_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE scanned_files (
                        scan_id INTEGER NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                        name TEXT NOT NULL,
                        path TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        category TEXT NOT NULL
                    )
                    """
                )
            else:
                columns = {
                    str(column["name"])
                    for column in connection.execute("PRAGMA table_info(scans)")
                }
                required = {
                    "scan_id",
                    "started_at",
                    "saved_at",
                    "root_path",
                    "scan_scope",
                    "scan_roots_json",
                    "file_count",
                    "total_size",
                    "elapsed_seconds",
                    "inaccessible_items",
                    "inaccessible_directories",
                    "skipped_directories",
                    "hard_link_paths",
                    "skipped_files",
                    "subfolder_count",
                    "categories_json",
                    "subfolders_json",
                    "largest_files_json",
                }
                missing = required - columns
                if missing:
                    raise ValueError(
                        "The older scan-history schema is missing required columns: "
                        + ", ".join(sorted(missing))
                    )
                if "completed_at" not in columns:
                    connection.execute(
                        "ALTER TABLE scans ADD COLUMN completed_at TEXT NOT NULL DEFAULT ''"
                    )
                    rows = connection.execute(
                        "SELECT scan_id, started_at, elapsed_seconds FROM scans"
                    ).fetchall()
                    for row in rows:
                        started = datetime.fromisoformat(str(row["started_at"]))
                        completed = datetime.fromtimestamp(
                            started.timestamp() + float(row["elapsed_seconds"]),
                            timezone.utc,
                        )
                        connection.execute(
                            "UPDATE scans SET completed_at = ? WHERE scan_id = ?",
                            (completed.isoformat(), row["scan_id"]),
                        )
                if "requested_roots_json" not in columns:
                    connection.execute(
                        "ALTER TABLE scans ADD COLUMN requested_roots_json TEXT NOT NULL DEFAULT '[]'"
                    )
                    if "scan_roots_json" in columns:
                        connection.execute(
                            "UPDATE scans SET requested_roots_json = scan_roots_json"
                        )
                files_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'scanned_files'"
                ).fetchone()
                if files_table is None:
                    raise ValueError(
                        "The older scan-history database has no scanned file records table."
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS scanned_files_scan_id ON scanned_files(scan_id)"
            )
            connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _summary(
        row: sqlite3.Row,
        *,
        load_error: str | None = None,
    ) -> ScanHistorySummary:
        usage = None
        if row["drive_total"] is not None:
            usage = DriveUsage(
                total=int(row["drive_total"]),
                used=int(row["drive_used"]),
                free=int(row["drive_free"]),
            )
        try:
            scan_roots = tuple(json.loads(row["scan_roots_json"]))
            requested_roots = tuple(json.loads(row["requested_roots_json"]))
            if not all(isinstance(root, str) for root in (*scan_roots, *requested_roots)):
                raise ValueError("Saved scan locations contain invalid values.")
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            scan_roots = ()
            requested_roots = ()
            load_error = load_error or f"Saved location metadata is invalid: {error}"
        return ScanHistorySummary(
            scan_id=int(row["scan_id"]),
            started_at=str(row["started_at"]),
            completed_at=str(row["completed_at"]),
            saved_at=str(row["saved_at"]),
            root_path=str(row["root_path"]),
            scan_scope=str(row["scan_scope"]),
            scan_roots=scan_roots,
            requested_roots=requested_roots,
            file_count=int(row["file_count"]),
            total_size=int(row["total_size"]),
            elapsed_seconds=float(row["elapsed_seconds"]),
            inaccessible_items=int(row["inaccessible_items"]),
            inaccessible_directories=int(row["inaccessible_directories"]),
            skipped_directories=int(row["skipped_directories"]),
            hard_link_paths=int(row["hard_link_paths"]),
            skipped_files=int(row["skipped_files"]),
            subfolder_count=int(row["subfolder_count"]),
            drive_usage=usage,
            load_error=load_error,
        )

    def save_scan(self, result: ScanResult) -> ScanHistorySummary:
        """Commit a complete scan and its file rows atomically."""
        saved_at = datetime.now(timezone.utc)
        completed_at = (
            datetime.fromisoformat(result.completed_at)
            if result.completed_at
            else saved_at
        )
        started_at = completed_at.timestamp() - result.elapsed_seconds
        started_text = datetime.fromtimestamp(started_at, timezone.utc).isoformat()
        saved_text = saved_at.isoformat()
        usage = result.drive_usage
        categories_json = json.dumps(
            [
                {
                    "name": item.name,
                    "size": item.size,
                    "file_count": item.file_count,
                    "largest_files": [_file_dict(file) for file in item.largest_files],
                }
                for item in result.categories
            ],
            separators=(",", ":"),
        )
        subfolders_json = json.dumps(
            [
                {
                    "name": item.name,
                    "path": item.path,
                    "size": item.size,
                    "file_count": item.file_count,
                }
                for item in result.subfolders
            ],
            separators=(",", ":"),
        )
        largest_files_json = json.dumps(
            [_file_dict(file) for file in result.largest_files],
            separators=(",", ":"),
        )

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO scans (
                        started_at, completed_at, saved_at, root_path, scan_scope, scan_roots_json,
                        requested_roots_json,
                        file_count, total_size, elapsed_seconds, inaccessible_items,
                        inaccessible_directories, skipped_directories, hard_link_paths,
                        skipped_files, subfolder_count, drive_total, drive_used, drive_free,
                        categories_json, subfolders_json, largest_files_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        started_text,
                        completed_at.isoformat(),
                        saved_text,
                        result.root_path,
                        result.scan_scope,
                        json.dumps(result.scan_roots, separators=(",", ":")),
                        json.dumps(
                            result.requested_roots or result.scan_roots,
                            separators=(",", ":"),
                        ),
                        len(result.files),
                        result.total_size,
                        result.elapsed_seconds,
                        result.inaccessible_items,
                        result.inaccessible_directories,
                        result.skipped_directories,
                        result.hard_link_paths,
                        result.skipped_files,
                        result.subfolder_count,
                        usage.total if usage else None,
                        usage.used if usage else None,
                        usage.free if usage else None,
                        categories_json,
                        subfolders_json,
                        largest_files_json,
                    ),
                )
                scan_id = int(cursor.lastrowid)
                self._insert_files(connection, scan_id, result.files)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        row = self._get_row(scan_id)
        if row is None:
            raise sqlite3.DatabaseError("The completed scan could not be read back from history.")
        return self._summary(row)

    @staticmethod
    def _insert_files(
        connection: sqlite3.Connection,
        scan_id: int,
        files: tuple[ScannedFile, ...],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO scanned_files (scan_id, name, path, size, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                (scan_id, file.name, file.path, file.size, file.category)
                for file in files
            ),
        )

    def list_scans(self) -> tuple[ScanHistorySummary, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM scans ORDER BY started_at DESC, scan_id DESC"
            ).fetchall()
            aggregates = {
                int(row["scan_id"]): (int(row["file_count"]), int(row["total_size"]))
                for row in connection.execute(
                    """
                    SELECT scan_id, COUNT(*) AS file_count, COALESCE(SUM(size), 0) AS total_size
                    FROM scanned_files GROUP BY scan_id
                    """
                )
            }
        summaries: list[ScanHistorySummary] = []
        for row in rows:
            scan_id = int(row["scan_id"])
            expected_count = int(row["file_count"])
            expected_size = int(row["total_size"])
            actual_count, actual_size = aggregates.get(scan_id, (0, 0))
            load_error = None
            if actual_count != expected_count or actual_size != expected_size:
                load_error = (
                    f"This saved scan declares {expected_count:,} file(s) and "
                    f"{expected_size:,} bytes, but its history contains "
                    f"{actual_count:,} file record(s) totaling {actual_size:,} bytes. "
                    "The record is preserved; rescan the location to create a valid entry."
                )
            summaries.append(self._summary(row, load_error=load_error))
        return tuple(summaries)

    def load_scan(
        self,
        scan_id: int,
        *,
        cancel_event: Event | None = None,
    ) -> ScanResult:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM scans WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if row is None:
                raise ValueError("That saved scan no longer exists.")
            cursor = connection.execute(
                """
                SELECT name, path, size, category FROM scanned_files
                WHERE scan_id = ? ORDER BY rowid
                """,
                (scan_id,),
            )
            file_list: list[ScannedFile] = []
            while batch := cursor.fetchmany(4096):
                if cancel_event is not None and cancel_event.is_set():
                    raise HistoryLoadCancelled("Loading the saved scan was cancelled.")
                file_list.extend(
                    ScannedFile(
                        name=str(item["name"]),
                        path=str(item["path"]),
                        size=int(item["size"]),
                        category=str(item["category"]),
                    )
                    for item in batch
                )
            if cancel_event is not None and cancel_event.is_set():
                raise HistoryLoadCancelled("Loading the saved scan was cancelled.")
            files = tuple(file_list)
        categories = tuple(
            CategorySummary(
                name=str(item["name"]),
                size=int(item["size"]),
                file_count=int(item["file_count"]),
                largest_files=tuple(
                    _file_from_dict(file) for file in item["largest_files"]
                ),
            )
            for item in json.loads(row["categories_json"])
        )
        subfolders = tuple(
            SubfolderSummary(
                name=str(item["name"]),
                path=str(item["path"]),
                size=int(item["size"]),
                file_count=int(item["file_count"]),
            )
            for item in json.loads(row["subfolders_json"])
        )
        largest_files = tuple(
            _file_from_dict(item) for item in json.loads(row["largest_files_json"])
        )
        summary = self._summary(row)
        if (
            len(files) != summary.file_count
            or sum(file.size for file in files) != summary.total_size
        ):
            raise sqlite3.DatabaseError(
                f"This saved scan declares {summary.file_count:,} file(s) and "
                f"{summary.total_size:,} bytes, but contains {len(files):,} file record(s) "
                f"totaling {sum(file.size for file in files):,} bytes. "
                "The record was not changed; rescan the location to create a valid entry."
            )
        return ScanResult(
            root_path=summary.root_path,
            files=files,
            categories=categories,
            subfolders=subfolders,
            subfolder_count=summary.subfolder_count,
            largest_files=largest_files,
            scanned_size=summary.total_size,
            elapsed_seconds=summary.elapsed_seconds,
            inaccessible_items=summary.inaccessible_items,
            inaccessible_directories=summary.inaccessible_directories,
            skipped_directories=summary.skipped_directories,
            scan_scope=summary.scan_scope,
            scan_roots=summary.scan_roots,
            drive_usage=summary.drive_usage,
            hard_link_paths=summary.hard_link_paths,
            skipped_files=summary.skipped_files,
            requested_roots=summary.requested_roots,
            completed_at=summary.completed_at,
        )

    def delete_scan(self, scan_id: int) -> None:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM scans WHERE scan_id = ?", (scan_id,))
            if cursor.rowcount == 0:
                raise ValueError("That saved scan no longer exists.")
            connection.commit()
            connection.execute("PRAGMA incremental_vacuum")

    def stats(self) -> ScanHistoryStats:
        with self._connection() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0])
        database_path = Path(self.path)
        database_bytes = 0
        for candidate in (
            database_path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
            Path(f"{self.path}-journal"),
        ):
            try:
                database_bytes += candidate.stat().st_size
            except FileNotFoundError:
                continue
        return ScanHistoryStats(scan_count=count, database_bytes=database_bytes)

    def _get_row(self, scan_id: int) -> sqlite3.Row | None:
        with self._connection() as connection:
            return connection.execute(
                "SELECT * FROM scans WHERE scan_id = ?", (scan_id,)
            ).fetchone()
