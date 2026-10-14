import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.scanner import scan_folder
from ui.main_window import MainWindow


class CleanupPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_cleanup_action_runs_off_ui_thread_using_latest_scan(self) -> None:
        installer = self.root / "Downloads" / "setup.exe"
        large_file = self.root / "Downloads" / "large.bin"
        installer.parent.mkdir()
        installer.write_bytes(b"old installer")
        large_file.write_bytes(b"large enough")
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=120)).timestamp()
        os.utime(installer, (old_timestamp, old_timestamp))

        window = MainWindow()
        result = scan_folder(self.root)
        window._scan_completed(result)
        with (
            patch("ui.main_window.discover_user_folders", return_value=(str(installer.parent),)),
            patch("core.recommendations._default_protected_roots", return_value=()),
        ):
            window.analyze_cleanup(90, 5)
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
            {str(installer), str(large_file)},
        )
        self.assertIn("does not indicate last use", window.cleanup.recommendation_table.item(0, 6).text())
        self.assertIsNone(window.cleanup_worker)
        window.close()


if __name__ == "__main__":
    unittest.main()
