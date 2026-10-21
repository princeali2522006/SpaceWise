import os
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from core.history import HistoryLoadCancelled, ScanHistoryStats, ScanHistoryStore
from core.scanner import scan_folder
from ui.history_page import HistoryPage
from ui.main_window import MainWindow


class ScanHistoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.sample_folder = self.root / "sample"
        self.sample_folder.mkdir()
        (self.sample_folder / "example.txt").write_text("snapshot", encoding="utf-8")
        self.history_path = self.root / "local-app-data" / "history.sqlite3"
        self.history_path_patcher = patch(
            "ui.main_window.default_history_path",
            return_value=str(self.history_path),
        )
        self.history_path_patcher.start()

    def tearDown(self) -> None:
        self.history_path_patcher.stop()
        self.temporary_directory.cleanup()

    def wait_for_workers(self, window: MainWindow, attribute: str) -> None:
        while getattr(window, attribute):
            workers = list(getattr(window, attribute))
            for worker in workers:
                worker.wait()
            for _ in range(5):
                self.app.processEvents()

    def dispose_widget(self, widget: QWidget) -> None:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_completed_scan_is_saved_and_history_opens_read_only_snapshot(self) -> None:
        window = MainWindow()
        window.selected_folder = str(self.sample_folder)
        window.scan_selected_folder()
        window.scan_worker.wait()
        for _ in range(5):
            self.app.processEvents()
        self.wait_for_workers(window, "history_workers")

        self.assertEqual(len(window.history.records), 1)
        record = window.history.records[0]
        self.assertEqual(record.file_count, 1)

        window.open_historical_scan(record.scan_id)
        self.wait_for_workers(window, "history_workers")

        self.assertTrue(window._is_historical_snapshot)
        self.assertEqual(window.dashboard.result.files, window.explorer.files)
        self.assertFalse(window.duplicates.find_button.isEnabled())
        self.assertFalse(window.cleanup.analyze_button.isEnabled())
        self.assertIn("Viewing Saved Scan", window.dashboard.last_scan_label.text())
        self.assertTrue(window.history.search_input.isEnabled())
        self.assertTrue(window.history.sort_combo.isEnabled())
        self.assertFalse(window.saved_scan_banner.isHidden())
        self.assertEqual(
            (self.sample_folder / "example.txt").read_text(encoding="utf-8"),
            "snapshot",
        )

        window.exit_saved_scan()
        self.assertFalse(window._is_historical_snapshot)
        self.assertTrue(window.saved_scan_banner.isHidden())
        self.assertIs(window.scan_result, window._fresh_scan_result)
        self.assertTrue(window.duplicates.find_button.isEnabled())
        window.open_historical_scan(record.scan_id)
        self.wait_for_workers(window, "history_workers")

        with (
            patch("ui.worker.scan_folders", side_effect=OSError("saved location unavailable")),
            patch("ui.main_window.QMessageBox.warning"),
        ):
            window.rescan_historical_scan(record.scan_id)
            window.scan_worker.wait()
            for _ in range(5):
                self.app.processEvents()
        self.assertTrue(window._is_historical_snapshot)
        self.assertFalse(window.duplicates.find_button.isEnabled())
        self.assertFalse(window.cleanup.analyze_button.isEnabled())

        window.rescan_historical_scan(record.scan_id)
        window.scan_worker.wait()
        for _ in range(5):
            self.app.processEvents()
        self.wait_for_workers(window, "history_workers")
        self.assertFalse(window._is_historical_snapshot)
        self.assertEqual(len(window.history.records), 2)

        with patch(
            "ui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            window.delete_historical_scan(record.scan_id)
        self.wait_for_workers(window, "history_workers")
        self.assertEqual(len(window.history.records), 1)
        self.assertTrue((self.sample_folder / "example.txt").exists())
        self.dispose_widget(window)

    def test_history_search_and_sort_update_the_visible_records(self) -> None:
        larger_folder = self.root / "larger"
        larger_folder.mkdir()
        (larger_folder / "large.bin").write_bytes(b"x" * 256)
        store = ScanHistoryStore(self.history_path)
        store.save_scan(scan_folder(self.sample_folder))
        store.save_scan(scan_folder(larger_folder))
        page = HistoryPage()
        page.set_records(store.list_scans(), store.stats())

        page.search_input.setText("larger")
        self.assertEqual(len(page._visible_records()), 1)
        self.assertEqual(page._visible_records()[0].root_path, str(larger_folder.resolve()))
        page.search_input.returnPressed.emit()
        self.assertIn("1 matching scan", page.status_label.text())

        page.search_input.clear()
        page.sort_combo.setCurrentIndex(2)
        self.assertEqual(page._visible_records()[0].root_path, str(larger_folder.resolve()))
        page.search_input.returnPressed.emit()
        self.assertIn("2 matching scan", page.status_label.text())

        template = page._visible_records()[0]
        page.set_records(
            tuple(replace(template, scan_id=index + 1) for index in range(55)),
            ScanHistoryStats(scan_count=55, database_bytes=0),
        )
        self.assertEqual(page.cards_layout.count(), 51)
        self.assertFalse(page.show_more_button.isHidden())
        page.show_more_button.click()
        self.assertEqual(page.cards_layout.count(), 56)
        self.dispose_widget(page)

    def test_exit_saved_scan_without_fresh_result_returns_to_ready_state(self) -> None:
        result = scan_folder(self.sample_folder)
        store = ScanHistoryStore(self.history_path)
        saved = store.save_scan(result)
        window = MainWindow()
        window.history.set_records(store.list_scans(), store.stats())

        window.open_historical_scan(saved.scan_id)
        self.wait_for_workers(window, "history_workers")
        self.assertTrue(window._is_historical_snapshot)
        window.exit_saved_scan()

        self.assertFalse(window._is_historical_snapshot)
        self.assertIsNone(window.scan_result)
        self.assertTrue(window.saved_scan_banner.isHidden())
        self.assertFalse(window.duplicates.find_button.isEnabled())
        self.assertTrue((self.sample_folder / "example.txt").exists())
        self.dispose_widget(window)

    def test_failed_history_load_restores_controls(self) -> None:
        store = ScanHistoryStore(self.history_path)
        saved = store.save_scan(scan_folder(self.sample_folder))
        window = MainWindow()
        window.history.set_records(store.list_scans(), store.stats())

        with patch(
            "ui.worker.ScanHistoryStore.load_scan",
            autospec=True,
            side_effect=sqlite3.DatabaseError("database read failed"),
        ):
            window.open_historical_scan(saved.scan_id)
            self.wait_for_workers(window, "history_workers")

        self.assertTrue(window.history.search_input.isEnabled())
        self.assertTrue(window.history.sort_combo.isEnabled())
        self.assertTrue(window.history.refresh_button.isEnabled())
        self.assertIn("database read failed", window.history.status_label.text())
        self.dispose_widget(window)

    def test_cancelled_history_load_restores_controls(self) -> None:
        store = ScanHistoryStore(self.history_path)
        saved = store.save_scan(scan_folder(self.sample_folder))
        window = MainWindow()
        window.history.set_records(store.list_scans(), store.stats())
        started = threading.Event()

        def wait_for_cancellation(
            _store: ScanHistoryStore,
            _scan_id: int,
            *,
            cancel_event: threading.Event | None = None,
        ) -> None:
            started.set()
            if cancel_event is None:
                raise AssertionError("History loading did not receive a cancellation event.")
            while not cancel_event.wait(0.005):
                continue
            raise HistoryLoadCancelled("Loading the saved scan was cancelled.")

        with patch(
            "ui.worker.ScanHistoryStore.load_scan",
            autospec=True,
            side_effect=wait_for_cancellation,
        ):
            window.open_historical_scan(saved.scan_id)
            self.assertTrue(started.wait(2))
            window.cancel_history_load()
            self.wait_for_workers(window, "history_workers")

        self.assertTrue(window.history.search_input.isEnabled())
        self.assertTrue(window.history.sort_combo.isEnabled())
        self.assertTrue(window.history.refresh_button.isEnabled())
        self.assertIn("cancelled", window.history.status_label.text())
        self.dispose_widget(window)


if __name__ == "__main__":
    unittest.main()
