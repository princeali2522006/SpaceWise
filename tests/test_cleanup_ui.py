import gc
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

from core.duplicates import DuplicateAnalysis, DuplicateGroup
from core.recommendations import build_cleanup_recommendations
from core.scanner import scan_folder
from ui.cleanup_page import (
    CleanupPage,
    format_age_days,
    format_large_threshold,
    parse_large_threshold,
)
from ui.main_window import MainWindow


class CleanupPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.pages: list[CleanupPage] = []
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for page in self.pages:
            page.close()
            page.deleteLater()
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.pages.clear()
        self.windows.clear()
        gc.collect()
        self.temporary_directory.cleanup()

    def _new_page(self) -> CleanupPage:
        page = CleanupPage()
        self.pages.append(page)
        return page

    def test_cleanup_action_runs_off_ui_thread_using_latest_scan(self) -> None:
        installer = self.root / "Downloads" / "setup.exe"
        large_file = self.root / "Downloads" / "large.bin"
        installer.parent.mkdir()
        installer.write_bytes(b"old installer")
        large_file.write_bytes(b"large enough" * 100_000)
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=120)).timestamp()
        os.utime(installer, (old_timestamp, old_timestamp))

        window = MainWindow()
        self.windows.append(window)
        result = scan_folder(self.root)
        window._scan_completed(result)
        window.cleanup._set_large_threshold(1024**2)
        with (
            patch("ui.main_window.discover_user_folders", return_value=(str(installer.parent),)),
            patch("core.recommendations._default_protected_roots", return_value=()),
        ):
            window.analyze_cleanup(90, 1024**2)
            worker = window.cleanup_worker
            self.assertIsNotNone(worker)
            worker.wait()
            for _ in range(10):
                self.app.processEvents()

        recommendations = window.cleanup.recommendations
        self.assertEqual(
            [item.file.path for item in recommendations.old_installers],
            [str(installer)],
        )
        self.assertEqual(
            {item.file.path for item in recommendations.large_files},
            {str(large_file)},
        )
        self.assertTrue(recommendations.duplicate_analysis_pending)
        installer_row = next(
            row
            for row in range(window.cleanup.recommendation_proxy.rowCount())
            if window.cleanup.recommendation_proxy.index(row, 1).data()
            == installer.name
        )
        window.cleanup.recommendation_table.setCurrentIndex(
            window.cleanup.recommendation_proxy.index(installer_row, 0)
        )
        self.assertIn("does not show", window.cleanup.detail_warning_label.text())
        self.assertIn("90 days", window.cleanup.detail_condition_label.text())
        self.assertIsNone(window.cleanup_worker)
        window.close()

    def test_cleanup_runs_while_duplicate_verification_is_active(self) -> None:
        large_file = self.root / "large.bin"
        large_file.write_bytes(b"x" * (2 * 1024**2))
        window = MainWindow()
        self.windows.append(window)
        window._scan_completed(scan_folder(self.root))
        window.cleanup._set_large_threshold(1024**2)

        class ActiveDuplicateWorker:
            @staticmethod
            def isRunning() -> bool:
                return True

            @staticmethod
            def cancel() -> None:
                return None

        window.duplicate_worker = ActiveDuplicateWorker()
        with (
            patch("ui.main_window.discover_user_folders", return_value=()),
            patch("core.recommendations._default_protected_roots", return_value=()),
        ):
            window.analyze_cleanup(90, 1024**2)
            worker = window.cleanup_worker
            self.assertIsNotNone(worker)
            worker.wait()
            for _ in range(10):
                self.app.processEvents()

        recommendations = window.cleanup.recommendations
        self.assertIsNotNone(recommendations)
        self.assertTrue(recommendations.duplicate_analysis_pending)
        self.assertEqual(
            [item.file.path for item in recommendations.large_files],
            [str(large_file)],
        )
        group = DuplicateGroup(1, "a" * 64, ("one", "two"), 2)
        window.cleanup.set_duplicate_analysis(
            DuplicateAnalysis(
                groups=(group,),
                unverified_files=(),
                candidate_files=2,
                hashed_files=2,
                elapsed_seconds=0.1,
            )
        )
        self.assertFalse(window.cleanup.recommendations.duplicate_analysis_pending)
        self.assertEqual(window.cleanup.recommendations.verified_duplicates, (group,))
        window.duplicate_worker = None

    def test_large_threshold_minimum_steps_units_and_direct_validation(self) -> None:
        page = self._new_page()
        self.assertEqual(page._large_threshold_bytes, 1024**3)
        self.assertEqual(page.large_file_threshold.text(), "1 GB")

        page._set_large_threshold(1024**2)
        self.assertFalse(page.large_file_minus_button.isEnabled())
        page.large_file_plus_button.click()
        self.assertEqual(page._large_threshold_bytes, 2 * 1024**2)
        page.large_file_minus_button.click()
        self.assertEqual(page._large_threshold_bytes, 1024**2)
        self.assertFalse(page.large_file_minus_button.isEnabled())

        self.assertEqual(format_large_threshold(1023 * 1024**2), "1023 MB")
        self.assertEqual(format_large_threshold(1024 * 1024**2), "1 GB")
        self.assertEqual(format_large_threshold(1536 * 1024**2), "1.5 GB")
        self.assertEqual(format_age_days(1), "About 1 day")
        self.assertEqual(parse_large_threshold("1 MB"), 1024**2)
        self.assertEqual(parse_large_threshold("1.5 GB"), 1536 * 1024**2)
        with self.assertRaises(ValueError):
            parse_large_threshold("0.99 MB")
        with self.assertRaises(ValueError):
            page._set_large_threshold(1024**2 - 1)

        page.large_file_threshold_input.setText("1.5 GB")
        page._commit_large_threshold_input()
        self.assertEqual(page._large_threshold_bytes, 1536 * 1024**2)
        page.large_file_threshold_input.setText("0 MB")
        page._commit_large_threshold_input()
        self.assertEqual(page._large_threshold_bytes, 1536 * 1024**2)
        self.assertIn("from 1 MB", page.status_label.text())

    def test_changing_threshold_reapplies_cached_recommendation_candidates(self) -> None:
        file_path = self.root / "medium.bin"
        file_path.write_bytes(b"x" * (2 * 1024**2))
        scanned_file = scan_folder(self.root).files[0]
        recommendations = build_cleanup_recommendations(
            (scanned_file,),
            personal_roots=(),
            large_file_threshold_bytes=3 * 1024**2,
            protected_roots=(),
        )
        page = self._new_page()
        page._set_large_threshold(3 * 1024**2)
        page.set_result(recommendations)
        self.assertEqual(len(page.recommendations.large_files), 0)
        page.large_file_threshold_input.setText("1 MB")
        page._commit_large_threshold_input()
        self.assertEqual(
            [item.file.path for item in page.recommendations.large_files],
            [str(file_path)],
        )
        self.assertEqual(page.recommendation_proxy.rowCount(), 1)
        self.assertIn("1 MB", page.detail_condition_label.text())

    def test_category_filter_details_and_read_only_file_actions(self) -> None:
        file_path = self.root / "large.bin"
        file_path.write_bytes(b"unchanged" * 200_000)
        scanned_file = scan_folder(self.root).files[0]
        before = file_path.stat()
        recommendations = build_cleanup_recommendations(
            (scanned_file,),
            personal_roots=(),
            large_file_threshold_bytes=1024**2,
            protected_roots=(),
        )
        page = self._new_page()
        page._set_large_threshold(1024**2)
        page.set_result(recommendations)
        page.category_filter.setCurrentText("Older installer")
        self.assertTrue(page.recommendation_table.isHidden())
        self.assertIn("No recommendations match", page.empty_state_label.text())
        page.category_filter.setCurrentText("Large file")
        self.assertFalse(page.recommendation_table.isHidden())
        page.recommendation_table.setCurrentIndex(
            page.recommendation_proxy.index(0, 0)
        )
        self.assertEqual(page.detail_paths.toPlainText(), str(file_path))
        self.assertIn("not necessarily unwanted", page.detail_warning_label.text())
        self.assertIn("MB", page.recommendation_proxy.index(0, 2).data() or "")
        self.assertIn("days", page.recommendation_proxy.index(0, 3).data() or "")
        page.copy_selected_path()
        self.assertEqual(QApplication.clipboard().text(), str(file_path))
        with patch("ui.cleanup_page.QProcess.startDetached", return_value=(True, 1)):
            page.show_selected_path()
        after = file_path.stat()
        self.assertEqual(file_path.read_bytes(), b"unchanged" * 200_000)
        self.assertEqual(before.st_size, after.st_size)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)

    def test_missing_recommendation_path_is_reported_without_opening(self) -> None:
        file_path = self.root / "gone.bin"
        file_path.write_bytes(b"x" * (2 * 1024**2))
        scanned_file = scan_folder(self.root).files[0]
        recommendations = build_cleanup_recommendations(
            (scanned_file,),
            personal_roots=(),
            large_file_threshold_bytes=1024**2,
            protected_roots=(),
        )
        file_path.unlink()
        page = self._new_page()
        page._set_large_threshold(1024**2)
        page.set_result(recommendations)
        page.recommendation_table.setCurrentIndex(
            page.recommendation_proxy.index(0, 0)
        )
        page.show_selected_path()
        self.assertIn("no longer exists", page.status_label.text())

    def test_inaccessible_recommendation_path_is_reported(self) -> None:
        file_path = self.root / "unavailable.bin"
        file_path.write_bytes(b"x" * (2 * 1024**2))
        scanned_file = scan_folder(self.root).files[0]
        recommendations = build_cleanup_recommendations(
            (scanned_file,),
            personal_roots=(),
            large_file_threshold_bytes=1024**2,
            protected_roots=(),
        )
        page = self._new_page()
        page._set_large_threshold(1024**2)
        page.set_result(recommendations)
        with patch("ui.cleanup_page._stat_path", side_effect=PermissionError("access denied")):
            page.show_selected_path()
        self.assertIn("access denied", page.status_label.text())

    def test_empty_cleanup_state_and_category_filter(self) -> None:
        page = self._new_page()
        result = build_cleanup_recommendations(
            (),
            personal_roots=(),
            protected_roots=(),
        )
        page.set_result(result)
        self.assertTrue(page.recommendation_table.isHidden())
        self.assertFalse(page.empty_state_label.isHidden())
        self.assertIn("No review opportunities", page.empty_state_label.text())

    def test_cleanup_list_sorts_size_age_and_duplicate_savings(self) -> None:
        downloads = self.root / "Downloads"
        downloads.mkdir()
        recent_installer = downloads / "recent.exe"
        old_installer = downloads / "old.msi"
        recent_installer.write_bytes(b"recent")
        old_installer.write_bytes(b"old")
        large_file = self.root / "large.bin"
        largest_file = self.root / "largest.bin"
        large_file.write_bytes(b"x" * (2 * 1024**2))
        largest_file.write_bytes(b"x" * (4 * 1024**2))
        now = datetime.now(timezone.utc)
        for path, age in ((recent_installer, 100), (old_installer, 200)):
            timestamp = (now - timedelta(days=age)).timestamp()
            os.utime(path, (timestamp, timestamp))
        scanned_files = scan_folder(self.root).files
        duplicate_analysis = DuplicateAnalysis(
            groups=(
                DuplicateGroup(100, "a" * 64, ("a1.bin", "a2.bin"), 2),
                DuplicateGroup(
                    200,
                    "b" * 64,
                    ("b1.bin", "b2.bin", "b3.bin", "b4.bin"),
                    4,
                ),
            ),
            unverified_files=(),
            candidate_files=6,
            hashed_files=6,
            elapsed_seconds=0.1,
        )
        recommendations = build_cleanup_recommendations(
            scanned_files,
            personal_roots=(str(downloads),),
            large_file_threshold_bytes=1024**2,
            duplicate_analysis=duplicate_analysis,
            protected_roots=(),
            now=now,
        )
        page = self._new_page()
        page._set_large_threshold(1024**2)
        page.set_result(recommendations)
        self.assertEqual(
            page.recommendation_table.horizontalHeader().sectionResizeMode(0),
            page.recommendation_table.horizontalHeader().ResizeMode.Interactive,
        )
        self.assertTrue(page.recommendation_table.isSortingEnabled())

        page.category_filter.setCurrentText("Large file")
        page.recommendation_table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        self.assertEqual(
            page.recommendation_proxy.index(0, 1).data(),
            largest_file.name,
        )

        page.category_filter.setCurrentText("Older installer")
        page.recommendation_table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        self.assertEqual(
            page.recommendation_proxy.index(0, 1).data(),
            old_installer.name,
        )

        page.category_filter.setCurrentText("Verified duplicates")
        page.recommendation_table.sortByColumn(4, Qt.SortOrder.DescendingOrder)
        self.assertEqual(
            page.recommendation_proxy.index(0, 4).data(),
            "600 B",
        )

    def test_global_interaction_feedback_styles_and_cursors(self) -> None:
        window = MainWindow()
        self.windows.append(window)
        styles = window.styleSheet()
        self.assertIn("QPushButton:enabled:hover", styles)
        self.assertIn("QPushButton:enabled:pressed", styles)
        self.assertIn("QPushButton:focus", styles)
        self.assertIn("QPushButton:disabled", styles)
        self.assertIn(
            "QTableView#cleanupRecommendationsTable::item:hover",
            styles,
        )
        self.assertEqual(
            window.dashboard.scan_button.cursor().shape(),
            Qt.CursorShape.PointingHandCursor,
        )
        self.assertEqual(
            window.cleanup.large_file_plus_button.cursor().shape(),
            Qt.CursorShape.PointingHandCursor,
        )
        self.assertEqual(
            window.duplicates.copy_path_button.cursor().shape(),
            Qt.CursorShape.ArrowCursor,
        )
        window.close()


if __name__ == "__main__":
    unittest.main()
