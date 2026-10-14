import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.activity_panel import ActivityPanel


class ActivityPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_elapsed_timer_updates_and_cancel_is_connected(self) -> None:
        panel = ActivityPanel("Cancel Scan")
        cancelled: list[bool] = []
        panel.cancel_requested.connect(lambda: cancelled.append(True))
        panel.start("Analyzing your storage", "Files scanned so far: 0")
        self.assertEqual(panel.progress_bar.minimum(), 0)
        self.assertEqual(panel.progress_bar.maximum(), 0)

        QTest.qWait(1050)
        self.app.processEvents()

        self.assertNotEqual(panel.elapsed_label.text(), "Elapsed: 0.0 s")
        panel.cancel_button.click()
        self.assertEqual(cancelled, [True])
        panel.stop()
        self.assertFalse(panel.refresh_timer.isActive())


if __name__ == "__main__":
    unittest.main()
