"""Dashboard screen and scan result visualizations."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.scanner import ScanResult
from ui.formatting import format_size


PALETTE = {
    "Videos": "#6268e8",
    "Images": "#3ecb86",
    "Documents": "#f5bb37",
    "Audio": "#ef6aa3",
    "Archives": "#ff824d",
    "Code": "#48aee8",
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


class DonutChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.values: dict[str, int] = {}
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_values(self, values: dict[str, int]) -> None:
        self.values = values
        self.update()

    def paintEvent(self, _: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width() - 36, self.height() - 24, 174)
        rect = self.rect().adjusted(18, 10, -18, -10)
        center_x = rect.center().x()
        center_y = rect.center().y()
        chart = rect.__class__(center_x - side // 2, center_y - side // 2, side, side)

        if not self.values or sum(self.values.values()) == 0:
            pen = QPen(QColor("#e7e9ed"), 22)
            painter.setPen(pen)
            painter.drawArc(chart, 0, 360 * 16)
            painter.setPen(QColor("#737b87"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(chart, Qt.AlignmentFlag.AlignCenter, "No scan data")
            return

        start = 90 * 16
        total = sum(self.values.values())
        for category, value in self.values.items():
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

    def __init__(self) -> None:
        super().__init__()
        self.result: ScanResult | None = None
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
        self.select_button = QPushButton("Select Folder")
        self.select_button.setObjectName("secondaryButton")
        self.select_button.clicked.connect(self.select_folder_requested.emit)
        self.scan_button = QPushButton("Scan Folder")
        self.scan_button.setObjectName("primaryButton")
        self.scan_button.clicked.connect(self.scan_requested.emit)
        header.addWidget(self.select_button)
        header.addWidget(self.scan_button)
        outer.addLayout(header)

        self.folder_label = QLabel("Selected folder: none")
        self.folder_label.setObjectName("mutedText")
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.folder_label)
        self.last_scan_label = QLabel()
        self.last_scan_label.setObjectName("mutedText")
        self.last_scan_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.last_scan_label.hide()
        outer.addWidget(self.last_scan_label)

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
        category_content.addWidget(self.chart, 1)
        self.legend = QVBoxLayout()
        self.legend.setSpacing(9)
        category_content.addLayout(self.legend, 1)
        category_body.addLayout(category_content, 1)

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
        self.largest_table.horizontalHeader().setStretchLastSection(True)
        self.largest_table.setColumnWidth(0, 145)
        self.largest_table.setColumnWidth(1, 72)
        self.largest_table.setMinimumHeight(245)
        self.largest_card.layout().addWidget(self.largest_table, 1)
        result_layout.addWidget(self.category_card, 0, 0)
        result_layout.addWidget(self.largest_card, 0, 1)
        result_layout.setColumnStretch(0, 1)
        result_layout.setColumnStretch(1, 1)
        content_layout.addWidget(self.results_area)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.content)
        outer.addWidget(scroll, 1)

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

    def show_empty_state(self) -> None:
        self.empty_state.show()
        self.results_area.hide()

    def set_folder(self, folder: str) -> None:
        self.folder_label.setText(f"Selected folder: {folder}")

    def set_scanning(self, scanning: bool) -> None:
        self.scan_button.setEnabled(not scanning)
        self.select_button.setEnabled(not scanning)
        self.scan_button.setText("Scanning…" if scanning else "Scan Folder")

    def set_result(self, result: ScanResult) -> None:
        self.result = result
        self.empty_state.hide()
        self.results_area.show()
        self.last_scan_label.setText(f"Last scan: {result.root_path}")
        self.last_scan_label.show()
        self.stats[0].set_value(f"{len(result.files):,}")
        self.stats[1].set_value(format_size(result.total_size))
        self.stats[2].set_value(str(len(result.category_sizes)))
        self.stats[3].set_value(f"{result.elapsed_seconds:.2f} s")

        self.chart.set_values(result.category_sizes)
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if result.category_sizes:
            for category, size in sorted(result.category_sizes.items(), key=lambda item: item[1], reverse=True):
                line = QHBoxLayout()
                dot = QLabel("●")
                dot.setStyleSheet(f"color: {PALETTE[category]};")
                name = QLabel(category)
                name.setObjectName("legendName")
                amount = QLabel(format_size(size))
                amount.setObjectName("mutedText")
                amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                line.addWidget(dot)
                line.addWidget(name)
                line.addStretch()
                line.addWidget(amount)
                holder = QWidget()
                holder.setLayout(line)
                self.legend.addWidget(holder)
        else:
            empty = QLabel("No files found in this folder.")
            empty.setObjectName("mutedText")
            self.legend.addWidget(empty)
        self.legend.addStretch()

        largest = sorted(result.files, key=lambda file: file.size, reverse=True)[:10]
        self.largest_table.setRowCount(len(largest))
        for row, file in enumerate(largest):
            for column, text in enumerate((file.name, format_size(file.size), file.path)):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.largest_table.setItem(row, column, item)

        if result.inaccessible_items:
            self.largest_hint.setText(
                f"The 10 largest files in this folder. {result.inaccessible_items} inaccessible item(s) skipped."
            )
        else:
            self.largest_hint.setText("The 10 largest files in this folder.")
