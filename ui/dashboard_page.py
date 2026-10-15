"""Dashboard screen and scan result visualizations."""

from functools import partial

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPaintEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.scanner import ScanProgress, ScanResult
from ui.activity_panel import ActivityPanel
from ui.formatting import format_size


PALETTE = {
    "Videos": "#6268e8",
    "Images": "#3ecb86",
    "Documents": "#f5bb37",
    "Audio": "#ef6aa3",
    "Archives": "#ff824d",
    "Code": "#48aee8",
    "Installers": "#aa77d4",
    "Virtual Machine Disks": "#cc6c54",
    "Disk Images": "#607d8b",
    "Databases": "#4c9a8a",
    "Other": "#aeb5c0",
}


class StatCard(QFrame):
    def __init__(self, label: str, value: str = "--", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(5)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("statValue")
        self.caption_label = QLabel(label)
        self.caption_label.setObjectName("mutedText")
        layout.addWidget(self.value_label)
        layout.addWidget(self.caption_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class SortableTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        value = self.data(Qt.ItemDataRole.UserRole)
        other_value = other.data(Qt.ItemDataRole.UserRole)
        if value is not None and other_value is not None:
            return value < other_value
        return super().__lt__(other)


class DonutChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.values: dict[str, int] = {}
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_values(self, values: dict[str, int]) -> None:
        self.values = values
        self.update()

    def paintEvent(self, _: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width() - 30, self.height() - 12, 138)
        rect = self.rect().adjusted(18, 10, -18, -10)
        center_x = rect.center().x()
        center_y = rect.center().y()
        chart = rect.__class__(center_x - side // 2, center_y - side // 2, side, side)

        total = sum(self.values.values())
        if not self.values or total == 0:
            pen = QPen(QColor("#e7e9ed"), 22)
            painter.setPen(pen)
            painter.drawArc(chart, 0, 360 * 16)
            if self.values:
                painter.setPen(QColor("#15171a"))
                painter.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
                painter.drawText(chart.adjusted(0, 30, 0, -43), Qt.AlignmentFlag.AlignCenter, "0 B")
                painter.setPen(QColor("#737b87"))
                painter.setFont(QFont("Segoe UI", 8))
                painter.drawText(chart.adjusted(0, 57, 0, -17), Qt.AlignmentFlag.AlignCenter, "Total size")
            else:
                painter.setPen(QColor("#737b87"))
                painter.setFont(QFont("Segoe UI", 9))
                painter.drawText(chart, Qt.AlignmentFlag.AlignCenter, "No scan data")
            return

        start = 90 * 16
        for category, value in self.values.items():
            if value == 0:
                continue
            span = round(value / total * 360 * 16)
            painter.setPen(QPen(QColor(PALETTE[category]), 22, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            painter.drawArc(chart, start, -span)
            start -= span

        painter.setPen(QColor("#15171a"))
        painter.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        painter.drawText(chart.adjusted(0, 48, 0, -64), Qt.AlignmentFlag.AlignCenter, format_size(total))
        painter.setPen(QColor("#737b87"))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(chart.adjusted(0, 76, 0, -36), Qt.AlignmentFlag.AlignCenter, "Total size")


class DashboardPage(QWidget):
    select_folder_requested = Signal()
    scan_requested = Signal()
    scan_my_files_requested = Signal()
    scan_drive_requested = Signal(str)
    cancel_scan_requested = Signal()
    open_category_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.result: ScanResult | None = None
        self.selected_category: str | None = None
        self.category_buttons: dict[str, QPushButton] = {}
        self._dashboard_tables: tuple[QTableWidget, ...] = ()
        self._build_ui()
        self.show_empty_state()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(18)
        header = QHBoxLayout()
        title_stack = QVBoxLayout()
        title_stack.setSpacing(3)
        title = QLabel("Dashboard")
        title.setObjectName("pageTitle")
        subtitle = QLabel("A clear view of your storage and what’s taking up space.")
        subtitle.setObjectName("mutedText")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack)
        header.addStretch()
        self.scan_my_files_button = QPushButton("Scan My Files")
        self.scan_my_files_button.setObjectName("primaryButton")
        self.scan_my_files_button.clicked.connect(self.scan_my_files_requested.emit)
        header.addWidget(self.scan_my_files_button)
        outer.addLayout(header)

        actions = QHBoxLayout()
        self.select_button = QPushButton("Select Folder")
        self.select_button.setObjectName("secondaryButton")
        self.select_button.clicked.connect(self.select_folder_requested.emit)
        self.scan_button = QPushButton("Scan Folder")
        self.scan_button.setObjectName("primaryButton")
        self.scan_button.clicked.connect(self.scan_requested.emit)
        actions.addWidget(self.select_button)
        actions.addWidget(self.scan_button)
        self.drive_combo = QComboBox()
        self.drive_combo.setMinimumWidth(190)
        self.drive_combo.addItem("Select a drive…", None)
        self.drive_combo.currentIndexChanged.connect(self._update_drive_button)
        actions.addWidget(self.drive_combo)
        self.scan_drive_button = QPushButton("Scan Drive")
        self.scan_drive_button.setObjectName("secondaryButton")
        self.scan_drive_button.clicked.connect(self._request_drive_scan)
        actions.addWidget(self.scan_drive_button)
        actions.addStretch()
        outer.addLayout(actions)

        self.folder_label = QLabel("Selected folder: none")
        self.folder_label.setObjectName("mutedText")
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.folder_label)
        self.last_scan_label = QLabel()
        self.last_scan_label.setObjectName("mutedText")
        self.last_scan_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.last_scan_label.hide()
        outer.addWidget(self.last_scan_label)
        self.scan_status_label = QLabel()
        self.scan_status_label.setObjectName("scanStatus")
        self.scan_status_label.hide()
        outer.addWidget(self.scan_status_label)
        self.activity_panel = ActivityPanel("Cancel Scan")
        self.activity_panel.cancel_requested.connect(self.cancel_scan_requested.emit)
        self.cancel_scan_button = self.activity_panel.cancel_button
        self.progress_label = self.activity_panel.details_label
        self.progress_bar = self.activity_panel.progress_bar
        outer.addWidget(self.activity_panel)
        self.drive_usage_label = QLabel()
        self.drive_usage_label.setObjectName("pathBanner")
        self.drive_usage_label.setWordWrap(True)
        self.drive_usage_label.hide()
        outer.addWidget(self.drive_usage_label)

        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)
        self.stats_layout = QGridLayout()
        self.stats_layout.setSpacing(12)
        self.stats = [
            StatCard("Files scanned"),
            StatCard("Total size"),
            StatCard("Categories"),
            StatCard("Scan time"),
        ]
        for index, card in enumerate(self.stats):
            self.stats_layout.addWidget(card, 0, index)
        content_layout.addLayout(self.stats_layout)

        self.empty_state = QFrame()
        self.empty_state.setObjectName("emptyCard")
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(24, 28, 24, 28)
        empty_layout.setSpacing(8)
        empty_title = QLabel("Your storage overview starts here")
        empty_title.setObjectName("sectionTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_message = QLabel("Select a folder, then scan it to see a breakdown of its files.")
        empty_message.setObjectName("mutedText")
        empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch()
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_message)
        empty_layout.addStretch()
        self.empty_state.setMinimumHeight(260)
        content_layout.addWidget(self.empty_state)

        self.results_area = QWidget()
        result_layout = QGridLayout(self.results_area)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setHorizontalSpacing(14)
        result_layout.setVerticalSpacing(12)
        self.category_card = self._make_panel("Storage by category")
        category_body = self.category_card.layout()
        category_content = QHBoxLayout()
        self.chart = DonutChart()
        category_content.addWidget(self.chart, 2)
        self.legend = QVBoxLayout()
        self.legend.setSpacing(3)
        category_content.addLayout(self.legend, 3)
        self.category_note = QLabel(
            "Installers, virtual machine disks, disk images and databases are extension-based "
            "estimates; file contents are not inspected."
        )
        self.category_note.setObjectName("mutedText")
        self.category_note.setWordWrap(True)
        category_body.addLayout(category_content, 1)
        category_body.addWidget(self.category_note)
        self.category_details = QFrame()
        self.category_details.setObjectName("detailCard")
        detail_layout = QVBoxLayout(self.category_details)
        detail_layout.setContentsMargins(12, 9, 12, 10)
        detail_layout.setSpacing(7)
        self.category_detail_title = QLabel("Select a category to see its details.")
        self.category_detail_title.setObjectName("sectionTitle")
        detail_layout.addWidget(self.category_detail_title)
        self.category_detail_summary = QLabel()
        self.category_detail_summary.setObjectName("mutedText")
        detail_layout.addWidget(self.category_detail_summary)
        self.category_largest = QTableWidget(0, 2)
        self.category_largest.setHorizontalHeaderLabels(["Largest files", "Size"])
        self.category_largest.setObjectName("compactTable")
        self.category_largest.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.category_largest.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.category_largest.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.category_largest.setShowGrid(False)
        self.category_largest.verticalHeader().hide()
        self.category_largest.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.category_largest.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.category_largest.setSortingEnabled(True)
        self.category_largest.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.category_largest.horizontalHeader().setStretchLastSection(True)
        self.category_largest.setColumnWidth(0, 195)
        self._fit_table_height(self.category_largest)
        detail_layout.addWidget(self.category_largest)
        self.open_explorer_button = QPushButton("Open category in Explorer")
        self.open_explorer_button.setObjectName("secondaryButton")
        self.open_explorer_button.setEnabled(False)
        self.open_explorer_button.clicked.connect(self._open_selected_category)
        detail_layout.addWidget(self.open_explorer_button, alignment=Qt.AlignmentFlag.AlignRight)
        category_body.addWidget(self.category_details)

        self.largest_card = self._make_panel("Largest files")
        self.largest_hint = QLabel("The 10 largest files in this folder.")
        self.largest_hint.setObjectName("mutedText")
        self.largest_card.layout().addWidget(self.largest_hint)
        self.largest_table = QTableWidget(0, 3)
        self.largest_table.setHorizontalHeaderLabels(["File name", "Size", "Path"])
        self.largest_table.setObjectName("compactTable")
        self.largest_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.largest_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.largest_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.largest_table.setShowGrid(False)
        self.largest_table.verticalHeader().hide()
        self.largest_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.largest_table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.largest_table.setSortingEnabled(True)
        self.largest_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.largest_table.horizontalHeader().setStretchLastSection(True)
        self.largest_table.setColumnWidth(0, 220)
        self.largest_table.setColumnWidth(1, 90)
        self._fit_table_height(self.largest_table)
        self.largest_card.layout().addWidget(self.largest_table)
        result_layout.addWidget(self.category_card, 0, 0)
        result_layout.addWidget(self.largest_card, 0, 1)

        self.subfolder_card = self._make_panel("Largest subfolders")
        self.subfolder_hint = QLabel("Immediate subfolders, including their recursive contents.")
        self.subfolder_hint.setObjectName("mutedText")
        self.subfolder_card.layout().addWidget(self.subfolder_hint)
        self.subfolder_table = QTableWidget(0, 4)
        self.subfolder_table.setHorizontalHeaderLabels(["Folder", "Size", "% of total", "Files"])
        self.subfolder_table.setObjectName("compactTable")
        self.subfolder_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.subfolder_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.subfolder_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.subfolder_table.setShowGrid(False)
        self.subfolder_table.verticalHeader().hide()
        self.subfolder_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.subfolder_table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.subfolder_table.setSortingEnabled(True)
        self.subfolder_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.subfolder_table.horizontalHeader().setStretchLastSection(True)
        self.subfolder_table.setColumnWidth(0, 320)
        self.subfolder_table.setColumnWidth(1, 90)
        self.subfolder_table.setColumnWidth(2, 90)
        self._fit_table_height(self.subfolder_table)
        self.subfolder_card.layout().addWidget(self.subfolder_table)
        result_layout.addWidget(self.subfolder_card, 1, 0, 1, 2)
        result_layout.setColumnStretch(0, 1)
        result_layout.setColumnStretch(1, 1)
        content_layout.addWidget(self.results_area)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.content)
        self.content_scroll_area = scroll
        outer.addWidget(scroll, 1)
        self._dashboard_tables = (
            self.category_largest,
            self.largest_table,
            self.subfolder_table,
        )
        for table in self._dashboard_tables:
            table.viewport().installEventFilter(self)

    @staticmethod
    def _make_panel(title: str) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        return panel

    @staticmethod
    def _fit_table_height(table: QTableWidget) -> None:
        table.resizeRowsToContents()
        header_height = table.horizontalHeader().sizeHint().height()
        rows_height = sum(table.rowHeight(row) for row in range(table.rowCount()))
        table.setFixedHeight(header_height + rows_height + table.frameWidth() * 2 + 2)

    @staticmethod
    def _table_item(text: str, sort_value: str | int | float, tooltip: str) -> QTableWidgetItem:
        item = SortableTableWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, sort_value)
        item.setToolTip(tooltip)
        return item

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.Wheel
            and any(watched is table.viewport() for table in self._dashboard_tables)
            and isinstance(event, QWheelEvent)
        ):
            scrollbar = self.content_scroll_area.verticalScrollBar()
            if scrollbar.maximum() <= scrollbar.minimum():
                return False
            delta = event.pixelDelta().y()
            if not delta:
                delta = round(
                    event.angleDelta().y()
                    / 120
                    * self.fontMetrics().height()
                    * 3
                )
            scrollbar.setValue(scrollbar.value() - delta)
            return True
        return super().eventFilter(watched, event)

    def show_empty_state(self) -> None:
        self.empty_state.show()
        self.results_area.hide()

    def set_folder(self, folder: str) -> None:
        self.folder_label.setText(f"Selected folder: {folder}")

    def set_drives(self, drives: tuple[tuple[str, str], ...]) -> None:
        self.drive_combo.clear()
        self.drive_combo.addItem("Select a drive…", None)
        for path, label in drives:
            self.drive_combo.addItem(label, path)
        self._update_drive_button()

    def _update_drive_button(self, *_: object) -> None:
        self.scan_drive_button.setEnabled(isinstance(self.drive_combo.currentData(), str))

    def _request_drive_scan(self) -> None:
        drive = self.drive_combo.currentData()
        if isinstance(drive, str):
            self.scan_drive_requested.emit(drive)

    def set_scanning(self, scanning: bool, folder: str | None = None) -> None:
        for button in (
            self.scan_button,
            self.select_button,
            self.scan_my_files_button,
        ):
            button.setEnabled(not scanning)
        self.scan_drive_button.setEnabled(
            not scanning and isinstance(self.drive_combo.currentData(), str)
        )
        self.drive_combo.setEnabled(not scanning)
        self.scan_button.setText("Scanning…" if scanning else "Scan Folder")
        if scanning:
            if folder == "Selected folder":
                scope_label = "Selected folder"
            elif folder == "Scan My Files":
                scope_label = "personal folders"
            elif folder and folder.startswith("Drive scan"):
                scope_label = "selected drive"
            else:
                scope_label = "storage"
            if self.result is None:
                status = f"Analyzing your storage · {scope_label} — no completed scan yet."
            else:
                status = (
                    f"Analyzing your storage · {scope_label} — the last completed results remain available "
                    "until this scan finishes."
                )
            self.scan_status_label.setText(status)
            self.scan_status_label.show()
            self.activity_panel.start("Analyzing your storage", "Files scanned so far: 0")
        else:
            self.activity_panel.stop()

    def set_result(self, result: ScanResult) -> None:
        self.result = result
        self.scan_status_label.clear()
        self.scan_status_label.hide()
        self.empty_state.hide()
        self.results_area.show()
        self.last_scan_label.setText(f"Last completed scan: {result.display_scope}")
        self.last_scan_label.setToolTip("\n".join(result.scan_roots))
        self.last_scan_label.show()
        if result.drive_usage is None:
            self.drive_usage_label.clear()
            self.drive_usage_label.hide()
        else:
            usage = result.drive_usage
            self.drive_usage_label.setText(
                f"Drive capacity (Windows): {format_size(usage.total)} total · "
                f"{format_size(usage.used)} used · {format_size(usage.free)} free. "
                "Scanned-file size below counts accessible files only."
            )
            self.drive_usage_label.show()
        self.stats[0].set_value(f"{len(result.files):,}")
        self.stats[1].caption_label.setText(
            "Scanned-file size" if result.drive_usage is not None else "Total size"
        )
        self.stats[1].set_value(format_size(result.total_size))
        self.stats[2].set_value(str(len(result.category_sizes)))
        self.stats[3].set_value(f"{result.elapsed_seconds:.2f} s")

        self.selected_category = None
        self.category_buttons.clear()
        self.chart.set_values(result.category_sizes)
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if result.categories:
            total = result.total_size
            for category in sorted(result.categories, key=lambda item: (-item.size, item.name.casefold())):
                percentage = category.size / total * 100 if total else 0.0
                button = QPushButton(
                    f"●  {category.name}    {format_size(category.size)}    {percentage:.1f}%"
                )
                button.setObjectName("categoryButton")
                button.setStyleSheet(f"color: {PALETTE[category.name]};")
                button.setCheckable(True)
                button.setToolTip(
                    f"{category.file_count:,} file(s) in {category.name}. "
                    "Classification is based on the filename extension only; file contents are not inspected."
                )
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(partial(self.select_category, category.name))
                self.category_buttons[category.name] = button
                self.legend.addWidget(button)
        else:
            empty = QLabel("No files found in this folder.")
            empty.setObjectName("mutedText")
            self.legend.addWidget(empty)
        self.legend.addStretch()

        self.category_largest.setSortingEnabled(False)
        self.category_largest.setRowCount(0)
        self.category_largest.setSortingEnabled(True)
        self._fit_table_height(self.category_largest)
        self.category_detail_title.setText("Select a category to see its details.")
        self.category_detail_summary.clear()
        self.open_explorer_button.setEnabled(False)
        self._populate_subfolders(result)

        largest = result.largest_files
        self.largest_table.setSortingEnabled(False)
        self.largest_table.setRowCount(len(largest))
        for row, file in enumerate(largest):
            self.largest_table.setItem(
                row,
                0,
                self._table_item(file.name, file.name.casefold(), f"{file.name}\n{file.path}"),
            )
            self.largest_table.setItem(
                row,
                1,
                self._table_item(format_size(file.size), file.size, f"{file.size:,} bytes"),
            )
            self.largest_table.setItem(
                row,
                2,
                self._table_item(file.path, file.path.casefold(), file.path),
            )
        self.largest_table.setSortingEnabled(True)
        if largest:
            self.largest_table.sortItems(1, Qt.SortOrder.DescendingOrder)
        self._fit_table_height(self.largest_table)

        self.largest_hint.setText("The 10 largest files in this scan.")
        if result.inaccessible_items or result.skipped_directories:
            self.scan_status_label.setText(
                f"Partial scan: {result.inaccessible_directories:,} inaccessible folder(s), "
                f"{result.inaccessible_items:,} inaccessible item(s), and "
                f"{result.skipped_directories:,} linked, reparse-point, or other-volume folder(s) skipped; "
                "statistics include accessible files only."
            )
            self.scan_status_label.show()
        else:
            self.scan_status_label.clear()
            self.scan_status_label.hide()

    def select_category(self, name: str) -> None:
        if self.result is None:
            return
        summary = next((category for category in self.result.categories if category.name == name), None)
        if summary is None:
            return
        self.selected_category = name
        for category_name, button in self.category_buttons.items():
            button.setChecked(category_name == name)
        self.category_detail_title.setText(name)
        total = self.result.total_size
        percentage = summary.size / total * 100 if total else 0.0
        self.category_detail_summary.setText(
            f"{format_size(summary.size)}  ·  {percentage:.1f}% of scanned storage  ·  {summary.file_count:,} file(s)"
        )

        largest = summary.largest_files
        self.category_largest.setSortingEnabled(False)
        self.category_largest.setRowCount(len(largest))
        for row, file in enumerate(largest):
            name_item = self._table_item(
                file.name,
                file.name.casefold(),
                f"{file.name}\n{file.path}",
            )
            size_item = self._table_item(
                format_size(file.size),
                file.size,
                f"{file.size:,} bytes\n{file.path}",
            )
            self.category_largest.setItem(row, 0, name_item)
            self.category_largest.setItem(row, 1, size_item)
        self.category_largest.setSortingEnabled(True)
        if largest:
            self.category_largest.sortItems(1, Qt.SortOrder.DescendingOrder)
        self._fit_table_height(self.category_largest)
        self.open_explorer_button.setEnabled(True)

    def _open_selected_category(self) -> None:
        if self.selected_category is not None:
            self.open_category_requested.emit(self.selected_category)

    def _populate_subfolders(self, result: ScanResult) -> None:
        visible_folders = result.subfolders[:10]
        self.subfolder_table.setSortingEnabled(False)
        self.subfolder_table.setRowCount(len(visible_folders))
        for row, folder in enumerate(visible_folders):
            percentage = folder.size / result.total_size * 100 if result.total_size else 0.0
            values = (folder.name, format_size(folder.size), f"{percentage:.1f}%", f"{folder.file_count:,}")
            sort_values: tuple[str | int | float, ...] = (
                folder.name.casefold(),
                folder.size,
                percentage,
                folder.file_count,
            )
            for column, (text, sort_value) in enumerate(zip(values, sort_values)):
                self.subfolder_table.setItem(
                    row,
                    column,
                    self._table_item(text, sort_value, folder.path),
                )
        self.subfolder_table.setSortingEnabled(True)
        if visible_folders:
            self.subfolder_table.sortItems(1, Qt.SortOrder.DescendingOrder)
        self._fit_table_height(self.subfolder_table)
        if result.subfolders:
            shown = min(10, len(result.subfolders))
            self.subfolder_hint.setText(
                f"Top {shown} of {result.subfolder_count:,} immediate subfolders by recursive size; "
                "root-level files are not included."
            )
        else:
            self.subfolder_hint.setText("No immediate subfolders containing files were found.")

    def set_scan_failed(self, folder: str, message: str) -> None:
        self.scan_status_label.setText(
            f"Scan failed for {folder}: {message} The last completed scan remains displayed."
        )
        self.scan_status_label.show()

    def set_scan_progress(self, progress: ScanProgress) -> None:
        self.activity_panel.set_details(
            f"Files scanned so far: {progress.files_scanned:,}"
        )

    def set_scan_cancelled(self, folder: str, message: str) -> None:
        self.scan_status_label.setText(
            f"Scan cancelled: {folder}. {message} The last completed scan remains displayed."
        )
        self.scan_status_label.show()

    def set_cancelling(self) -> None:
        self.activity_panel.set_cancelling()
