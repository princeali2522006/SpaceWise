"""Scanned-file explorer with searchable, sortable, model-backed results."""

import os
import stat
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPoint,
    QProcess,
    QTimer,
    Qt,
    QSortFilterProxyModel,
    Signal,
    QUrl,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.discovery import discover_user_folders
from core.scanner import ScanProgress, ScanResult, ScannedFile
from ui.activity_panel import ActivityPanel
from ui.formatting import format_scan_time, format_size


HEADERS = ("Filename", "Size", "Category", "Full path")


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


_FILTER_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="spacewise-filter")


def _filter_file_rows(
    files: tuple[ScannedFile, ...],
    personal_roots: tuple[str, ...],
    category: str,
    query: str,
    personal_only: bool,
    sort_column: int,
    descending: bool,
    cancel_event: Event,
) -> tuple[int, ...] | None:
    folded_query = query.casefold()
    if personal_only and not personal_roots:
        personal_roots = discover_user_folders()
    normalized_roots = tuple(_normalized_path(root) for root in personal_roots)
    root_prefixes = tuple(
        root if root.endswith(os.sep) else root + os.sep for root in normalized_roots
    )
    matching_rows: list[int] = []
    for row, file in enumerate(files):
        if row % 1024 == 0 and cancel_event.is_set():
            return None
        if category and file.category != category:
            continue
        if folded_query and folded_query not in file.name.casefold():
            continue
        if personal_only:
            path = _normalized_path(file.path)
            if not any(
                path == root or path.startswith(prefix)
                for root, prefix in zip(normalized_roots, root_prefixes)
            ):
                continue
        matching_rows.append(row)

    sort_keys = (
        lambda file: file.name.casefold(),
        lambda file: file.size,
        lambda file: file.category.casefold(),
        lambda file: file.path.casefold(),
    )
    matching_rows.sort(
        key=lambda row: sort_keys[sort_column](files[row]),
        reverse=descending,
    )
    return tuple(matching_rows)


class ScannedFilesModel(QAbstractTableModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.files: tuple[ScannedFile, ...] = ()
        self.visible_rows: Sequence[int] = range(0)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.visible_rows)

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
        if not index.isValid() or not 0 <= index.row() < len(self.visible_rows):
            return None
        file = self.file_at(index.row())
        values = (file.name, format_size(file.size), file.category, file.path)
        if role == Qt.ItemDataRole.DisplayRole:
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == 0:
                return f"{file.name}\n{file.path}"
            if index.column() == 1:
                return f"{file.size:,} bytes"
            return file.path
        if role == Qt.ItemDataRole.UserRole:
            if index.column() == 1:
                return file.size
            return values[index.column()].casefold()
        return None

    def file_at(self, row: int) -> ScannedFile:
        return self.files[self.visible_rows[row]]

    def set_files(self, files: tuple[ScannedFile, ...]) -> None:
        self.beginResetModel()
        self.files = files
        self.visible_rows = range(len(files))
        self.endResetModel()

    def set_visible_rows(self, rows: Sequence[int]) -> None:
        self.beginResetModel()
        self.visible_rows = rows
        self.endResetModel()


