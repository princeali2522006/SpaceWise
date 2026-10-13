"""Main application window and sidebar navigation."""

from PySide6.QtCore import Qt
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

from core.scanner import ScanResult
from ui.dashboard_page import DashboardPage
from ui.explorer_page import ExplorerPage
from ui.placeholder_page import PlaceholderPage
from ui.worker import ScanWorker


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
        self.nav_buttons: list[QPushButton] = []
        self._build_ui()
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
        self.pages = [
            self.dashboard,
            self.explorer,
            PlaceholderPage("Duplicates", "Find duplicate files across a selected folder."),
            PlaceholderPage("Cleanup", "Review safe opportunities to free up storage."),
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
        version = QLabel("SpaceWise  •  Sprint 1")
        version.setObjectName("sidebarFooter")
        sidebar_layout.addWidget(version)

        for page in self.pages:
            self.stack.addWidget(page)
        self.nav_group.idClicked.connect(self.stack.setCurrentIndex)
        self.nav_buttons[0].setChecked(True)
        self.dashboard.select_folder_requested.connect(self.select_folder)
        self.dashboard.scan_requested.connect(self.scan_selected_folder)
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
            QComboBox { padding: 8px 10px; border: 1px solid #dfe2e7; border-radius: 7px; background: white; }
            QTableWidget { border: 1px solid #e7e9ed; border-radius: 8px; alternate-background-color: #fafbfc; }
            QHeaderView::section {
                background: #f7f8fa; color: #616975; border: none;
                border-bottom: 1px solid #e7e9ed; padding: 9px; font-weight: 600;
            }
            QTableWidget::item { padding: 7px; border-bottom: 1px solid #f0f1f3; }
            QTableWidget::item:selected { background: #e9edf7; color: #17191d; }
            #comingSoon { font-size: 19pt; font-weight: 650; }
            QScrollArea { border: none; }
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
        if self.scan_worker is not None and self.scan_worker.isRunning():
            return
        self.dashboard.set_scanning(True)
        self.scan_worker = ScanWorker(self.selected_folder)
        self.scan_worker.completed.connect(self._scan_completed)
        self.scan_worker.failed.connect(self._scan_failed)
        self.scan_worker.finished.connect(self._scan_finished)
        self.scan_worker.start()

    def _scan_completed(self, result: object) -> None:
        if not isinstance(result, ScanResult):
            QMessageBox.critical(self, "Scan error", "The scanner returned an unexpected result.")
            return
        self.scan_result = result
        self.dashboard.set_result(result)
        self.explorer.set_result(result)

    def _scan_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Could not scan folder", message)

    def _scan_finished(self) -> None:
        self.dashboard.set_scanning(False)
        self.scan_worker = None

    def closeEvent(self, event: object) -> None:
        if self.scan_worker is not None and self.scan_worker.isRunning():
            self.scan_worker.wait()
        super().closeEvent(event)
