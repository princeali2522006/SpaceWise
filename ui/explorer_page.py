"""Scanned-file explorer with category filtering and sorting."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.scanner import ScanResult, ScannedFile
from ui.formatting import format_size


class FileTableItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        own_size = self.data(Qt.ItemDataRole.UserRole)
        other_size = other.data(Qt.ItemDataRole.UserRole)
        if own_size is not None and other_size is not None:
            return int(own_size) < int(other_size)
        return super().__lt__(other)


class ExplorerPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.files: tuple[ScannedFile, ...] = ()
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
        self.filter_combo = QComboBox()
        self.filter_combo.setMinimumWidth(160)
        self.filter_combo.currentTextChanged.connect(self._populate_table)
        header.addWidget(self.filter_combo)
        layout.addLayout(header)

        self.path_label = QLabel("No scan yet — choose a folder on the Dashboard and scan it.")
        self.path_label.setObjectName("pathBanner")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Filename", "Size", "Category", "Full path"])
        self.table.setObjectName("fileTable")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 210)
        self.table.setColumnWidth(1, 95)
        self.table.setColumnWidth(2, 120)
        layout.addWidget(self.table, 1)
        self._show_empty()

    def _show_empty(self) -> None:
        self.table.setRowCount(0)
        self.table.setEnabled(False)
        self.path_label.setText("No scan yet — choose a folder on the Dashboard and scan it.")

    def set_result(self, result: ScanResult | None) -> None:
        if result is None:
            self.files = ()
            self.filter_combo.blockSignals(True)
            self.filter_combo.clear()
            self.filter_combo.addItem("All categories")
            self.filter_combo.blockSignals(False)
            self._show_empty()
            return

        self.files = result.files
        self.path_label.setText(result.root_path)
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem("All categories")
        self.filter_combo.addItems(sorted({file.category for file in result.files}))
        self.filter_combo.blockSignals(False)
        self.table.setEnabled(True)
        self._populate_table()

    def _populate_table(self, *_: str) -> None:
        selected_category = self.filter_combo.currentText()
        files = self.files
        if selected_category and selected_category != "All categories":
            files = tuple(file for file in files if file.category == selected_category)

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(files))
        for row, file in enumerate(files):
            values = (file.name, format_size(file.size), file.category, file.path)
            for column, text in enumerate(values):
                item = FileTableItem(text) if column == 1 else QTableWidgetItem(text)
                item.setToolTip(text)
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, file.size)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
