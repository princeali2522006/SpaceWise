import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from core.discovery import LocalDrive
from core.duplicates import (
    DuplicateAnalysis,
    DuplicateGroup,
    DuplicateProgress,
    UnverifiedFile,
)
from core.scanner import DriveUsage, ScanCancelledError, scan_folder
from ui.duplicates_page import DuplicatesPage
from ui.main_window import MainWindow


class ScanModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.cache_path_patcher = patch(
            "core.duplicates._default_cache_path",
            return_value=str(self.root / "application-cache" / "hashes.sqlite3"),
        )
        self.cache_path_patcher.start()
        (self.root / "notes.txt").write_text("read-only sample", encoding="utf-8")
        (self.root / "Documents").mkdir()
        (self.root / "Documents" / "annual.txt").write_text("nested sample", encoding="utf-8")

    def tearDown(self) -> None:
        self.cache_path_patcher.stop()
        self.temporary_directory.cleanup()

    def dispose_widget(self, widget: QWidget) -> None:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

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
        self.assertIn(
            "Logical size of scanned paths",
            window.dashboard.stats[1].caption_label.text(),
        )
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

    def test_duplicate_analysis_uses_latest_result_and_is_invalidated_by_new_scan(self) -> None:
        first = self.root / "duplicate-a.bin"
        second = self.root / "duplicate-b.bin"
        first.write_bytes(b"identical content")
        second.write_bytes(b"identical content")
        window = MainWindow()
        result = scan_folder(self.root)
        window._scan_completed(result)

        window.find_duplicates(False)
        worker = window.duplicate_worker
        self.assertIsNotNone(worker)
        worker.wait()
        for _ in range(10):
            self.app.processEvents()

        analysis = window.duplicates.analysis
        self.assertEqual(len(analysis.groups), 1)
        self.assertEqual(set(analysis.groups[0].paths), {str(first), str(second)})
        self.assertEqual(
            analysis.potential_recoverable_bytes,
            len(b"identical content"),
        )
        self.assertIsNone(window.duplicate_worker)

        window._scan_completed(scan_folder(self.root))
        self.assertIsNone(window.duplicates.analysis)
        window.close()

    def test_duplicate_analysis_can_be_limited_to_known_personal_roots(self) -> None:
        personal = self.root / "personal"
        personal.mkdir()
        personal_first = personal / "one.bin"
        personal_second = personal / "two.bin"
        outside = self.root / "outside.bin"
        personal_first.write_bytes(b"same bytes")
        personal_second.write_bytes(b"same bytes")
        outside.write_bytes(b"same bytes")
        window = MainWindow()
        window._scan_completed(scan_folder(self.root))

        with patch("ui.main_window.discover_user_folders", return_value=(str(personal),)):
            window.find_duplicates(True)
        worker = window.duplicate_worker
        self.assertIsNotNone(worker)
        worker.wait()
        for _ in range(10):
            self.app.processEvents()

        analysis = window.duplicates.analysis
        self.assertEqual(len(analysis.groups), 1)
        self.assertEqual(
            set(analysis.groups[0].paths),
            {str(personal_first), str(personal_second)},
        )
        self.assertNotIn(str(outside), analysis.groups[0].paths)
        window.close()

    def test_duplicate_page_shows_unverified_paths_and_reasons(self) -> None:
        page = DuplicatesPage()
        self.addCleanup(self.dispose_widget, page)
        missing_path = str(self.root / "changed.bin")
        page.set_result(
            DuplicateAnalysis(
                groups=(),
                unverified_files=(
                    UnverifiedFile(missing_path, "File changed during hashing."),
                ),
                candidate_files=2,
                hashed_files=1,
                elapsed_seconds=0.5,
            )
        )

        self.assertEqual(page.groups_table.model().rowCount(), 1)
        self.assertEqual(page.groups_table.model().index(0, 0).data(), "Unverified")
        self.assertEqual(page.paths_model.rowCount(), 1)
        self.assertEqual(page.paths_model.index(0, 0).data(), missing_path)
        self.assertIn("File changed during hashing", page.detail_summary.text())
        self.assertIn("Incomplete analysis", page.result_state_label.text())
        self.assertIn("not included in savings", page.results_model.data(
            page.results_model.index(0, 0),
            Qt.ItemDataRole.ToolTipRole,
        ).lower())

    def test_duplicate_progress_shows_stage_counters_and_no_estimate(self) -> None:
        page = DuplicatesPage()
        self.addCleanup(self.dispose_widget, page)
        page.set_analyzing()
        page.set_progress(
            DuplicateProgress(
                files_checked=17,
                candidate_files=100,
                elapsed_seconds=9.2,
                hashed_files=5,
                bytes_processed=2 * 1024**2,
                skipped_files=2,
                stage="verifying SHA-256",
                cached_files_reused=3,
            )
        )

        self.assertEqual(
            page.progress_status.text(),
            "Verifying candidate contents with full SHA-256",
        )
        details = page.progress_details.text()
        for expected in ("17", "100", "5", "2.0 MB", "2", "3", "9.2 s"):
            self.assertIn(expected, details)
        self.assertEqual(page.progress_bar.minimum(), 0)
        self.assertEqual(page.progress_bar.maximum(), 0)
        self.assertNotIn("%", details)

    def test_duplicate_group_selection_shows_all_paths_and_copy_action(self) -> None:
        page = DuplicatesPage()
        self.addCleanup(self.dispose_widget, page)
        paths = (
            str(self.root / "notes.txt"),
            str(self.root / "Documents" / "annual.txt"),
        )
        group = DuplicateGroup(
            size=16,
            digest="a" * 64,
            paths=paths,
            verified_copy_count=2,
        )
        page.set_result(
            DuplicateAnalysis(
                groups=(group,),
                unverified_files=(),
                candidate_files=2,
                hashed_files=2,
                elapsed_seconds=0.1,
            )
        )

        self.assertEqual(page.groups_table.model().rowCount(), 1)
        self.assertEqual(page.paths_model.rowCount(), 2)
        self.assertEqual(
            {page.paths_model.index(row, 0).data() for row in range(2)},
            set(paths),
        )
        with patch("ui.duplicates_page.QApplication.clipboard") as clipboard:
            page._copy_selected_path()
        clipboard.return_value.setText.assert_called_once_with(paths[0])
        self.assertIn("SHA-256 content", page.status_label.text())
        self.assertIn("estimated potential recoverable space", page.summary_label.text())

    def test_duplicate_group_table_sorts_size_savings_and_copy_count(self) -> None:
        page = DuplicatesPage()
        self.addCleanup(self.dispose_widget, page)
        groups = (
            DuplicateGroup(4, "a" * 64, ("alpha.bin", "alpha-copy.bin"), 2),
            DuplicateGroup(10, "b" * 64, ("beta.bin", "beta-copy.bin"), 2),
            DuplicateGroup(6, "c" * 64, ("gamma.bin", "gamma-2.bin", "gamma-3.bin", "gamma-4.bin"), 4),
        )
        page.set_result(
            DuplicateAnalysis(
                groups=groups,
                unverified_files=(),
                candidate_files=8,
                hashed_files=8,
                elapsed_seconds=0.1,
            )
        )
        table_model = page.groups_table.model()

        page.groups_table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        self.assertEqual(table_model.index(0, 1).data(), "beta.bin")
        page.groups_table.sortByColumn(4, Qt.SortOrder.DescendingOrder)
        self.assertEqual(table_model.index(0, 1).data(), "gamma.bin")
        page.groups_table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        self.assertEqual(table_model.index(0, 1).data(), "gamma.bin")

    def test_duplicate_results_handle_thousands_of_groups_with_table_models(self) -> None:
        page = DuplicatesPage()
        self.addCleanup(self.dispose_widget, page)
        groups = tuple(
            DuplicateGroup(
                size=index,
                digest=f"{index:064x}",
                paths=(f"file-{index}.bin", f"copy-{index}.bin"),
                verified_copy_count=2,
            )
            for index in range(1, 2001)
        )
        page.set_result(
            DuplicateAnalysis(
                groups=groups,
                unverified_files=(),
                candidate_files=4000,
                hashed_files=4000,
                elapsed_seconds=1.0,
            )
        )

        self.assertEqual(page.groups_table.model().rowCount(), 2000)
        self.assertIsNotNone(page.groups_table.model().index(1999, 1).data())
        self.assertEqual(page.paths_model.rowCount(), 2)
        self.assertLess(len(page.findChildren(QPushButton)), 10)

    def test_duplicate_page_empty_and_cancelled_states_are_clear(self) -> None:
        page = DuplicatesPage()
        self.addCleanup(self.dispose_widget, page)
        self.assertIn("No duplicate analysis yet", page.result_state_label.text())
        page.set_result(
            DuplicateAnalysis(
                groups=(),
                unverified_files=(),
                candidate_files=0,
                hashed_files=0,
                elapsed_seconds=0.0,
            )
        )
        self.assertIn("no equal-size candidate sets", page.result_state_label.text())

        page.set_cancelled("User requested cancellation.")
        self.assertIn("Analysis cancelled", page.result_state_label.text())
        self.assertIn("No partial results", page.status_label.text())

    def test_duplicate_file_reveal_is_read_only_and_handles_unavailable_paths(self) -> None:
        page = DuplicatesPage()
        self.addCleanup(self.dispose_widget, page)
        existing = self.root / "notes.txt"
        with patch(
            "ui.duplicates_page.QProcess.startDetached",
            return_value=(True, 42),
        ) as start_detached:
            page.show_in_windows_explorer(str(existing))
        start_detached.assert_called_once_with(
            "explorer.exe",
            ["/select,", str(existing)],
        )

        missing = str(self.root / "missing.txt")
        with patch("ui.duplicates_page.QMessageBox.information") as information:
            with patch("ui.duplicates_page.QProcess.startDetached") as start_detached:
                page.show_in_windows_explorer(missing)
        information.assert_called_once()
        start_detached.assert_not_called()

        with patch(
            "ui.duplicates_page.os.stat",
            side_effect=PermissionError("access denied"),
        ):
            with patch("ui.duplicates_page.QMessageBox.warning") as warning:
                with patch("ui.duplicates_page.QProcess.startDetached") as start_detached:
                    page.show_in_windows_explorer(str(existing))
        warning.assert_called_once()
        start_detached.assert_not_called()


if __name__ == "__main__":
    unittest.main()
