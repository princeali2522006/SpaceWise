"""Read-only duplicate-file analysis page."""

import os
import stat

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QProcess,
    Qt,
    QTimer,
    QSortFilterProxyModel,
    Signal,
    QUrl,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.duplicates import (
    DuplicateAnalysis,
    DuplicateGroup,
    DuplicateProgress,
    UnverifiedFile,
)
from core.scanner import ScanResult
from ui.activity_panel import ActivityPanel
from ui.formatting import format_size


RESULT_HEADERS = (
    "Verification",
    "Representative filename",
    "Verified copies",
    "File size",
    "Estimated savings",
)


def format_duration(seconds: float) -> str:
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} hr {minutes} min {remaining_seconds} sec"
    if minutes:
        return f"{minutes} min {remaining_seconds} sec"
    return f"{remaining_seconds} sec"


class DuplicateResultsModel(QAbstractTableModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.groups: tuple[DuplicateGroup, ...] = ()
        self.unverified_files: tuple[UnverifiedFile, ...] = ()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.groups) + len(self.unverified_files)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(RESULT_HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(RESULT_HEADERS)
        ):
            return RESULT_HEADERS[section]
        return None

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | int | None:
        if not index.isValid() or not 0 <= index.row() < self.rowCount():
            return None

        if index.row() < len(self.groups):
            group = self.groups[index.row()]
            filename = os.path.basename(group.paths[0])
            values = (
                "Verified duplicate group",
                filename,
                f"{group.verified_copy_count:,} distinct copies · {len(group.paths):,} paths",
                format_size(group.size),
                format_size(group.recoverable_bytes),
            )
            sort_values: tuple[str | int, ...] = (
                0,
                filename.casefold(),
                group.verified_copy_count,
                group.size,
                group.recoverable_bytes,
            )
            tooltip = (
                f"{filename}\nSHA-256: {group.digest}\n"
                f"{group.verified_copy_count:,} distinct file copies across {len(group.paths):,} path(s)"
            )
        else:
            item = self.unverified_files[index.row() - len(self.groups)]
            filename = os.path.basename(item.path)
            values = ("Unverified", filename, "Not verified", "Unknown", "Not counted")
            sort_values = (1, filename.casefold(), 0, -1, -1)
            tooltip = f"{item.path}\nNot included in savings: {item.reason}"

        if role == Qt.ItemDataRole.DisplayRole:
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return tooltip
        if role == Qt.ItemDataRole.UserRole:
            return sort_values[index.column()]
        return None

    def result_at(self, row: int) -> DuplicateGroup | UnverifiedFile:
        if row < len(self.groups):
            return self.groups[row]
        return self.unverified_files[row - len(self.groups)]

    def set_results(
        self,
        groups: tuple[DuplicateGroup, ...],
        unverified_files: tuple[UnverifiedFile, ...],
    ) -> None:
        self.beginResetModel()
        self.groups = groups
        self.unverified_files = unverified_files
        self.endResetModel()


