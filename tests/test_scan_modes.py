import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.discovery import LocalDrive
from core.scanner import DriveUsage, ScanCancelledError, scan_folder
from ui.main_window import MainWindow


class ScanModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "notes.txt").write_text("read-only sample", encoding="utf-8")
        (self.root / "Documents").mkdir()
        (self.root / "Documents" / "annual.txt").write_text("nested sample", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def wait_for_scan(self, window: MainWindow) -> None:
        worker = window.scan_worker
        self.assertIsNotNone(worker)
        worker.wait()
        for _ in range(5):
            self.app.processEvents()
        self.assertIsNone(window.scan_worker)

    def test_manual_folder_scan_updates_dashboard_and_explorer_together(self) -> None:
        window = MainWindow()
        self.assertIsNone(window.scan_worker)
        window.selected_folder = str(self.root)
        window.scan_selected_folder()
        self.assertIn("Selected folder", window.dashboard.scan_status_label.text())
        self.wait_for_scan(window)

        self.assertEqual(window.scan_result.total_size, sum(file.size for file in window.explorer.files))
        self.assertEqual(window.dashboard.result, window.scan_result)
        self.assertEqual(window.explorer.last_scan_root, window.scan_result.root_path)
        window.close()

    def test_my_files_scan_deduplicates_overlapping_known_folder_roots(self) -> None:
        window = MainWindow()
        with patch(
            "ui.main_window.discover_user_folders",
            return_value=(str(self.root), str(self.root / "Documents")),
        ):
            window.scan_my_files()
        self.wait_for_scan(window)

        self.assertEqual(len(window.scan_result.scan_roots), 2)
        self.assertEqual(len(window.scan_result.files), 2)
        self.assertEqual(window.scan_result.scan_scope, "Scan My Files")
        self.assertEqual(window.dashboard.result, window.scan_result)
        self.assertEqual(window.explorer.files, window.scan_result.files)
        window.close()

    def test_drive_scan_keeps_os_usage_separate_from_scanned_files(self) -> None:
        drive = LocalDrive(
            path=str(self.root),
            drive_type="Fixed",
            usage=DriveUsage(total=10_000, used=7_500, free=2_500),
        )
        with (
            patch("ui.main_window.discover_local_drives", return_value=(drive,)),
            patch(
                "ui.main_window.refresh_drive_usage",
                return_value=DriveUsage(total=10_000, used=7_500, free=2_500),
            ),
        ):
            window = MainWindow()
            window.scan_drive(str(self.root))
            self.wait_for_scan(window)

        result = window.scan_result
        self.assertEqual(result.scan_scope, f"Drive scan: {self.root}")
        self.assertEqual(result.drive_usage.total, 10_000)
        self.assertLess(result.total_size, result.drive_usage.used)
        self.assertIn("Scanned-file size", window.dashboard.stats[1].caption_label.text())
        self.assertIn("capacity (Windows)", window.dashboard.drive_usage_label.text())
        self.assertEqual(window.dashboard.result, result)
        self.assertEqual(window.explorer.files, result.files)
        window.close()

    def test_cancel_keeps_last_completed_dashboard_and_explorer_result(self) -> None:
        window = MainWindow()
        previous_result = scan_folder(self.root)
        window.scan_result = previous_result
        window.dashboard.set_result(previous_result)
        window.explorer.set_result(previous_result)
        another_folder = self.root / "Documents"
        started = threading.Event()

        def wait_for_cancel(_folders: tuple[str, ...], **options: object) -> None:
            assert _folders
            started.set()
            cancel_event = options["cancel_event"]
            assert isinstance(cancel_event, threading.Event)
            cancel_event.wait(2)
            raise ScanCancelledError("Scan cancelled. The last completed scan is unchanged.")

        with patch("ui.worker.scan_folders", side_effect=wait_for_cancel):
            window._start_scan(
                (str(another_folder),),
                "Selected folder",
                strict_roots=True,
            )
            self.assertTrue(started.wait(1))
            window.cancel_scan()
            self.wait_for_scan(window)

        self.assertIs(window.scan_result, previous_result)
        self.assertIs(window.dashboard.result, previous_result)
        self.assertEqual(window.explorer.files, previous_result.files)
        self.assertIn("cancelled", window.dashboard.scan_status_label.text().casefold())
        window.close()


if __name__ == "__main__":
    unittest.main()
