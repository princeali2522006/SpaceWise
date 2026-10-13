"""Background worker for folder scans."""

from PySide6.QtCore import QThread, Signal

from core.scanner import scan_folder


class ScanWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, folder: str) -> None:
        super().__init__()
        self.folder = folder

    def run(self) -> None:
        try:
            self.completed.emit(scan_folder(self.folder))
        except (OSError, ValueError) as error:
            self.failed.emit(str(error))