class ScannedFilesProxyModel(QSortFilterProxyModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSortRole(Qt.ItemDataRole.UserRole)


class ExplorerPage(QWidget):
    cancel_scan_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.files: tuple[ScannedFile, ...] = ()
        self.last_scan_root: str | None = None
        self._personal_roots: tuple[str, ...] = ()
        self._filter_generation = 0
        self._applied_filter_generation = 0
        self._filter_cancel_event: Event | None = None
        self._filter_future: Future[tuple[int, ...] | None] | None = None
        self._filter_future_generation = 0
        self._search_confirmation_pending = False
        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
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
        self.file_scope_combo = QComboBox()
        self.file_scope_combo.addItems(("All files", "Personal files"))
        self.file_scope_combo.setMinimumWidth(145)
        header.addWidget(self.search_input)
        header.addWidget(self.filter_combo)
        header.addWidget(self.file_scope_combo)
        layout.addLayout(header)

        self.path_label = QLabel("No scan yet — choose a folder on the Dashboard and scan it.")
        self.path_label.setObjectName("pathBanner")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)
        self.search_feedback_label = QLabel()
        self.search_feedback_label.setObjectName("mutedText")
        self.search_feedback_label.setAccessibleName("Search result count")
        self.search_feedback_label.hide()
        layout.addWidget(self.search_feedback_label)
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
        self._filter_timer = QTimer(self)
        self._filter_timer.setInterval(16)
        self._filter_timer.timeout.connect(self._poll_filter)
        self.table = QTableView()
        self.table.setModel(self.proxy_model)
        self.table.setObjectName("fileTable")
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(False)
        self.table.setShowGrid(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 270)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 125)
        self.table.horizontalHeader().setSortIndicator(0, self._sort_order)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().sectionClicked.connect(self._sort_by_column)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.table, 1)

        self.search_input.textChanged.connect(self._populate_table)
        self.search_input.returnPressed.connect(self._handle_search_return)
        self.filter_combo.currentTextChanged.connect(self._populate_table)
        self.file_scope_combo.currentTextChanged.connect(self._populate_table)
        self._show_empty()

    def _show_empty(self) -> None:
        self.file_model.set_files(())
        self.table.setEnabled(False)
        self.last_scan_root = None
        self.path_label.setText("No scan yet — choose a folder on the Dashboard and scan it.")

    def set_result(
        self,
        result: ScanResult | None,
        *,
        historical: bool = False,
        saved_at: str | None = None,
    ) -> None:
        if result is None:
            self.files = ()
            self.last_scan_root = None
            self.filter_combo.blockSignals(True)
            self.filter_combo.clear()
            self.filter_combo.addItem("All categories")
            self.filter_combo.blockSignals(False)
            self.file_scope_combo.setCurrentIndex(0)
            self.search_input.clear()
            self.search_feedback_label.clear()
            self.search_feedback_label.hide()
            self._search_confirmation_pending = False
            self.scan_status_label.clear()
            self.scan_status_label.hide()
            self.activity_panel.stop()
            self._show_empty()
            return

        previous_category = self.filter_combo.currentText()
        self.files = result.files
        self.last_scan_root = result.root_path
        if historical:
            timestamp = (
                f" · saved {format_scan_time(saved_at)}" if saved_at is not None else ""
            )
            self.path_label.setText(f"Viewing Saved Scan{timestamp}: {result.display_scope}")
        else:
            self.path_label.setText(
                f"Latest completed scan · {result.scanner_backend}: {result.display_scope}"
            )
        self.path_label.setToolTip("\n".join(result.scan_roots))
        self.search_feedback_label.clear()
        self.search_feedback_label.hide()
        self._search_confirmation_pending = False
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem("All categories")
        self.filter_combo.addItems(category.name for category in result.categories)
        previous_index = self.filter_combo.findText(previous_category)
        if previous_index >= 0:
            self.filter_combo.setCurrentIndex(previous_index)
        self.filter_combo.blockSignals(False)
        self.scan_status_label.clear()
        self.scan_status_label.hide()
        self.activity_panel.stop()
        self.file_model.set_files(result.files)
        self._personal_roots = ()
        self.table.setEnabled(True)
        self._populate_table()
        if (
            result.inaccessible_items
            or result.skipped_directories
            or result.skipped_files
            or result.hard_link_paths
        ):
            notes: list[str] = []
            if result.inaccessible_items or result.skipped_directories or result.skipped_files:
                notes.append(
                    f"Partial scan: {result.inaccessible_directories:,} inaccessible folder(s), "
                    f"{result.inaccessible_items:,} inaccessible item(s), "
                    f"{result.skipped_directories:,} linked/reparse/other-volume folder(s), "
                    f"{result.skipped_files:,} linked/reparse file(s) skipped."
                )
            if result.hard_link_paths:
                notes.append(
                    f"Logical file totals include {result.hard_link_paths:,} additional hard-link "
                    "path(s) sharing file contents."
                )
            self.scan_status_label.setText(
                " ".join(notes)
            )
            self.scan_status_label.show()
        else:
            self.scan_status_label.clear()
            self.scan_status_label.hide()

    def set_scanning(self) -> None:
        self.scan_status_label.setText(
            "Scanning… the last completed results remain available until this scan finishes."
            if self.files
            else "Scanning… no completed scan yet."
        )
        self.scan_status_label.show()
        self.activity_panel.start("Scanning…", "Files discovered so far: 0")

    def set_scan_failed(self, folder: str, message: str) -> None:
        self.activity_panel.stop()
        self.scan_status_label.setText(
            f"Scan failed for {folder}: {message} Previous completed results remain displayed."
        )
        self.scan_status_label.show()

    def set_scan_progress(self, progress: ScanProgress) -> None:
        self.activity_panel.status_label.setText(
            f"Scanning · {progress.backend_label}"
        )
        self.activity_panel.set_details(f"Files discovered: {progress.files_scanned:,}")
        self.activity_panel.details_label.setToolTip("")

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

    def _handle_search_return(self) -> None:
        query = self.search_input.text().strip()
        if self._applied_filter_generation == self._filter_generation:
            self._show_search_count(query, self.file_model.rowCount())
            return
        self._search_confirmation_pending = True
        self.search_feedback_label.setText("Updating search results…")
        self.search_feedback_label.show()

    def _show_search_count(self, query: str, count: int) -> None:
        if query:
            noun = "file" if count == 1 else "files"
            self.search_feedback_label.setText(
                f"{count:,} matching {noun} for “{query}”."
            )
            self.search_feedback_label.show()
        else:
            self.search_feedback_label.clear()
            self.search_feedback_label.hide()

    def _populate_table(self, *_: str) -> None:
        self._search_confirmation_pending = False
        self.search_feedback_label.clear()
        self.search_feedback_label.hide()
        self._filter_generation += 1
        generation = self._filter_generation
        if self._filter_cancel_event is not None:
            self._filter_cancel_event.set()
        if self._filter_future is not None:
            self._filter_future.cancel()

        category = self.filter_combo.currentText()
        category = "" if category == "All categories" else category
        query = self.search_input.text().strip()
        personal_only = self.file_scope_combo.currentText() == "Personal files"
        if not self.files:
            self.file_model.set_visible_rows(range(len(self.files)))
            self._applied_filter_generation = generation
            if self._search_confirmation_pending:
                self._show_search_count(
                    self.search_input.text().strip(),
                    self.file_model.rowCount(),
                )
                self._search_confirmation_pending = False
            self._filter_cancel_event = None
            self._filter_future = None
            self._filter_timer.stop()
            return

        cancel_event = Event()
        self._filter_cancel_event = cancel_event
        self._filter_future_generation = generation
        self._filter_future = _FILTER_EXECUTOR.submit(
            _filter_file_rows,
            self.files,
            self._personal_roots,
            category,
            query,
            personal_only,
            self._sort_column,
            self._sort_order == Qt.SortOrder.DescendingOrder,
            cancel_event,
        )
        self._filter_timer.start()

    def _sort_by_column(self, column: int) -> None:
        if column == self._sort_column:
            self._sort_order = (
                Qt.SortOrder.DescendingOrder
                if self._sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self._sort_column = column
            self._sort_order = Qt.SortOrder.AscendingOrder
        self.table.horizontalHeader().setSortIndicator(column, self._sort_order)
        self._populate_table()

    def _poll_filter(self) -> None:
        future = self._filter_future
        if future is None or not future.done():
            return
        generation = self._filter_future_generation
        self._filter_timer.stop()
        self._filter_future = None
        if generation != self._filter_generation:
            return

        rows = future.result()
        self._filter_cancel_event = None
        if rows is not None:
            self.file_model.set_visible_rows(rows)
        self._applied_filter_generation = generation

    def _show_context_menu(self, position: QPoint) -> None:
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        self.table.selectRow(index.row())
        source_index = self.proxy_model.mapToSource(index)
        file = self.file_model.file_at(source_index.row())
        menu = QMenu(self)
        action = menu.addAction("Show in Windows Explorer")
        action.triggered.connect(
            lambda _checked=False, selected_file=file: self.show_in_windows_explorer(selected_file)
        )
        menu.popup(self.table.viewport().mapToGlobal(position))

    def show_in_windows_explorer(self, file: ScannedFile) -> None:
        try:
            file_stat = os.stat(file.path)
        except FileNotFoundError:
            QMessageBox.information(
                self,
                "File unavailable",
                "This file no longer exists at its scanned location.",
            )
            return
        except OSError as error:
            QMessageBox.warning(
                self,
                "File unavailable",
                f"Could not access this file: {error}",
            )
            return

        if not stat.S_ISREG(file_stat.st_mode):
            QMessageBox.warning(
                self,
                "File unavailable",
                "The scanned path is no longer a regular file.",
            )
            return

        started, _process_id = QProcess.startDetached(
            "explorer.exe",
            ["/select,", file.path],
        )
        if started:
            return
        folder = os.path.dirname(file.path)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
            QMessageBox.warning(
                self,
                "Could not open folder",
                "Windows Explorer could not be started for this location.",
            )
