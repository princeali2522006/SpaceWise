import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QMenu, QScrollArea, QSizePolicy

from core.scanner import ScanProgress, ScannedFile, scan_folder
from ui.dashboard_page import DashboardPage
from ui.explorer_page import ExplorerPage


class ExplorerAndDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "report.txt").write_text("one", encoding="utf-8")
        (self.root / "annual-report.txt").write_text("two", encoding="utf-8")
        (self.root / "picture.png").write_bytes(b"p" * 512)
        self.result = scan_folder(self.root)
        self.history_path_patcher = patch(
            "ui.main_window.default_history_path",
            return_value=str(self.root / "app-data" / "SpaceWise" / "history.sqlite3"),
        )
        self.history_path_patcher.start()

    def tearDown(self) -> None:
        self.history_path_patcher.stop()
        self.temporary_directory.cleanup()

    def _wait_for_filter(self, explorer: ExplorerPage) -> None:
        deadline = time.monotonic() + 5
        while (
            explorer._applied_filter_generation != explorer._filter_generation
            and time.monotonic() < deadline
        ):
            self.app.processEvents()
            time.sleep(0.001)
        self.assertEqual(
            explorer._applied_filter_generation,
            explorer._filter_generation,
        )

    def test_search_and_category_filter_combine_with_full_name_and_path_tooltips(self) -> None:
        explorer = ExplorerPage()
        explorer.set_result(self.result)
        explorer.filter_combo.setCurrentText("Documents")
        explorer.search_input.setText("REPORT")
        self._wait_for_filter(explorer)
        model = explorer.table.model()

        self.assertEqual(model.rowCount(), 2)
        visible_paths = {
            model.index(row, 3).data(Qt.ItemDataRole.ToolTipRole)
            for row in range(model.rowCount())
        }
        self.assertEqual(
            visible_paths,
            {str(self.root / "annual-report.txt"), str(self.root / "report.txt")},
        )
        for row in range(model.rowCount()):
            filename = model.index(row, 0).data(Qt.ItemDataRole.DisplayRole)
            tooltip = model.index(row, 0).data(Qt.ItemDataRole.ToolTipRole)
            self.assertIn(filename, tooltip)
            self.assertIn(str(self.root / filename), tooltip)

    def test_clearing_search_restores_selected_category(self) -> None:
        explorer = ExplorerPage()
        explorer.set_result(self.result)
        explorer.filter_combo.setCurrentText("Documents")
        explorer.search_input.setText("annual")
        self._wait_for_filter(explorer)

        self.assertEqual(explorer.table.model().rowCount(), 1)
        explorer.search_input.clear()
        self._wait_for_filter(explorer)
        self.assertEqual(explorer.table.model().rowCount(), 2)

    def test_enter_shows_matching_count_without_restarting_live_filter(self) -> None:
        explorer = ExplorerPage()
        explorer.set_result(self.result)
        explorer.search_input.setText("report")
        self._wait_for_filter(explorer)
        generation = explorer._filter_generation

        explorer._handle_search_return()

        self.assertEqual(explorer._filter_generation, generation)
        self.assertIn("2 matching files", explorer.search_feedback_label.text())
        self.assertIn("report", explorer.search_feedback_label.text())
        self.assertFalse(explorer.search_feedback_label.isHidden())

    def test_personal_filter_uses_user_folder_paths_and_combines_with_other_filters(self) -> None:
        personal_root = self.root / "Documents"
        outside_root = self.root / "Archive"
        personal_root.mkdir()
        outside_root.mkdir()
        (personal_root / "annual-report.txt").write_text("personal", encoding="utf-8")
        (outside_root / "annual-report.txt").write_text("outside", encoding="utf-8")
        result = scan_folder(self.root)

        explorer = ExplorerPage()
        explorer.set_result(result)
        explorer._personal_roots = (str(personal_root),)
        explorer.filter_combo.setCurrentText("Documents")
        explorer.search_input.setText("annual-report")
        self._wait_for_filter(explorer)

        self.assertEqual(explorer.table.model().rowCount(), 3)
        explorer.file_scope_combo.setCurrentText("Personal files")
        self._wait_for_filter(explorer)

        model = explorer.table.model()
        self.assertEqual(model.rowCount(), 1)
        source_index = explorer.proxy_model.mapToSource(model.index(0, 0))
        self.assertEqual(
            explorer.file_model.file_at(source_index.row()).path,
            str(personal_root / "annual-report.txt"),
        )
        explorer.search_input.clear()
        self._wait_for_filter(explorer)
        self.assertEqual(model.rowCount(), 1)

    def test_filename_sorting_and_filtering_stay_consistent(self) -> None:
        explorer = ExplorerPage()
        explorer.set_result(self.result)
        self._wait_for_filter(explorer)
        explorer._sort_by_column(0)
        self._wait_for_filter(explorer)
        model = explorer.table.model()
        self.assertEqual(
            [model.index(row, 0).data(Qt.ItemDataRole.DisplayRole) for row in range(3)],
            ["report.txt", "picture.png", "annual-report.txt"],
        )
        explorer.search_input.setText("report")
        self._wait_for_filter(explorer)
        self.assertEqual(
            [model.index(row, 0).data(Qt.ItemDataRole.DisplayRole) for row in range(model.rowCount())],
            ["report.txt", "annual-report.txt"],
        )

    def test_size_sorting_still_works_after_async_filtering(self) -> None:
        files = tuple(
            replace(file, size=100 if file.name == "report.txt" else file.size)
            for file in self.result.files
        )
        explorer = ExplorerPage()
        explorer.set_result(replace(self.result, files=files))
        explorer.filter_combo.setCurrentText("Documents")
        explorer.search_input.setText("report")
        explorer._sort_by_column(1)
        explorer._sort_by_column(1)
        self._wait_for_filter(explorer)

        self.assertEqual(
            [
                explorer.table.model().index(row, 1).data(Qt.ItemDataRole.UserRole)
                for row in range(explorer.table.model().rowCount())
            ],
            [100, 3],
        )

    def test_context_menu_reveals_file_without_executing_it(self) -> None:
        explorer = ExplorerPage()
        explorer.set_result(self.result)
        explorer.resize(1000, 600)
        explorer.show()
        self.app.processEvents()
        index = explorer.table.model().index(0, 0)
        position = explorer.table.visualRect(index).center()
        file = explorer.file_model.file_at(
            explorer.proxy_model.mapToSource(index).row()
        )
        with patch(
            "ui.explorer_page.QProcess.startDetached",
            return_value=(True, 42),
        ) as start_detached:
            with patch.object(
                QMenu,
                "popup",
                lambda menu, *_args: menu.actions()[0].trigger(),
            ):
                explorer._show_context_menu(position)

        start_detached.assert_called_once_with("explorer.exe", ["/select,", file.path])
        explorer.close()

    def test_missing_and_inaccessible_files_are_reported_without_opening_them(self) -> None:
        explorer = ExplorerPage()
        missing = self.root / "missing.txt"
        file = ScannedFile("missing.txt", str(missing), 0, "Documents")
        with patch("ui.explorer_page.QMessageBox.information") as information:
            with patch("ui.explorer_page.QProcess.startDetached") as start_detached:
                explorer.show_in_windows_explorer(file)
        information.assert_called_once()
        start_detached.assert_not_called()

        with patch(
            "ui.explorer_page.os.stat",
            side_effect=PermissionError("access denied"),
        ):
            with patch("ui.explorer_page.QMessageBox.warning") as warning:
                with patch("ui.explorer_page.QProcess.startDetached") as start_detached:
                    explorer.show_in_windows_explorer(file)
        warning.assert_called_once()
        start_detached.assert_not_called()

    def test_size_and_category_columns_sort(self) -> None:
        explorer = ExplorerPage()
        explorer.set_result(self.result)
        explorer._sort_by_column(1)
        explorer._sort_by_column(1)
        self._wait_for_filter(explorer)
        model = explorer.table.model()
        self.assertEqual(
            [model.index(row, 1).data(Qt.ItemDataRole.UserRole) for row in range(3)],
            [512, 3, 3],
        )
        explorer._sort_by_column(2)
        self._wait_for_filter(explorer)
        self.assertEqual(
            [model.index(row, 2).data(Qt.ItemDataRole.DisplayRole) for row in range(3)],
            ["Documents", "Documents", "Images"],
        )

    def test_large_result_set_remains_model_backed_and_searchable(self) -> None:
        files = tuple(
            ScannedFile(
                name=f"report-{index:05}.txt",
                path=str(self.root / f"report-{index:05}.txt"),
                size=index,
                category="Documents" if index % 2 == 0 else "Other",
            )
            for index in range(12_000)
        )
        explorer = ExplorerPage()
        explorer.set_result(replace(self.result, files=files))

        self.assertEqual(explorer.file_model.rowCount(), 12_000)
        self.assertEqual(explorer.table.model().rowCount(), 12_000)
        explorer.filter_combo.setCurrentText("Documents")
        explorer.search_input.setText("report-119")
        self._wait_for_filter(explorer)
        self.assertEqual(explorer.table.model().rowCount(), 50)
        explorer.search_input.setText("report-11998")
        self._wait_for_filter(explorer)
        self.assertEqual(explorer.table.model().rowCount(), 1)
        self.assertEqual(
            explorer.table.model().index(0, 0).data(Qt.ItemDataRole.DisplayRole),
            "report-11998.txt",
        )

    def test_category_detail_and_explorer_navigation_signal(self) -> None:
        dashboard = DashboardPage()
        dashboard.set_result(self.result)
        requested: list[str] = []
        dashboard.open_category_requested.connect(requested.append)

        dashboard.category_buttons["Documents"].click()

        self.assertEqual(dashboard.selected_category, "Documents")
        self.assertIn("2 file(s)", dashboard.category_detail_summary.text())
        self.assertEqual(dashboard.category_largest.rowCount(), 2)
        dashboard.open_explorer_button.click()
        self.assertEqual(requested, ["Documents"])

    def test_scanning_status_keeps_previous_scan_identified(self) -> None:
        explorer = ExplorerPage()
        explorer.set_result(self.result)
        explorer.set_scanning()

        self.assertIn(str(self.root), explorer.path_label.text())
        self.assertNotIn(str(self.root), explorer.scan_status_label.text())
        self.assertIn("Scanning", explorer.scan_status_label.text())
        self.assertIn("last completed results", explorer.scan_status_label.text())

    def test_partial_results_are_labeled_and_zero_byte_categories_stay_visible(self) -> None:
        zero_root = self.root / "zero-only"
        zero_root.mkdir()
        (zero_root / "empty.txt").write_bytes(b"")
        result = scan_folder(zero_root)
        dashboard = DashboardPage()
        dashboard.set_result(result)
        dashboard.category_buttons["Documents"].click()

        self.assertEqual(result.total_size, 0)
        self.assertEqual(result.categories[0].file_count, 1)
        self.assertIn("0.0%", dashboard.category_detail_summary.text())
        self.assertEqual(dashboard.category_largest.rowCount(), 1)

        explorer = ExplorerPage()
        explorer.set_result(replace(self.result, inaccessible_directories=1, inaccessible_items=1))
        self.assertIn("Partial scan", explorer.scan_status_label.text())

    def test_dashboard_tables_expand_instead_of_scrolling_internally(self) -> None:
        dashboard = DashboardPage()
        self.assertEqual(
            dashboard.largest_table.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Fixed,
        )
        self.assertLess(dashboard.largest_table.height(), 90)
        dashboard.set_result(self.result)

        self.assertIsNotNone(dashboard.findChild(QScrollArea))
        self.assertIs(dashboard.findChild(QScrollArea).widget(), dashboard.content)
        tables = (
            dashboard.largest_table,
            dashboard.category_largest,
            dashboard.subfolder_table,
        )
        for table in tables:
            self.assertEqual(
                table.verticalScrollBarPolicy(),
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            self.assertGreaterEqual(
                table.height(),
                table.horizontalHeader().height()
                + sum(table.rowHeight(row) for row in range(table.rowCount())),
            )

        long_files = tuple(
            ScannedFile(name=f"file-{index}.bin", path=f"c:/test/file-{index}.bin", size=index, category="Other")
            for index in range(10)
        )
        long_categories = (replace(self.result.categories[0], largest_files=long_files),)
        long_result = replace(
            self.result,
            largest_files=long_files,
            categories=long_categories,
        )
        dashboard.set_result(long_result)
        dashboard.select_category(long_categories[0].name)
        self.assertEqual(dashboard.largest_table.rowCount(), 10)
        self.assertEqual(dashboard.category_largest.rowCount(), 10)
        self.assertEqual(
            dashboard.largest_table.item(0, 1).data(Qt.ItemDataRole.UserRole),
            9,
        )
        self.assertEqual(
            dashboard.category_largest.item(0, 1).data(Qt.ItemDataRole.UserRole),
            9,
        )
        self.assertEqual(
            dashboard.largest_table.item(0, 2).text(),
            long_files[-1].path,
        )
        self.assertIn(long_files[-1].path, dashboard.largest_table.item(0, 0).toolTip())
        self.assertGreater(
            dashboard.largest_table.height(),
            dashboard.largest_table.horizontalHeader().height() + 9 * 10,
        )

    def test_dashboard_tables_are_resizable_sortable_and_forward_wheel_to_page(self) -> None:
        long_files = tuple(
            ScannedFile(
                name=f"long-file-name-{index}.bin",
                path=str(self.root / f"long-file-name-{index}.bin"),
                size=index,
                category="Other",
            )
            for index in range(10)
        )
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "inside.bin").write_bytes(b"nested")
        dashboard_result = scan_folder(self.root)
        dashboard = DashboardPage()
        dashboard.set_result(
            replace(
                dashboard_result,
                largest_files=long_files,
                categories=(replace(dashboard_result.categories[0], largest_files=long_files),),
            )
        )
        dashboard.resize(980, 620)
        dashboard.show()
        self.app.processEvents()
        for table in (
            dashboard.largest_table,
            dashboard.category_largest,
            dashboard.subfolder_table,
        ):
            self.assertTrue(table.isSortingEnabled())
            self.assertEqual(
                table.horizontalHeader().sectionResizeMode(0),
                table.horizontalHeader().ResizeMode.Interactive,
            )
            original_width = table.columnWidth(0)
            table.setColumnWidth(0, original_width + 10)
            self.assertEqual(table.columnWidth(0), original_width + 10)

        centered = Qt.AlignmentFlag.AlignCenter
        self.assertEqual(
            dashboard.largest_table.horizontalHeader().defaultAlignment(),
            centered,
        )
        self.assertEqual(
            dashboard.largest_table.item(0, 1).textAlignment(),
            int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
        )
        self.assertEqual(
            dashboard.subfolder_table.item(0, 1).textAlignment(),
            int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
        )

        scrollbar = dashboard.content_scroll_area.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        before = scrollbar.value()
        wheel = QWheelEvent(
            QPointF(4, 4),
            QPointF(4, 4),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(dashboard.largest_table.viewport(), wheel)
        self.assertGreater(scrollbar.value(), before)
        dashboard.close()

    def test_main_window_keeps_dashboard_and_explorer_on_the_same_scan_result(self) -> None:
        from ui.main_window import MainWindow

        window = MainWindow()
        window._scan_completed(self.result)

        self.assertIs(window.dashboard.result, self.result)
        self.assertIs(window.explorer.files, self.result.files)
        self.assertEqual(window.explorer.file_model.rowCount(), len(self.result.files))
        while window.history_workers:
            for worker in list(window.history_workers):
                worker.wait()
            self.app.processEvents()
        window.close()

    def test_scan_activity_shows_only_count_and_indeterminate_progress(self) -> None:
        dashboard = DashboardPage()
        dashboard.set_scanning(True, "Selected folder")
        dashboard.set_scan_progress(ScanProgress(files_scanned=42, elapsed_seconds=1.2))

        self.assertEqual(
            dashboard.progress_label.text(),
            "Files discovered: 42",
        )
        self.assertEqual(dashboard.progress_bar.minimum(), 0)
        self.assertEqual(dashboard.progress_bar.maximum(), 0)
        self.assertNotIn(str(self.root), dashboard.progress_label.text())
        self.assertIn("Scanning", dashboard.activity_panel.status_label.text())
        location = self.root / "nested" / "current"
        dashboard.set_scan_progress(
            ScanProgress(
                files_scanned=43,
                elapsed_seconds=1.3,
                current_directory=str(location),
            )
        )
        self.assertEqual(dashboard.progress_label.text(), "Files discovered: 43")
        self.assertNotIn("current", dashboard.progress_label.text())
        self.assertEqual(dashboard.progress_label.toolTip(), "")
        dashboard.set_scanning(False)


if __name__ == "__main__":
    unittest.main()
