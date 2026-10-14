import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea

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

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_search_and_category_filter_combine_and_path_is_available(self) -> None:
        explorer = ExplorerPage()
        explorer.set_result(self.result)
        explorer.filter_combo.setCurrentText("Documents")
        explorer.search_input.setText("REPORT")
        model = explorer.table.model()

        self.assertEqual(model.rowCount(), 2)
        visible_paths = {
            model.index(row, 0).data(Qt.ItemDataRole.ToolTipRole)
            for row in range(model.rowCount())
        }
        self.assertEqual(
            visible_paths,
            {str(self.root / "annual-report.txt"), str(self.root / "report.txt")},
        )

    def test_size_and_category_columns_sort(self) -> None:
        explorer = ExplorerPage()
        explorer.set_result(self.result)
        explorer.table.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        model = explorer.table.model()
        self.assertEqual(
            [model.index(row, 1).data(Qt.ItemDataRole.UserRole) for row in range(3)],
            [512, 3, 3],
        )
        explorer.table.sortByColumn(2, Qt.SortOrder.AscendingOrder)
        self.assertEqual(
            [model.index(row, 2).data(Qt.ItemDataRole.DisplayRole) for row in range(3)],
            ["Documents", "Documents", "Images"],
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
        self.assertIn("Analyzing your storage", explorer.scan_status_label.text())
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
        self.assertGreater(
            dashboard.largest_table.height(),
            dashboard.largest_table.horizontalHeader().height() + 9 * 10,
        )

    def test_scan_activity_shows_only_count_and_indeterminate_progress(self) -> None:
        dashboard = DashboardPage()
        dashboard.set_scanning(True, "Selected folder")
        dashboard.set_scan_progress(ScanProgress(files_scanned=42, elapsed_seconds=1.2))

        self.assertEqual(
            dashboard.progress_label.text(),
            "Files scanned so far: 42",
        )
        self.assertEqual(dashboard.progress_bar.minimum(), 0)
        self.assertEqual(dashboard.progress_bar.maximum(), 0)
        self.assertNotIn(str(self.root), dashboard.progress_label.text())
        self.assertIn("Analyzing your storage", dashboard.activity_panel.status_label.text())
        dashboard.set_scanning(False)


if __name__ == "__main__":
    unittest.main()
