"""Background worker for folder scans."""

import threading
from collections.abc import Sequence

from PySide6.QtCore import QThread, Signal

from core.scanner import DriveUsage, ScanCancelledError, scan_folders


class ScanWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(str)
    cancelled = Signal(str)

    def __init__(
        self,
        folders: Sequence[str] | str,
        *,
        scan_scope: str = "Selected folder",
        drive_usage: DriveUsage | None = None,
        stay_on_volume: bool = False,
        strict_roots: bool = True,
    ) -> None:
        super().__init__()
        self.folders = (folders,) if isinstance(folders, str) else tuple(folders)
        self.scan_scope = scan_scope
        self.drive_usage = drive_usage
        self.stay_on_volume = stay_on_volume
        self.strict_roots = strict_roots
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            result = scan_folders(
                self.folders,
                scan_scope=self.scan_scope,
                drive_usage=self.drive_usage,
                stay_on_volume=self.stay_on_volume,
                progress_callback=self.progress.emit,
                cancel_event=self._cancel_event,
                strict_roots=self.strict_roots,
            )
            self.completed.emit(result)
        except ScanCancelledError as error:
            self.cancelled.emit(str(error))
        except (OSError, ValueError) as error:
            self.failed.emit(str(error))
