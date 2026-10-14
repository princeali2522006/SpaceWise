"""Scanned-file explorer with searchable, sortable, model-backed results."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QSortFilterProxyModel, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.scanner import ScanProgress, ScanResult, ScannedFile
from ui.activity_panel import ActivityPanel
from ui.formatting import format_size


HEADERS = ("Filename", "Size", "Category", "Full path")


class ScannedFilesModel(QAbstractTableModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.files: tuple[ScannedFile, ...] = ()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.files)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return HEADERS[section]
        return None

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | int | None:
        if not index.isValid() or not 0 <= index.row() < len(self.files):
            return None
        file = self.files[index.row()]
        values = (file.name, format_size(file.size), file.category, file.path)
        if role == Qt.ItemDataRole.DisplayRole:
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return file.path
        if role == Qt.ItemDataRole.UserRole:
            if index.column() == 1:
                return file.size
            return values[index.column()].casefold()
        return None

    def file_at(self, row: int) -> ScannedFile:
        return self.files[row]

    def set_files(self, files: tuple[ScannedFile, ...]) -> None:
        self.beginResetModel()
        self.files = files
        self.endResetModel()


class ScannedFilesProxyModel(QSortFilterProxyModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.category = ""
        self.query = ""
        self.setSortRole(Qt.ItemDataRole.UserRole)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if source_parent.isValid():
            return False
        source = self.sourceModel()
        if not isinstance(source, ScannedFilesModel):
            return False
        file = source.file_at(source_row)
        return (not self.category or file.category == self.category) and (
            not self.query or self.query in file.name.casefold()
        )

    def set_filter(self, category: str, query: str) -> None:
        begin_change = getattr(self, "beginFilterChange", None)
        if begin_change is not None:
            begin_change()
            self.category = "" if category == "All categories" else category
            self.query = query.strip().casefold()
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:
            self.category = "" if category == "All categories" else category
            self.query = query.strip().casefold()
            self.invalidateRowsFilter()


class ExplorerPage(QWidget):
    cancel_scan_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.files: tuple[ScannedFile, ...] = ()
        self.last_scan_root: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title_stack = QVBoxLayout()
        title_stack.setSpacing(3)
        title = QLabel("Explorer")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Browse the files from your most recent scan.")
        subtitle.setObjectName("mutedText")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack)
        header.addStretch()
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Search filenames…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(230)
        self.filter_combo = QComboBox()
        self.filter_combo.setMinimumWidth(160)
        header.addWidget(self.search_input)
        header.addWidget(self.filter_combo)
        layout.addLayout(header)

        self.path_label = QLabel("No scan yet — choose a folder on the Dashboard and scan it.")
        self.path_label.setObjectName("pathBanner")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)
        self.scan_status_label = QLabel()
        self.scan_status_label.setObjectName("scanStatus")
        self.scan_status_label.hide()
        layout.addWidget(self.scan_status_label)
        self.activity_panel = ActivityPanel("Cancel Scan")
        self.activity_panel.cancel_requested.connect(self.cancel_scan_requested.emit)
        self.cancel_scan_button = self.activity_panel.cancel_button
        self.progress_label = self.activity_panel.details_label
        layout.addWidget(self.activity_panel)

        self.file_model = ScannedFilesModel(self)
        self.proxy_model = ScannedFilesProxyModel(self)
        self.proxy_model.setSourceModel(self.file_model)
        self.table = QTableView()
        self.table.setModel(self.proxy_model)
        self.table.setObjectName("fileTable")
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 310)
        self.table.setColumnWidth(1, 105)
        self.table.setColumnWidth(2, 130)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        layout.addWidget(self.table, 1)

        self.search_input.textChanged.connect(self._populate_table)
        self.filter_combo.currentTextChanged.connect(self._populate_table)
        self._show_empty()

    def _show_empty(self) -> None:
        self.file_model.set_files(())
        self.table.setEnabled(False)
        self.last_scan_root = None
        self.path_label.setText("No scan yet — choose a folder on the Dashboard and scan it.")

    def set_result(self, result: ScanResult | None) -> None:
        if result is None:
            self.files = ()
            self.last_scan_root = None
            self.filter_combo.blockSignals(True)
            self.filter_combo.clear()
            self.filter_combo.addItem("All categories")
            self.filter_combo.blockSignals(False)
            self.search_input.clear()
            self.scan_status_label.clear()
            self.scan_status_label.hide()
            self.activity_panel.stop()
            self._show_empty()
            return

        previous_category = self.filter_combo.currentText()
        self.files = result.files
        self.last_scan_root = result.root_path
        self.path_label.setText(f"Latest completed scan: {result.display_scope}")
        self.path_label.setToolTip("\n".join(result.scan_roots))
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem("All categories")
        self.filter_combo.addItems(sorted({file.category for file in result.files}))
        previous_index = self.filter_combo.findText(previous_category)
        if previous_index >= 0:
            self.filter_combo.setCurrentIndex(previous_index)
        self.filter_combo.blockSignals(False)
        self.scan_status_label.clear()
        self.scan_status_label.hide()
        self.activity_panel.stop()
        self.file_model.set_files(result.files)
        self.table.setEnabled(True)
        self._populate_table()
        if result.inaccessible_items or result.skipped_directories:
            self.scan_status_label.setText(
                f"Partial scan: {result.inaccessible_directories:,} inaccessible folder(s), "
                f"{result.inaccessible_items:,} inaccessible item(s), "
                f"{result.skipped_directories:,} linked, reparse-point, or other-volume folder(s) skipped."
            )
            self.scan_status_label.show()

    def set_scanning(self) -> None:
        self.scan_status_label.setText(
            "Analyzing your storage — the last completed results remain available until this scan finishes."
            if self.files
            else "Analyzing your storage — no completed scan yet."
        )
        self.scan_status_label.show()
        self.activity_panel.start("Analyzing your storage", "Files scanned so far: 0")

    def set_scan_failed(self, folder: str, message: str) -> None:
        self.activity_panel.stop()
        self.scan_status_label.setText(
            f"Scan failed for {folder}: {message} Previous completed results remain displayed."
        )
        self.scan_status_label.show()

    def set_scan_progress(self, progress: ScanProgress) -> None:
        self.activity_panel.set_details(
            f"Files scanned so far: {progress.files_scanned:,}"
        )

    def set_scan_cancelled(self, scope: str, message: str) -> None:
        self.activity_panel.stop()
        self.scan_status_label.setText(
            f"Scan cancelled: {scope}. {message} The last completed scan remains displayed."
        )
        self.scan_status_label.show()

    def set_category_filter(self, category: str) -> None:
        index = self.filter_combo.findText(category)
        if index >= 0:
            self.filter_combo.setCurrentIndex(index)

    def set_cancelling(self) -> None:
        self.activity_panel.set_cancelling()

    def _populate_table(self, *_: str) -> None:
        self.proxy_model.set_filter(
            self.filter_combo.currentText(),
            self.search_input.text(),
        )
