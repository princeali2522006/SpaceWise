import sqlite3
import tempfile
import unittest
from threading import Event
from pathlib import Path
from unittest.mock import patch

from core.history import HistoryLoadCancelled, ScanHistoryStore
from core.scanner import DriveUsage, scan_folder


class ScanHistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.scan_root = self.root / "sample"
        self.scan_root.mkdir()
        (self.scan_root / "notes.txt").write_text("history sample", encoding="utf-8")
        (self.scan_root / "empty.bin").write_bytes(b"")
        (self.scan_root / "empty-folder").mkdir()
        self.database_path = self.root / "app-data" / "SpaceWise" / "history.sqlite3"
        self.store = ScanHistoryStore(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_save_and_load_preserve_complete_scan_metadata(self) -> None:
        result = scan_folder(self.scan_root)
        saved = self.store.save_scan(result)
        loaded = self.store.load_scan(saved.scan_id)

        self.assertEqual(loaded, result)
        self.assertEqual(saved.requested_roots, (str(self.scan_root),))
        self.assertEqual(self.store.list_scans(), (saved,))
        self.assertEqual(self.store.stats().scan_count, 1)
        self.assertGreater(self.store.stats().database_bytes, 0)
        self.assertEqual(loaded.subfolder_count, result.subfolder_count)
        self.assertEqual(loaded.categories, result.categories)

    def test_drive_usage_and_scan_coverage_counters_round_trip(self) -> None:
        result = scan_folder(self.scan_root)
        result = result.__class__(
            **{
                **result.__dict__,
                "drive_usage": DriveUsage(total=1000, used=700, free=300),
                "inaccessible_items": 2,
                "inaccessible_directories": 1,
                "skipped_directories": 3,
                "hard_link_paths": 4,
                "skipped_files": 5,
            }
        )
        saved = self.store.save_scan(result)

        self.assertEqual(self.store.load_scan(saved.scan_id), result)

    def test_failed_file_insert_rolls_back_the_entire_snapshot(self) -> None:
        result = scan_folder(self.scan_root)
        with patch.object(
            self.store,
            "_insert_files",
            side_effect=sqlite3.OperationalError("simulated interrupted write"),
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "interrupted"):
                self.store.save_scan(result)

        self.assertEqual(self.store.list_scans(), ())
        self.assertEqual(self.store.stats().scan_count, 0)

    def test_missing_or_corrupt_snapshot_is_reported(self) -> None:
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            self.store.load_scan(123)

        result = scan_folder(self.scan_root)
        saved = self.store.save_scan(result)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "DELETE FROM scanned_files WHERE scan_id = ?",
                (saved.scan_id,),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(sqlite3.DatabaseError, "declares"):
            self.store.load_scan(saved.scan_id)

    def test_mismatched_record_is_listed_without_blocking_other_scans(self) -> None:
        valid = self.store.save_scan(scan_folder(self.scan_root))
        damaged_root = self.root / "damaged"
        damaged_root.mkdir()
        (damaged_root / "data.bin").write_bytes(b"before")
        damaged = self.store.save_scan(scan_folder(damaged_root))
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "DELETE FROM scanned_files WHERE scan_id = ? AND name = ?",
                (damaged.scan_id, "data.bin"),
            )
            connection.commit()
        finally:
            connection.close()

        records = {record.scan_id: record for record in self.store.list_scans()}

        self.assertIsNone(records[valid.scan_id].load_error)
        self.assertIn("declares", records[damaged.scan_id].load_error or "")
        self.assertEqual(self.store.load_scan(valid.scan_id).files, scan_folder(self.scan_root).files)
        with self.assertRaisesRegex(sqlite3.DatabaseError, "declares"):
            self.store.load_scan(damaged.scan_id)

    def test_saved_metadata_loads_when_original_path_is_now_inaccessible(self) -> None:
        saved = self.store.save_scan(scan_folder(self.scan_root))
        expected_files = scan_folder(self.scan_root).files
        for child in self.scan_root.iterdir():
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        self.scan_root.rmdir()

        self.assertEqual(self.store.load_scan(saved.scan_id).files, expected_files)

    def test_loading_can_be_cancelled_without_trusting_partial_results(self) -> None:
        saved = self.store.save_scan(scan_folder(self.scan_root))
        cancel_event = Event()
        cancel_event.set()

        with self.assertRaises(HistoryLoadCancelled):
            self.store.load_scan(saved.scan_id, cancel_event=cancel_event)

    def test_older_schema_is_migrated_additively_and_loads(self) -> None:
        legacy_path = self.root / "legacy" / "history.sqlite3"
        legacy_path.parent.mkdir()
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(
                """
                CREATE TABLE scans (
                    scan_id INTEGER PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    scan_scope TEXT NOT NULL,
                    scan_roots_json TEXT NOT NULL,
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
                );
                CREATE TABLE scanned_files (
                    scan_id INTEGER NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    category TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT INTO scans VALUES (
                    1, '2025-01-01T00:00:00+00:00', '2025-01-01T00:01:00+00:00',
                    'Z:\\\\legacy', 'Selected folder', '["Z:\\\\legacy"]', 1, 5, 2.0,
                    0, 0, 0, 0, 0, 0, NULL, NULL, NULL, '[]', '[]', '[]'
                )
                """
            )
            connection.execute(
                "INSERT INTO scanned_files VALUES (1, 'old.txt', 'Z:\\\\legacy\\\\old.txt', 5, 'Documents')"
            )
            connection.commit()
        finally:
            connection.close()

        legacy_store = ScanHistoryStore(legacy_path)
        saved = legacy_store.load_scan(1)

        self.assertEqual(saved.files[0].name, "old.txt")
        self.assertEqual(saved.requested_roots, ("Z:\\legacy",))
        migrated = sqlite3.connect(legacy_path)
        try:
            version = migrated.execute("PRAGMA user_version").fetchone()[0]
            columns = {
                row[1] for row in migrated.execute("PRAGMA table_info(scans)")
            }
        finally:
            migrated.close()
        self.assertEqual(version, ScanHistoryStore.SCHEMA_VERSION)
        self.assertIn("completed_at", columns)
        self.assertIn("requested_roots_json", columns)

    def test_delete_removes_snapshot_rows_without_touching_scanned_files(self) -> None:
        source = self.scan_root / "notes.txt"
        content_before = source.read_bytes()
        timestamp_before = source.stat().st_mtime_ns
        saved = self.store.save_scan(scan_folder(self.scan_root))

        self.store.delete_scan(saved.scan_id)

        self.assertEqual(self.store.list_scans(), ())
        self.assertEqual(source.read_bytes(), content_before)
        self.assertEqual(source.stat().st_mtime_ns, timestamp_before)
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            self.store.delete_scan(saved.scan_id)


if __name__ == "__main__":
    unittest.main()
