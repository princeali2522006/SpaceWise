"""Browsing and managing saved scans."""

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.history import ScanHistoryStats, ScanHistorySummary
from ui.formatting import format_size


class HistoryPage(QWidget):
    PAGE_SIZE = 50

    open_scan_requested = Signal(int)
    rescan_requested = Signal(int)
    delete_scan_requested = Signal(int)
    refresh_requested = Signal()
    cancel_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(12)

        title = QLabel("Scan History")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Completed scans saved on this device.")
        subtitle.setObjectName("mutedText")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        toolbar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Search scan locations")
        self.search_input.setAccessibleName("Search scan history")
        self.search_input.textChanged.connect(self._search_changed)
        self.search_input.returnPressed.connect(self._confirm_search)
        toolbar.addWidget(self.search_input, 1)
        toolbar.addWidget(QLabel("Sort"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(
            ("Newest first", "Oldest first", "Largest scans", "Most files")
        )
        self.sort_combo.currentIndexChanged.connect(self._sort_changed)
        toolbar.addWidget(self.sort_combo)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        toolbar.addWidget(self.refresh_button)
        layout.addLayout(toolbar)

        self.status_label = QLabel()
        self.status_label.setObjectName("mutedText")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.cancel_button = QPushButton("Cancel loading")
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(9)
        self.scroll_area.setWidget(self.cards_container)
        layout.addWidget(self.scroll_area, 1)

        self.show_more_button = QPushButton("Load next 50 scans")
        self.show_more_button.setObjectName("secondaryButton")
        self.show_more_button.clicked.connect(self._show_more)
        self.show_more_button.hide()
        layout.addWidget(self.show_more_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.empty_label = QLabel("No saved scans yet. Completed scans are saved automatically.")
        self.empty_label.setObjectName("mutedText")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.empty_label, 1)

        self.storage_label = QLabel()
        self.storage_label.setObjectName("mutedText")
        self.storage_label.setToolTip("Database and SQLite sidecar files.")
        layout.addWidget(self.storage_label)

        self.records: tuple[ScanHistorySummary, ...] = ()
        self._visible_limit = self.PAGE_SIZE
        self._search_confirmation = False
        self._render_records()

    def set_busy(self, message: str, *, cancellable: bool = False) -> None:
        self.status_label.setText(message)
        self.refresh_button.setEnabled(False)
        self.search_input.setEnabled(False)
        self.sort_combo.setEnabled(False)
        self.cards_container.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Cancel loading")
        self.cancel_button.setVisible(cancellable)

    def set_records(
        self,
        records: tuple[ScanHistorySummary, ...],
        stats: ScanHistoryStats,
    ) -> None:
        self.records = records
        self._visible_limit = self.PAGE_SIZE
        self._set_idle_controls()
        self._search_confirmation = False
        self._render_records()
        self.storage_label.setText(
            f"{stats.scan_count:,} saved scan(s) · "
            f"History database: {format_size(stats.database_bytes)}"
        )

    def set_idle(self, message: str = "") -> None:
        self._set_idle_controls()
        self.status_label.setText(message)

    def _set_idle_controls(self) -> None:
        self.refresh_button.setEnabled(True)
        self.search_input.setEnabled(True)
        self.sort_combo.setEnabled(True)
        self.cards_container.setEnabled(True)
        self.cancel_button.hide()

    def set_error(self, message: str) -> None:
        self._set_idle_controls()
        self.status_label.setText(f"Could not access scan history: {message}")

    def get_record(self, scan_id: int) -> ScanHistorySummary | None:
        return next((record for record in self.records if record.scan_id == scan_id), None)

    def _search_changed(self, _query: str) -> None:
        self._visible_limit = self.PAGE_SIZE
        self._search_confirmation = False
        self._render_records()

    def _sort_changed(self, _index: int) -> None:
        self._visible_limit = self.PAGE_SIZE
        self._render_records()

    def _show_more(self) -> None:
        self._visible_limit += self.PAGE_SIZE
        self._render_records()

    def _confirm_search(self) -> None:
        self._search_confirmation = True
        self._render_records()

    def _visible_records(self) -> list[ScanHistorySummary]:
        query = self.search_input.text().strip().casefold()
        records = [
            record
            for record in self.records
            if not query
            or query in record.scan_scope.casefold()
            or query in record.root_path.casefold()
            or any(query in root.casefold() for root in record.scan_roots)
        ]
        sort_index = self.sort_combo.currentIndex()
        if sort_index == 1:
            records.sort(key=lambda record: (record.completed_at, record.scan_id))
        elif sort_index == 2:
            records.sort(
                key=lambda record: (record.total_size, record.completed_at),
                reverse=True,
            )
        elif sort_index == 3:
            records.sort(
                key=lambda record: (record.file_count, record.completed_at),
                reverse=True,
            )
        else:
            records.sort(
                key=lambda record: (record.completed_at, record.scan_id),
                reverse=True,
            )
        return records

    @staticmethod
    def _format_date(timestamp: str) -> str:
        try:
            return datetime.fromisoformat(timestamp).astimezone().strftime(
                "%b %d, %Y · %H:%M"
            )
        except ValueError:
            return timestamp

    def _render_records(self, *_args: object) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        records = self._visible_records()
        self.scroll_area.setVisible(bool(records))
        self.empty_label.setVisible(not records)
        visible_records = records[: self._visible_limit]
        self.show_more_button.setVisible(len(records) > len(visible_records))
        if records:
            self.show_more_button.setText(
                f"Load next {min(self.PAGE_SIZE, len(records) - len(visible_records))} scans"
            )
            self.status_label.setText(
                f"{len(records):,} matching scan(s)"
                if self._search_confirmation
                else f"Showing {len(visible_records):,} of {len(records):,} matching scan(s)"
            )
        elif self._search_confirmation:
            self.status_label.setText("0 matching scans")
        else:
            self.status_label.clear()
        if not records and self.records:
            self.empty_label.setText("No saved scans match this search.")
        else:
            self.empty_label.setText(
                "No saved scans yet. Completed scans are saved automatically."
            )
        for record in visible_records:
            card = QFrame()
            card.setObjectName("historyCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 11, 14, 11)
            card_layout.setSpacing(5)

            heading = QHBoxLayout()
            scope = QLabel(record.scan_scope)
            scope.setObjectName("sectionTitle")
            scope.setToolTip(record.scan_scope)
            heading.addWidget(scope, 1)
            heading.addWidget(QLabel(self._format_date(record.completed_at)))
            card_layout.addLayout(heading)

            location = QLabel(record.root_path)
            location.setObjectName("mutedText")
            location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            location.setToolTip("\n".join(record.scan_roots))
            location.setWordWrap(True)
            card_layout.addWidget(location)

            metrics = QLabel(
                f"{record.file_count:,} files · {format_size(record.total_size)} logical size · "
                f"{record.elapsed_seconds:.1f} sec · "
                f"Saved {self._format_date(record.saved_at)}"
            )
            metrics.setObjectName("mutedText")
            card_layout.addWidget(metrics)
            if record.load_error:
                integrity_note = QLabel(record.load_error)
                integrity_note.setObjectName("historyWarning")
                integrity_note.setWordWrap(True)
                card_layout.addWidget(integrity_note)
            coverage = (
                record.inaccessible_items
                + record.skipped_directories
                + record.skipped_files
            )
            if coverage or record.hard_link_paths:
                note = QLabel(
                    f"Coverage notes: {coverage:,} inaccessible or skipped item(s) · "
                    f"{record.hard_link_paths:,} additional hard-link path(s)"
                )
                note.setObjectName("mutedText")
                card_layout.addWidget(note)

            actions = QHBoxLayout()
            actions.addStretch()
            open_button = QPushButton("Open Scan")
            open_button.setObjectName("secondaryButton")
            open_button.setEnabled(record.load_error is None)
            if record.load_error:
                open_button.setToolTip(record.load_error)
            open_button.clicked.connect(
                lambda _checked=False, scan_id=record.scan_id: self.open_scan_requested.emit(
                    scan_id
                )
            )
            rescan_button = QPushButton("Rescan Location")
            rescan_button.setObjectName("secondaryButton")
            rescan_button.clicked.connect(
                lambda _checked=False, scan_id=record.scan_id: self.rescan_requested.emit(
                    scan_id
                )
            )
            delete_button = QPushButton("Remove Scan")
            delete_button.setObjectName("secondaryButton")
            delete_button.clicked.connect(
                lambda _checked=False, scan_id=record.scan_id: self.delete_scan_requested.emit(
                    scan_id
                )
            )
            actions.addWidget(open_button)
            actions.addWidget(rescan_button)
            actions.addWidget(delete_button)
            card_layout.addLayout(actions)
            self.cards_layout.addWidget(card)
        self.cards_layout.addStretch()
