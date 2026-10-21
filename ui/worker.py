"""Background worker for folder scans."""

import threading
from collections.abc import Sequence
import sqlite3

from PySide6.QtCore import QThread, Signal

from core.history import HistoryLoadCancelled, ScanHistoryStore
from core.recommendations import (
    RecommendationAnalysisCancelled,
    build_cleanup_recommendations,
)
from core.duplicates import (
    DuplicateAnalysis,
    DuplicateAnalysisCancelled,
    analyze_duplicates,
)
from core.scanner import DriveUsage, ScannedFile, ScanCancelledError, ScanResult, scan_folders


class ScanWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
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


class ScanHistoryWorker(QThread):
    completed = Signal(str, object)
    failed = Signal(str, str)
    cancelled = Signal(str, str)

    def __init__(
        self,
        action: str,
        database_path: str,
        *,
        result: ScanResult | None = None,
        scan_id: int | None = None,
    ) -> None:
        super().__init__()
        self.action = action
        self.store = ScanHistoryStore(database_path)
        self.result = result
        self.scan_id = scan_id
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            if self.action == "list":
                payload = (self.store.list_scans(), self.store.stats())
            elif self.action == "save":
                if self.result is None:
                    raise ValueError("No completed scan was provided for history.")
                payload = self.store.save_scan(self.result)
            elif self.action == "load":
                if self.scan_id is None:
                    raise ValueError("No saved scan was selected.")
                payload = (
                    self.scan_id,
                    self.store.load_scan(
                        self.scan_id,
                        cancel_event=self._cancel_event,
                    ),
                )
            elif self.action == "delete":
                if self.scan_id is None:
                    raise ValueError("No saved scan was selected.")
                self.store.delete_scan(self.scan_id)
                payload = (self.store.list_scans(), self.store.stats())
            else:
                raise ValueError(f"Unsupported scan-history operation: {self.action}")
            self.completed.emit(self.action, payload)
        except HistoryLoadCancelled as error:
            self.cancelled.emit(self.action, str(error))
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            OverflowError,
            sqlite3.Error,
        ) as error:
            self.failed.emit(self.action, str(error))


class DuplicateWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal(str)

    def __init__(
        self,
        files: Sequence[ScannedFile],
        *,
        personal_roots: Sequence[str] | None = None,
    ) -> None:
        super().__init__()
        self.files = tuple(files)
        self.personal_roots = tuple(personal_roots) if personal_roots is not None else None
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            result = analyze_duplicates(
                self.files,
                personal_roots=self.personal_roots,
                progress_callback=self.progress.emit,
                cancel_event=self._cancel_event,
            )
            self.completed.emit(result)
        except DuplicateAnalysisCancelled as error:
            self.cancelled.emit(str(error))
        except (OSError, ValueError) as error:
            self.failed.emit(str(error))


class CleanupWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal(str)

    def __init__(
        self,
        files: Sequence[ScannedFile],
        *,
        personal_roots: Sequence[str],
        age_threshold_days: int,
        large_file_threshold_bytes: int,
        duplicate_analysis: DuplicateAnalysis | None,
    ) -> None:
        super().__init__()
        self.files = tuple(files)
        self.personal_roots = tuple(personal_roots)
        self.age_threshold_days = age_threshold_days
        self.large_file_threshold_bytes = large_file_threshold_bytes
        self.duplicate_analysis = duplicate_analysis
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            result = build_cleanup_recommendations(
                self.files,
                personal_roots=self.personal_roots,
                age_threshold_days=self.age_threshold_days,
                large_file_threshold_bytes=self.large_file_threshold_bytes,
                duplicate_analysis=self.duplicate_analysis,
                progress_callback=self.progress.emit,
                cancel_event=self._cancel_event,
            )
            self.completed.emit(result)
        except RecommendationAnalysisCancelled as error:
            self.cancelled.emit(str(error))
        except (OSError, ValueError) as error:
            self.failed.emit(str(error))