class DuplicatePathsModel(QAbstractTableModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: tuple[tuple[str, str, str, str], ...] = ()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else 4

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return ("File name", "Containing folder", "Full path", "Verification")[section]
        return None

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return row[2] if index.column() < 3 else row[3]
        return None

    def path_at(self, row: int) -> str:
        return self.rows[row][2]

    def set_group(self, result: DuplicateGroup | UnverifiedFile | None) -> None:
        if isinstance(result, DuplicateGroup):
            rows = tuple(
                (
                    os.path.basename(path),
                    os.path.dirname(path),
                    path,
                    "Verified identical content",
                )
                for path in result.paths
            )
        elif isinstance(result, UnverifiedFile):
            rows = (
                (
                    os.path.basename(result.path),
                    os.path.dirname(result.path),
                    result.path,
                    f"Not verified: {result.reason}",
                ),
            )
        else:
            rows = ()
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class DuplicatesPage(QWidget):
    find_duplicates_requested = Signal(bool)
    cancel_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(14)
        title = QLabel("Duplicates")
        title.setObjectName("pageTitle")
        description = QLabel("Review verified duplicate files using full SHA-256 comparison.")
        description.setObjectName("mutedText")
        description.setToolTip(
            "Completed hashes may be reused when file identity, size, timestamps, and a content "
            "sample match. Edits that preserve those values can evade cache invalidation. "
            "SpaceWise reads files only and never changes them."
        )
        layout.addWidget(title)
        layout.addWidget(description)

        controls = QHBoxLayout()
        self.personal_only = QCheckBox("Limit analysis to personal files")
        controls.addWidget(self.personal_only)
        controls.addStretch()
        self.find_button = QPushButton("Find Duplicates")
        self.find_button.setObjectName("primaryButton")
        self.find_button.clicked.connect(
            lambda: self.find_duplicates_requested.emit(self.personal_only.isChecked())
        )
        controls.addWidget(self.find_button)
        layout.addLayout(controls)

        self.scan_scope_label = QLabel("No completed scan available. Scan a folder, your files, or a drive first.")
        self.scan_scope_label.setObjectName("pathBanner")
        self.scan_scope_label.setWordWrap(True)
        layout.addWidget(self.scan_scope_label)
        self.activity_panel = ActivityPanel("Cancel Analysis")
        self.activity_panel.cancel_requested.connect(self.cancel_requested.emit)
        self.progress_panel = self.activity_panel
        self.progress_status = self.activity_panel.status_label
        self.progress_details = self.activity_panel.details_label
        self.progress_bar = self.activity_panel.progress_bar
        self.cancel_button = self.activity_panel.cancel_button
        layout.addWidget(self.activity_panel)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("sectionTitle")
        self.analysis_duration_label = QLabel()
        self.analysis_duration_label.setObjectName("mutedText")
        self.analysis_duration_label.setAccessibleName("Duplicate analysis duration")
        summary_layout = QHBoxLayout()
        summary_layout.addWidget(self.summary_label, 1)
        summary_layout.addWidget(self.analysis_duration_label)
        layout.addLayout(summary_layout)
        self.status_label = QLabel()
        self.status_label.setObjectName("mutedText")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.result_state_label = QLabel(
            "No duplicate analysis yet. Select Find Duplicates to compare scanned files."
        )
        self.result_state_label.setObjectName("mutedText")
        self.result_state_label.setWordWrap(True)
        layout.addWidget(self.result_state_label)

        self.results_model = DuplicateResultsModel(self)
        self.results_proxy = QSortFilterProxyModel(self)
        self.results_proxy.setSourceModel(self.results_model)
        self.results_proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.groups_table = QTableView()
        self.groups_table.setObjectName("duplicateGroupsTable")
        self.groups_table.setModel(self.results_proxy)
        self.groups_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.groups_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.groups_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.groups_table.setAlternatingRowColors(True)
        self.groups_table.setShowGrid(False)
        self.groups_table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.groups_table.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.groups_table.verticalHeader().hide()
        header = self.groups_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setHighlightSections(False)
        header.setSortIndicatorShown(True)
        self.groups_table.setSortingEnabled(True)
        self.groups_table.setColumnWidth(0, 190)
        self.groups_table.setColumnWidth(1, 220)
        self.groups_table.setColumnWidth(2, 180)
        self.groups_table.setColumnWidth(3, 100)
        self.groups_table.setColumnWidth(4, 150)
        self.groups_table.setMinimumHeight(150)
        self.groups_table.selectionModel().currentRowChanged.connect(
            self._show_selected_result
        )
        self.groups_table.horizontalHeader().sortIndicatorChanged.connect(
            self._restore_selected_group
        )
        self._selected_group_identity: tuple[int, str] | None = None
        self.detail_title = QLabel("Select a result to see its files.")
        self.detail_title.setObjectName("sectionTitle")
        self.detail_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_summary = QLabel()
        self.detail_summary.setObjectName("mutedText")
        self.detail_summary.setWordWrap(True)
        self.paths_model = DuplicatePathsModel(self)
        self.paths_table = QTableView()
        self.paths_table.setObjectName("duplicatePathsTable")
        self.paths_table.setModel(self.paths_model)
        self.paths_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.paths_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.paths_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.paths_table.setShowGrid(False)
        self.paths_table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.paths_table.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.paths_table.verticalHeader().hide()
        self.paths_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.paths_table.horizontalHeader().setStretchLastSection(True)
        self.paths_table.setColumnWidth(0, 170)
        self.paths_table.setColumnWidth(1, 230)
        self.paths_table.setColumnWidth(2, 420)
        self.paths_table.setColumnWidth(3, 190)
        self.paths_table.setMinimumHeight(150)
        self.paths_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.paths_table.customContextMenuRequested.connect(
            self._show_path_context_menu
        )
        self.copy_path_button = QPushButton("Copy selected path")
        self.copy_path_button.setObjectName("secondaryButton")
        self.copy_path_button.clicked.connect(self._copy_selected_path)
        self.show_in_explorer_button = QPushButton("Show in Windows Explorer")
        self.show_in_explorer_button.setObjectName("secondaryButton")
        self.show_in_explorer_button.clicked.connect(self._show_selected_path)
        detail_actions = QHBoxLayout()
        detail_actions.addStretch()
        detail_actions.addWidget(self.copy_path_button)
        detail_actions.addWidget(self.show_in_explorer_button)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        detail_layout.setSpacing(7)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_summary)
        detail_layout.addWidget(self.paths_table, 1)
        detail_layout.addLayout(detail_actions)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.groups_table)
        splitter.addWidget(detail)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(7)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([240, 420])
        layout.addWidget(splitter, 1)
        self._set_path_actions_enabled(False)
        self.set_scan_result(None)

    def set_scan_result(self, result: ScanResult | None) -> None:
        self.analysis = None
        self._selected_group_identity = None
        self.results_model.set_results((), ())
        self.paths_model.set_group(None)
        self.detail_title.setText("Select a result to see its files.")
        self.detail_title.setToolTip("")
        self.detail_summary.clear()
        self.summary_label.clear()
        self.analysis_duration_label.clear()
        self.status_label.clear()
        self.status_label.setToolTip("")
        self.activity_panel.stop()
        self.find_button.setEnabled(result is not None)
        self.personal_only.setEnabled(True)
        self._set_path_actions_enabled(False)
        if result is None:
            self.scan_scope_label.setText(
                "No completed scan available. Scan a folder, your files, or a drive first."
            )
            self.result_state_label.setText(
                "No duplicate analysis yet. Complete a scan, then select Find Duplicates."
            )
        else:
            self.scan_scope_label.setText(
                f"Latest completed scan: {result.display_scope} · {len(result.files):,} scanned file(s)"
            )
            self.result_state_label.setText(
                "No duplicate analysis yet. Select Find Duplicates to compare scanned files."
            )

    def set_historical_result(self, result: ScanResult) -> None:
        self.set_scan_result(result)
        self.find_button.setEnabled(False)
        self.personal_only.setEnabled(False)
        self.status_label.setText(
            "Saved metadata may be out of date. Rescan the location before verifying "
            "current file contents."
        )
        self.result_state_label.setText(
            "Duplicate verification is unavailable for saved scans. Rescan to continue."
        )

    def set_analyzing(self, candidate_count: int | None = None) -> None:
        self.analysis = None
        self._selected_group_identity = None
        self.results_model.set_results((), ())
        self.paths_model.set_group(None)
        self.summary_label.clear()
        self.analysis_duration_label.clear()
        self.status_label.setToolTip("")
        self.find_button.setEnabled(False)
        self.personal_only.setEnabled(False)
        self._set_path_actions_enabled(False)
        self.result_state_label.setText(
            "Analysis in progress. Results appear when verification completes."
        )
        self.activity_panel.start(
            "Analyzing your storage",
            (
                "Finding same-size candidate files"
                if candidate_count is None
                else f"Files checked: 0 of {candidate_count:,} candidates"
            ),
        )
        self.status_label.setText(
            "Candidates are sampled first, then likely matches are verified with full SHA-256. "
            "Large files can take longer."
        )

    def set_progress(self, progress: DuplicateProgress) -> None:
        stages = {
            "grouping": "Grouping scanned files by size",
            "validating candidates": "Validating same-size candidates",
            "sampling candidates": "Comparing bounded content samples",
            "verifying SHA-256": "Verifying candidate contents with full SHA-256",
            "validating file paths": "Rechecking candidate paths",
            "finalizing results": "Sorting verified results",
            "complete": "Analysis complete",
        }
        self.activity_panel.status_label.setText(stages.get(progress.stage, progress.stage))
        stage_specific = {
            "sampling candidates": "Sampled candidates: ",
            "verifying SHA-256": "SHA-256 verification: ",
            "validating candidates": "Candidate validation: ",
            "validating file paths": "Path validation: ",
            "grouping": "Grouping files by size: ",
            "finalizing results": "Finalizing results: ",
        }
        label_prefix = stage_specific.get(progress.stage, "Progress: ")
        self.activity_panel.set_details(
            f"{label_prefix}"
            f"Candidates: {progress.candidate_files:,} · "
            f"Processed: {progress.files_checked:,} · "
            f"SHA-256 hashed: {progress.hashed_files:,} · "
            f"Read: {format_size(progress.bytes_processed)} · "
            f"Skipped: {progress.skipped_files:,} · "
            f"Cache reused: {progress.cached_files_reused:,} · "
            f"Elapsed: {progress.elapsed_seconds:.1f} s"
        )

    def set_result(self, result: DuplicateAnalysis) -> None:
        self.analysis = result
        self._selected_group_identity = None
        self.activity_panel.stop()
        self.personal_only.setEnabled(True)
        self.find_button.setEnabled(True)
        self.results_model.set_results(result.groups, result.unverified_files)
        self.paths_model.set_group(None)
        self._set_path_actions_enabled(False)
        self.status_label.setToolTip("")
        self.summary_label.setText(
            f"{len(result.groups):,} verified duplicate group(s) · "
            f"{format_size(result.potential_recoverable_bytes)} potential savings"
        )
        if result.elapsed_seconds is not None:
            self.analysis_duration_label.setText(
                f"Analysis time: {format_duration(result.elapsed_seconds)}"
            )
        else:
            self.analysis_duration_label.clear()
        if result.groups and any(
            group.verified_copy_count < len(group.paths) for group in result.groups
        ):
            hard_link_note = " Hard-linked paths are shown but do not count as separate copies."
        else:
            hard_link_note = ""
        self.status_label.setText(
            f"Checked {result.candidate_files:,} same-size candidate file(s); "
            f"SHA-256 hashed {result.hashed_files:,} distinct file(s), "
            f"reused {result.cached_files_reused:,} cached hash(es), and read "
            f"{format_size(result.bytes_processed)}; skipped {result.skipped_files:,}. "
            "Savings count only distinct verified copies." + hard_link_note
        )
        if result.groups and result.unverified_files:
            self.result_state_label.setText(
                "Incomplete analysis: verified duplicate groups are shown, and files that could not "
                "be verified are listed separately. Unverified files are not included in savings."
            )
        elif result.unverified_files:
            self.result_state_label.setText(
                "Incomplete analysis: no verified duplicate groups were found; some candidate files "
                "could not be verified and are listed below."
            )
        elif result.groups:
            self.result_state_label.setText(
                "Verified with full SHA-256. Select a group to inspect its matching paths."
            )
        elif result.candidate_files == 0:
            self.result_state_label.setText(
                "Analysis complete: no equal-size candidate sets were found."
            )
        else:
            self.result_state_label.setText(
                "Analysis complete: no verified duplicate groups were found. Equal-size candidates "
                "were checked by SHA-256 content."
            )
        if result.groups:
            self.groups_table.selectRow(0)
        elif result.unverified_files:
            self.groups_table.selectRow(0)
        else:
            self.detail_title.setText("No matching file paths.")
        if result.unverified_files:
            self.status_label.setToolTip(
                "\n".join(f"{item.path}: {item.reason}" for item in result.unverified_files)
            )

    def set_no_candidates(self, message: str) -> None:
        self.activity_panel.stop()
        self.personal_only.setEnabled(True)
        self.find_button.setEnabled(True)
        self.status_label.setToolTip("")
        self.result_state_label.setText(message)
        self.status_label.setText(message)

    def set_failed(self, message: str) -> None:
        self.activity_panel.stop()
        self.personal_only.setEnabled(True)
        self.find_button.setEnabled(True)
        self.status_label.setToolTip("")
        self.result_state_label.setText(
            "Analysis failed. No partial results were committed."
        )
        self.status_label.setText(f"Duplicate analysis failed: {message}")

    def set_cancelled(self, message: str) -> None:
        self.activity_panel.stop()
        self.personal_only.setEnabled(True)
        self.find_button.setEnabled(True)
        self.status_label.setToolTip("")
        self.result_state_label.setText(
            "Analysis cancelled. No partial results were committed."
        )
        self.status_label.setText(
            f"Duplicate analysis cancelled. {message} No partial results were committed."
        )

    def _set_path_actions_enabled(self, enabled: bool) -> None:
        self.copy_path_button.setEnabled(enabled)
        self.show_in_explorer_button.setEnabled(enabled)

    def _show_selected_result(self, current: QModelIndex) -> None:
        if not current.isValid():
            self.paths_model.set_group(None)
            self._selected_group_identity = None
            self._set_path_actions_enabled(False)
            return
        source_index = self.results_proxy.mapToSource(current)
        result = self.results_model.result_at(source_index.row())
        self.paths_model.set_group(result)
        self._set_path_actions_enabled(self.paths_model.rowCount() > 0)
        if isinstance(result, DuplicateGroup):
            self._selected_group_identity = (result.size, result.digest)
            source_index = self.results_proxy.mapToSource(current)
            group_number = source_index.row() + 1
            filename = os.path.basename(result.paths[0])
            self.detail_title.setText(
                f"Selected: {filename} — Group {group_number:,} of "
                f"{len(self.results_model.groups):,}"
            )
            self.detail_title.setToolTip(
                f"SHA-256: {result.digest}\n{result.paths[0]}"
            )
            self.detail_summary.setText(
                f"{result.verified_copy_count:,} distinct verified copy/copies across "
                f"{len(result.paths):,} path(s). Estimated potential savings: "
                f"{format_size(result.recoverable_bytes)}."
            )
        else:
            self._selected_group_identity = None
            self.detail_title.setText(
                f"Selected: {os.path.basename(result.path)} — Not verified"
            )
            self.detail_title.setToolTip(result.path)
            self.detail_summary.setText(
                f"Not counted toward duplicate groups or savings. {result.reason}"
            )
        if self.paths_model.rowCount():
            self.paths_table.selectRow(0)

    def _restore_selected_group(self, *_args: object) -> None:
        identity = self._selected_group_identity
        if identity is None:
            return

        def restore() -> None:
            source_row = next(
                (
                    row
                    for row, group in enumerate(self.results_model.groups)
                    if (group.size, group.digest) == identity
                ),
                None,
            )
            if source_row is None:
                return
            source_index = self.results_model.index(source_row, 0)
            proxy_index = self.results_proxy.mapFromSource(source_index)
            if proxy_index.isValid():
                self.groups_table.selectRow(proxy_index.row())

        QTimer.singleShot(0, self, restore)

    def _selected_path(self) -> str | None:
        index = self.paths_table.currentIndex()
        return self.paths_model.path_at(index.row()) if index.isValid() else None

    def _copy_selected_path(self) -> None:
        path = self._selected_path()
        if path is not None:
            QApplication.clipboard().setText(path)

    def _show_selected_path(self) -> None:
        path = self._selected_path()
        if path is not None:
            self.show_in_windows_explorer(path)

    def _show_path_context_menu(self, position) -> None:
        index = self.paths_table.indexAt(position)
        if not index.isValid():
            return
        self.paths_table.selectRow(index.row())
        menu = QMenu(self)
        menu.addAction("Copy path", self._copy_selected_path)
        menu.addAction("Show in Windows Explorer", self._show_selected_path)
        menu.popup(self.paths_table.viewport().mapToGlobal(position))

    def show_in_windows_explorer(self, path: str) -> None:
        try:
            file_stat = os.stat(path)
        except FileNotFoundError:
            QMessageBox.information(
                self,
                "File unavailable",
                "This file no longer exists at its analyzed location.",
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
                "The analyzed path is no longer a regular file.",
            )
            return

        started = QProcess.startDetached(
            "explorer.exe",
            ["/select,", path],
        )[0]
        if started:
            return
        folder = os.path.dirname(path)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
            QMessageBox.warning(
                self,
                "Could not open folder",
                "Windows Explorer could not be started for this location.",
            )
