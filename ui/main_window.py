"""Main application window and sidebar navigation."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.discovery import discover_local_drives, discover_user_folders, refresh_drive_usage
from core.duplicates import DuplicateAnalysis, DuplicateProgress
from core.recommendations import CleanupRecommendations, RecommendationProgress
from core.scanner import DriveUsage, ScanProgress, ScanResult
from ui.cleanup_page import CleanupPage
from ui.dashboard_page import DashboardPage
from ui.duplicates_page import DuplicatesPage
from ui.explorer_page import ExplorerPage
from ui.formatting import format_size
from ui.placeholder_page import PlaceholderPage
from ui.worker import CleanupWorker, DuplicateWorker, ScanWorker


SECTIONS = (
    ("Dashboard", "⌂"),
    ("Explorer", "▤"),
    ("Duplicates", "▢"),
    ("Cleanup", "♧"),
    ("Settings", "⚙"),
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SpaceWise")
        self.resize(1180, 760)
        self.setMinimumSize(900, 620)
        self.selected_folder: str | None = None
        self.scan_result: ScanResult | None = None
        self.scan_worker: ScanWorker | None = None
        self.duplicate_worker: DuplicateWorker | None = None
        self.cleanup_worker: CleanupWorker | None = None
        self.active_scan_scope: str | None = None
        self._close_after_scan = False
        self.local_drives = discover_local_drives()
        self.nav_buttons: list[QPushButton] = []
        self._build_ui()
        self.dashboard.set_drives(self._drive_options())
        self._apply_styles()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(205)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 18, 14, 16)
        sidebar_layout.setSpacing(7)
        brand = QLabel("▱  SpaceWise")
        brand.setObjectName("brand")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addSpacing(18)

        self.stack = QStackedWidget()
        self.dashboard = DashboardPage()
        self.explorer = ExplorerPage()
        self.duplicates = DuplicatesPage()
        self.cleanup = CleanupPage()
        self.pages = [
            self.dashboard,
            self.explorer,
            self.duplicates,
            self.cleanup,
            PlaceholderPage("Settings", "Application preferences will be available here."),
        ]

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, (label, icon) in enumerate(SECTIONS):
            button = QPushButton(f"{icon}    {label}")
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            sidebar_layout.addWidget(button)
        sidebar_layout.addStretch()
        version = QLabel("SpaceWise  •  Sprint 2")
        version.setObjectName("sidebarFooter")
        sidebar_layout.addWidget(version)

        for page in self.pages:
            self.stack.addWidget(page)
        self.nav_group.idClicked.connect(self.stack.setCurrentIndex)
        self.nav_buttons[0].setChecked(True)
        self.dashboard.select_folder_requested.connect(self.select_folder)
        self.dashboard.scan_requested.connect(self.scan_selected_folder)
        self.dashboard.scan_my_files_requested.connect(self.scan_my_files)
        self.dashboard.scan_drive_requested.connect(self.scan_drive)
        self.dashboard.cancel_scan_requested.connect(self.cancel_scan)
        self.explorer.cancel_scan_requested.connect(self.cancel_scan)
        self.duplicates.find_duplicates_requested.connect(self.find_duplicates)
        self.duplicates.cancel_requested.connect(self.cancel_duplicate_analysis)
        self.cleanup.analyze_requested.connect(self.analyze_cleanup)
        self.cleanup.cancel_requested.connect(self.cancel_cleanup_analysis)
        self.dashboard.open_category_requested.connect(self.open_category_in_explorer)
        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #ffffff;
                color: #17191d;
                font-family: "Segoe UI";
                font-size: 10pt;
            }
            #sidebar { background: #f6f7f9; border-right: 1px solid #e7e9ed; }
            #brand { font-size: 16pt; font-weight: 700; padding: 2px 6px; }
            #sidebarFooter { color: #9aa1ab; font-size: 8pt; padding: 8px 5px; }
            QPushButton#navButton {
                text-align: left; padding: 10px 11px; border: 0; border-radius: 7px;
                color: #434953; background: transparent; font-size: 9.5pt;
            }
            QPushButton#navButton:hover { background: #eceef2; }
            QPushButton#navButton:checked { background: #e7e9ed; color: #17191d; font-weight: 600; }
            #pageTitle { font-size: 20pt; font-weight: 700; }
            #mutedText { color: #747c88; font-size: 9pt; }
            #statCard, #panel, #emptyCard {
                background: #ffffff; border: 1px solid #e7e9ed; border-radius: 10px;
            }
            #statValue { font-size: 18pt; font-weight: 650; }
            #sectionTitle { font-size: 10.5pt; font-weight: 650; }
            #legendName { font-size: 9pt; }
            #emptyCard { background: #fbfbfc; }
            #detailCard { background: #fafbfc; border: 1px solid #eceef1; border-radius: 7px; }
            #scanStatus {
                padding: 9px 12px; background: #f7f8fa; border: 1px solid #e7e9ed;
                border-radius: 7px; color: #525a65;
            }
            QPushButton#categoryButton {
                text-align: left; padding: 5px 7px; border: 0; border-radius: 5px;
                background: transparent; font-size: 9pt;
            }
            QPushButton#categoryButton:hover { background: #f1f2f5; }
            QPushButton#categoryButton:checked { background: #f1f2f5; font-weight: 600; }
            QLineEdit#searchInput {
                padding: 8px 10px; border: 1px solid #dfe2e7; border-radius: 7px;
                background: white; selection-background-color: #e9edf7;
            }
            QLineEdit#searchInput:focus { border-color: #b8bec8; }
            QPushButton#primaryButton, QPushButton#secondaryButton {
                border-radius: 7px; padding: 9px 15px; font-weight: 600;
            }
            QPushButton#primaryButton { background: #17191d; color: white; border: 1px solid #17191d; }
            QPushButton#primaryButton:hover { background: #35383f; }
            QPushButton#primaryButton:disabled { background: #8e9299; border-color: #8e9299; }
            QPushButton#secondaryButton { background: #ffffff; border: 1px solid #dfe2e7; }
            QPushButton#secondaryButton:hover { background: #f6f7f9; }
            QLabel#pathBanner {
                padding: 10px 12px; background: #f7f8fa; border: 1px solid #e7e9ed;
                border-radius: 7px; color: #525a65;
            }
            QProgressBar#scanProgress {
                border: none; border-radius: 2px; background: #eceef2;
            }
            QProgressBar#scanProgress::chunk { background: #202328; border-radius: 2px; }
            QComboBox { padding: 8px 10px; border: 1px solid #dfe2e7; border-radius: 7px; background: white; }
            QTableView, QTableWidget {
                border: 1px solid #e7e9ed; border-radius: 8px; alternate-background-color: #fafbfc;
            }
            QHeaderView::section {
                background: #f7f8fa; color: #616975; border: none;
                border-bottom: 1px solid #e7e9ed; padding: 9px; font-weight: 600;
            }
            QTableView::item, QTableWidget::item { padding: 7px; border-bottom: 1px solid #f0f1f3; }
            QTableView::item:selected, QTableWidget::item:selected {
                background: #e9edf7; color: #17191d;
            }
            #comingSoon { font-size: 19pt; font-weight: 650; }
            QScrollArea { border: none; }
            #analysisProgressPanel {
                background: #f7f8fa; border: 1px solid #e7e9ed; border-radius: 8px;
            }
            """
        )

    def select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select a folder to scan",
            self.selected_folder or "",
        )
        if not folder:
            return
        self.selected_folder = folder
        self.dashboard.set_folder(folder)

    def scan_selected_folder(self) -> None:
        if self.selected_folder is None:
            QMessageBox.information(self, "Select a folder", "Choose a folder before starting a scan.")
            return
        self._start_scan(
            (self.selected_folder,),
            "Selected folder",
            strict_roots=True,
        )

    def scan_my_files(self) -> None:
        folders = discover_user_folders()
        if not folders:
            QMessageBox.information(
                self,
                "No user folders found",
                "Windows did not report any existing Desktop, Downloads, Documents, Pictures, Videos, or Music folders.",
            )
            return
        self._start_scan(
            folders,
            "Scan My Files",
            strict_roots=False,
        )

    def scan_drive(self, drive_path: str) -> None:
        drive = next((item for item in self.local_drives if item.path == drive_path), None)
        if drive is None:
            QMessageBox.warning(self, "Drive unavailable", "The selected local drive is no longer available.")
            return
        try:
            drive_usage = refresh_drive_usage(drive.path)
        except OSError as error:
            QMessageBox.warning(self, "Drive unavailable", f"Could not read drive capacity: {error}")
            return
        self._start_scan(
            (drive.path,),
            f"Drive scan: {drive.path}",
            drive_usage=drive_usage,
            stay_on_volume=True,
            strict_roots=True,
        )

    def _start_scan(
        self,
        folders: tuple[str, ...],
        scope: str,
        *,
        drive_usage: DriveUsage | None = None,
        stay_on_volume: bool = False,
        strict_roots: bool,
    ) -> None:
        if (
            (self.duplicate_worker is not None and self.duplicate_worker.isRunning())
            or (self.cleanup_worker is not None and self.cleanup_worker.isRunning())
        ):
            self.dashboard.set_scan_failed(
                scope,
                "Wait for the current analysis to finish or cancel it before starting a scan.",
            )
            return
        if self.scan_worker is not None and self.scan_worker.isRunning():
            return
        self.active_scan_scope = scope
        self.dashboard.set_scanning(True, scope)
        self.explorer.set_scanning()
        self.scan_worker = ScanWorker(
            folders,
            scan_scope=scope,
            drive_usage=drive_usage,
            stay_on_volume=stay_on_volume,
            strict_roots=strict_roots,
        )
        self.scan_worker.completed.connect(self._scan_completed)
        self.scan_worker.failed.connect(self._scan_failed)
        self.scan_worker.progress.connect(self._scan_progress)
        self.scan_worker.cancelled.connect(self._scan_cancelled)
        self.scan_worker.finished.connect(self._scan_finished)
        self.scan_worker.start()

    def cancel_scan(self) -> None:
        if self.scan_worker is not None and self.scan_worker.isRunning():
            self.scan_worker.cancel()
            self.dashboard.set_cancelling()
            self.dashboard.scan_status_label.setText(
                "Cancelling scan — the last completed results remain displayed."
            )
            self.explorer.scan_status_label.setText(
                "Cancelling scan — the last completed results remain displayed."
            )
            self.explorer.set_cancelling()

    def find_duplicates(self, personal_only: bool) -> None:
        if self.scan_worker is not None and self.scan_worker.isRunning():
            self.duplicates.set_failed("Wait for the active scan to finish first.")
            return
        if self.cleanup_worker is not None and self.cleanup_worker.isRunning():
            self.duplicates.set_failed("Wait for recommendation analysis to finish first.")
            return
        if self.duplicate_worker is not None and self.duplicate_worker.isRunning():
            return
        if self.scan_result is None:
            self.duplicates.set_no_candidates("Complete a scan before searching for duplicates.")
            return

        personal_roots = discover_user_folders() if personal_only else None
        self.duplicates.set_analyzing()
        self.duplicate_worker = DuplicateWorker(
            self.scan_result.files,
            personal_roots=personal_roots,
        )
        self.duplicate_worker.completed.connect(self._duplicates_completed)
        self.duplicate_worker.failed.connect(self.duplicates.set_failed)
        self.duplicate_worker.progress.connect(self._duplicates_progress)
        self.duplicate_worker.cancelled.connect(self.duplicates.set_cancelled)
        self.duplicate_worker.finished.connect(self._duplicates_finished)
        self.duplicate_worker.start()

    def cancel_duplicate_analysis(self) -> None:
        if self.duplicate_worker is not None and self.duplicate_worker.isRunning():
            self.duplicate_worker.cancel()
            self.duplicates.cancel_button.setEnabled(False)
            self.duplicates.cancel_button.setText("Cancelling…")

    def analyze_cleanup(self, age_threshold_days: int, large_file_threshold_bytes: int) -> None:
        if self.scan_worker is not None and self.scan_worker.isRunning():
            self.cleanup.set_failed("Wait for the active scan to finish first.")
            return
        if self.duplicate_worker is not None and self.duplicate_worker.isRunning():
            self.cleanup.set_failed("Wait for duplicate analysis to finish first.")
            return
        if self.cleanup_worker is not None and self.cleanup_worker.isRunning():
            return
        if self.scan_result is None:
            self.cleanup.set_failed("Complete a scan before analyzing recommendations.")
            return

        self.cleanup.set_analyzing()
        self.cleanup_worker = CleanupWorker(
            self.scan_result.files,
            personal_roots=discover_user_folders(),
            age_threshold_days=age_threshold_days,
            large_file_threshold_bytes=large_file_threshold_bytes,
            duplicate_analysis=self.duplicates.analysis,
        )
        self.cleanup_worker.completed.connect(self._cleanup_completed)
        self.cleanup_worker.failed.connect(self.cleanup.set_failed)
        self.cleanup_worker.progress.connect(self._cleanup_progress)
        self.cleanup_worker.cancelled.connect(self.cleanup.set_cancelled)
        self.cleanup_worker.finished.connect(self._cleanup_finished)
        self.cleanup_worker.start()

    def cancel_cleanup_analysis(self) -> None:
        if self.cleanup_worker is not None and self.cleanup_worker.isRunning():
            self.cleanup_worker.cancel()
            self.cleanup.cancel_button.setEnabled(False)
            self.cleanup.cancel_button.setText("Cancelling…")

    def _cleanup_completed(self, result: object) -> None:
        if not isinstance(result, CleanupRecommendations):
            self.cleanup.set_failed("The analyzer returned an unexpected result.")
            return
        self.cleanup.set_result(result)

    def _cleanup_progress(self, progress: object) -> None:
        if isinstance(progress, RecommendationProgress):
            self.cleanup.set_progress(progress)

    def _cleanup_finished(self) -> None:
        self.cleanup_worker = None
        if self._close_after_scan and (
            (self.scan_worker is None or not self.scan_worker.isRunning())
            and (self.duplicate_worker is None or not self.duplicate_worker.isRunning())
        ):
            self._close_after_scan = False
            self.close()

    def _duplicates_completed(self, result: object) -> None:
        if not isinstance(result, DuplicateAnalysis):
            self.duplicates.set_failed("The analyzer returned an unexpected result.")
            return
        self.duplicates.set_result(result)
        if self.scan_result is not None:
            self.cleanup.set_scan_result(self.scan_result)

    def _duplicates_progress(self, progress: object) -> None:
        if isinstance(progress, DuplicateProgress):
            self.duplicates.set_progress(progress)

    def _duplicates_finished(self) -> None:
        self.duplicate_worker = None
        if self._close_after_scan and (
            (self.scan_worker is None or not self.scan_worker.isRunning())
            and (self.cleanup_worker is None or not self.cleanup_worker.isRunning())
        ):
            self._close_after_scan = False
            self.close()

    def _scan_completed(self, result: object) -> None:
        if not isinstance(result, ScanResult):
            QMessageBox.critical(self, "Scan error", "The scanner returned an unexpected result.")
            return
        self.scan_result = result
        self.duplicates.set_scan_result(result)
        self.cleanup.set_scan_result(result)
        self.dashboard.set_result(result)
        self.explorer.set_result(result)

    def _scan_failed(self, message: str) -> None:
        scope = self.active_scan_scope or "requested scan"
        self.dashboard.set_scan_failed(scope, message)
        self.explorer.set_scan_failed(scope, message)
        QMessageBox.warning(self, "Could not scan folder", message)

    def _scan_progress(self, progress: object) -> None:
        if isinstance(progress, ScanProgress):
            self.dashboard.set_scan_progress(progress)
            self.explorer.set_scan_progress(progress)

    def _scan_cancelled(self, message: str) -> None:
        scope = self.active_scan_scope or "requested scan"
        self.dashboard.set_scan_cancelled(scope, message)
        self.explorer.set_scan_cancelled(scope, message)

    def _scan_finished(self) -> None:
        self.dashboard.set_scanning(False)
        self.scan_worker = None
        self.active_scan_scope = None
        if self._close_after_scan and (
            (self.duplicate_worker is None or not self.duplicate_worker.isRunning())
            and (self.cleanup_worker is None or not self.cleanup_worker.isRunning())
        ):
            self._close_after_scan = False
            self.close()

    def _drive_options(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (
                drive.path,
                f"{drive.path} · {format_size(drive.usage.total)} total · "
                f"{format_size(drive.usage.used)} used · {format_size(drive.usage.free)} free",
            )
            for drive in self.local_drives
        )

    def open_category_in_explorer(self, category: str) -> None:
        self.nav_buttons[1].setChecked(True)
        self.stack.setCurrentIndex(1)
        self.explorer.set_category_filter(category)

    def closeEvent(self, event: QCloseEvent) -> None:
        scan_running = self.scan_worker is not None and self.scan_worker.isRunning()
        duplicates_running = (
            self.duplicate_worker is not None and self.duplicate_worker.isRunning()
        )
        cleanup_running = self.cleanup_worker is not None and self.cleanup_worker.isRunning()
        if scan_running or duplicates_running or cleanup_running:
            self._close_after_scan = True
            if scan_running:
                self.scan_worker.cancel()
            if duplicates_running:
                self.duplicate_worker.cancel()
            if cleanup_running:
                self.cleanup_worker.cancel()
            event.ignore()
            return
        super().closeEvent(event)
