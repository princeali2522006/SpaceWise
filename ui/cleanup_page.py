"""Read-only cleanup review opportunities."""

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import os
import re
import stat

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QProcess,
    QSortFilterProxyModel,
    Qt,
    Signal,
    QUrl,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.duplicates import DuplicateAnalysis
from core.recommendations import (
    MIN_LARGE_FILE_THRESHOLD_BYTES,
    CleanupRecommendations,
    FileReviewOpportunity,
    RecommendationProgress,
    filter_cleanup_recommendations,
)
from core.scanner import ScanResult
from ui.activity_panel import ActivityPanel
from ui.formatting import format_size


MAX_LARGE_FILE_THRESHOLD_BYTES = 100_000 * 1024**3
_LARGE_THRESHOLD_PATTERN = re.compile(
    r"^\s*(\d+(?:\.\d{1,6})?)\s*(MB|GB)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _RecommendationRow:
    category: str
    title: str
    size_bytes: int
    age_days: int | None
    copies: int | None
    savings_bytes: int | None
    paths: tuple[str, ...]
    reason: str
    condition: str
    warning: str


_RECOMMENDATION_HEADERS = (
    "Category",
    "File or duplicate group",
    "File size",
    "Age / copies",
    "Potential savings",
)


class RecommendationModel(QAbstractTableModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: tuple[_RecommendationRow, ...] = ()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_RECOMMENDATION_HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(_RECOMMENDATION_HEADERS)
        ):
            return _RECOMMENDATION_HEADERS[section]
        return None

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | int | None:
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return (
                row.category.casefold(),
                row.title.casefold(),
                row.size_bytes,
                row.age_days if row.age_days is not None else row.copies or -1,
                row.savings_bytes if row.savings_bytes is not None else -1,
            )[index.column()]
        if role == Qt.ItemDataRole.UserRole + 1 and index.column() == 0:
            return row.category
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return (
                row.category,
                row.title,
                format_size(row.size_bytes),
                (
                    f"{row.copies:,} verified copies"
                    if row.copies is not None
                    else format_age_days(row.age_days or 0)
                ),
                format_size(row.savings_bytes)
                if row.savings_bytes is not None
                else "Not estimated",
            )[index.column()]
        return None

    def recommendation_at(self, row: int) -> _RecommendationRow:
        return self.rows[row]

    def set_rows(self, rows: tuple[_RecommendationRow, ...]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class RecommendationFilterModel(QSortFilterProxyModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.category = "All recommendations"
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.setFilterKeyColumn(0)
        self.setFilterRole(Qt.ItemDataRole.UserRole + 1)

    def set_category(self, category: str) -> None:
        self.category = category
        self.setFilterFixedString(
            "" if category == "All recommendations" else category
        )


def _stat_path(path: str) -> os.stat_result:
    return os.stat(path)


def format_large_threshold(threshold_bytes: int) -> str:
    unit_bytes = 1024**3 if threshold_bytes >= 1024**3 else 1024**2
    unit = "GB" if unit_bytes == 1024**3 else "MB"
    value = Decimal(threshold_bytes) / Decimal(unit_bytes)
    display_value = format(value, "f")
    if "." in display_value:
        display_value = display_value.rstrip("0").rstrip(".")
    return f"{display_value} {unit}"


def format_age_days(days: int) -> str:
    unit = "day" if days == 1 else "days"
    return f"About {days:,} {unit}"


def parse_large_threshold(text: str) -> int:
    match = _LARGE_THRESHOLD_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError("Enter a size with units, for example 100 MB or 1.5 GB.")
    try:
        amount = Decimal(match.group(1))
    except InvalidOperation as error:
        raise ValueError("Enter a valid numeric size.") from error
    multiplier = 1024**3 if match.group(2).upper() == "GB" else 1024**2
    exact_bytes = amount * multiplier
    if exact_bytes != exact_bytes.to_integral_value():
        raise ValueError("The size must resolve to a whole number of bytes.")
    threshold_bytes = int(exact_bytes)
    if not MIN_LARGE_FILE_THRESHOLD_BYTES <= threshold_bytes <= MAX_LARGE_FILE_THRESHOLD_BYTES:
        raise ValueError("The large-file threshold must be from 1 MB through 100,000 GB.")
    return threshold_bytes


class CleanupPage(QWidget):
    analyze_requested = Signal(int, int)
    cancel_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.recommendations: CleanupRecommendations | None = None
        self._all_recommendations: CleanupRecommendations | None = None
        self._selected_row: _RecommendationRow | None = None
        self._large_threshold_bytes = 1024**3

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(10)
        title = QLabel("Cleanup")
        title.setObjectName("pageTitle")
        description = QLabel(
            "Review suggested storage opportunities. SpaceWise does not delete files; "
            "recommendations require your judgment."
        )
        description.setObjectName("mutedText")
        description.setWordWrap(True)
        header_layout = QHBoxLayout()
        title_stack = QVBoxLayout()
        title_stack.setSpacing(3)
        title_stack.addWidget(title)
        title_stack.addWidget(description)
        header_layout.addLayout(title_stack)
        header_layout.addStretch()
        self.analyze_button = QPushButton("Analyze Recommendations")
        self.analyze_button.setObjectName("primaryButton")
        self.analyze_button.clicked.connect(self._request_analysis)
        header_layout.addWidget(self.analyze_button)
        layout.addLayout(header_layout)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Older installer:"))
        self.age_threshold = QSpinBox()
        self.age_threshold.setRange(1, 3650)
        self.age_threshold.setValue(90)
        self.age_threshold.setSuffix(" days")
        self.age_threshold.setToolTip("Review installer-like files at least this many days old.")
        self.age_threshold.valueChanged.connect(self._thresholds_changed)
        controls.addWidget(self.age_threshold)

        controls.addWidget(QLabel("Large file minimum:"))
        self.large_file_minus_button = QPushButton("−")
        self.large_file_minus_button.setObjectName("thresholdButton")
        self.large_file_minus_button.setAccessibleName("Decrease large-file threshold")
        self.large_file_minus_button.setToolTip("Decrease the large-file threshold.")
        self.large_file_minus_button.setFixedSize(42, 38)
        self.large_file_minus_button.clicked.connect(lambda: self._step_large_threshold(-1))
        controls.addWidget(self.large_file_minus_button)
        self.large_file_threshold_input = QLineEdit(
            format_large_threshold(self._large_threshold_bytes)
        )
        self.large_file_threshold = self.large_file_threshold_input
        self.large_file_threshold_input.setFixedWidth(126)
        self.large_file_threshold_input.setAccessibleName("Large-file threshold")
        self.large_file_threshold_input.setPlaceholderText("e.g. 100 MB")
        self.large_file_threshold_input.setToolTip(
            "Type a value from 1 MB through 100,000 GB, including its unit."
        )
        self.large_file_threshold_input.editingFinished.connect(
            self._commit_large_threshold_input
        )
        controls.addWidget(self.large_file_threshold_input)
        self.large_file_plus_button = QPushButton("+")
        self.large_file_plus_button.setObjectName("thresholdButton")
        self.large_file_plus_button.setAccessibleName("Increase large-file threshold")
        self.large_file_plus_button.setToolTip("Increase the large-file threshold.")
        self.large_file_plus_button.setFixedSize(42, 38)
        self.large_file_plus_button.clicked.connect(lambda: self._step_large_threshold(1))
        controls.addWidget(self.large_file_plus_button)
        controls.addStretch()
        layout.addLayout(controls)
        self._update_large_threshold_controls()

        self.scan_scope_label = QLabel("No completed scan available.")
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
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        self.safety_label = QLabel(
            "Large files meet the size threshold; older installers meet the age threshold; "
            "verified duplicates have identical SHA-256 contents. "
            "Size and age thresholds affect only their own categories, never verified duplicates. "
            "A file may appear in more than one category. "
            "Older does not mean unused, and large does not mean unwanted. "
            "Important project, virtual-machine, or system files should not be treated as safe to remove. "
            "Potential savings are estimated only for verified duplicate copies."
        )
        self.safety_label.setObjectName("mutedText")
        self.safety_label.setWordWrap(True)
        layout.addWidget(self.safety_label)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Show:"))
        self.category_filter = QComboBox()
        self.category_filter.addItems(
            ("All recommendations", "Older installer", "Large file", "Verified duplicates")
        )
        self.category_filter.setMinimumWidth(190)
        self.category_filter.currentTextChanged.connect(self._apply_category_filter)
        filter_layout.addWidget(self.category_filter)
        filter_layout.addWidget(QLabel("Select a row to inspect its full details; sort by clicking a column."))
        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        self.status_label = QLabel()
        self.status_label.setObjectName("mutedText")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.empty_state_label = QLabel("No recommendations yet. Analyze a completed scan to review opportunities.")
        self.empty_state_label.setObjectName("mutedText")
        self.empty_state_label.setWordWrap(True)
        layout.addWidget(self.empty_state_label)

        self.recommendation_model = RecommendationModel(self)
        self.recommendation_proxy = RecommendationFilterModel(self)
        self.recommendation_proxy.setSourceModel(self.recommendation_model)
        self.recommendation_table = QTableView()
        self.recommendation_table.setObjectName("cleanupRecommendationsTable")
        self.recommendation_table.setModel(self.recommendation_proxy)
        self.recommendation_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.recommendation_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.recommendation_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.recommendation_table.setAlternatingRowColors(True)
        self.recommendation_table.setShowGrid(False)
        self.recommendation_table.setSortingEnabled(True)
        self.recommendation_table.verticalHeader().hide()
        self.recommendation_table.verticalHeader().setDefaultSectionSize(30)
        self.recommendation_table.setWordWrap(False)
        header = self.recommendation_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self.recommendation_table.setColumnWidth(0, 160)
        self.recommendation_table.setColumnWidth(1, 340)
        self.recommendation_table.setColumnWidth(2, 105)
        self.recommendation_table.setColumnWidth(3, 145)
        self.recommendation_table.setColumnWidth(4, 150)
        self.recommendation_table.selectionModel().currentChanged.connect(
            self._show_selected_details
        )
        self.recommendation_table.doubleClicked.connect(self.open_location)
        layout.addWidget(self.recommendation_table, 1)

        detail_title = QLabel("Recommendation details")
        detail_title.setObjectName("sectionTitle")
        self.detail_title = detail_title
        layout.addWidget(detail_title)
        self.detail_reason_label = QLabel("Select a recommendation to inspect its reason and threshold.")
        self.detail_reason_label.setWordWrap(True)
        self.detail_reason_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail_reason_label)
        self.detail_condition_label = QLabel()
        self.detail_condition_label.setWordWrap(True)
        self.detail_condition_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail_condition_label)
        self.detail_warning_label = QLabel()
        self.detail_warning_label.setObjectName("mutedText")
        self.detail_warning_label.setWordWrap(True)
        layout.addWidget(self.detail_warning_label)
        self.detail_paths = QPlainTextEdit()
        self.detail_paths.setReadOnly(True)
        self.detail_paths.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.detail_paths.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.detail_paths.setMaximumHeight(110)
        self.detail_paths.setAccessibleName("Complete recommendation path")
        layout.addWidget(self.detail_paths)
        path_actions = QHBoxLayout()
        self.path_selector = QComboBox()
        self.path_selector.setMinimumWidth(200)
        self.path_selector.setAccessibleName("File in this recommendation")
        self.path_selector.currentIndexChanged.connect(self._update_path_action_state)
        path_actions.addWidget(self.path_selector, 1)
        self.copy_path_button = QPushButton("Copy selected path")
        self.copy_path_button.setObjectName("secondaryButton")
        self.copy_path_button.clicked.connect(self.copy_selected_path)
        path_actions.addWidget(self.copy_path_button)
        self.show_in_explorer_button = QPushButton("Show in Windows Explorer")
        self.show_in_explorer_button.setObjectName("secondaryButton")
        self.show_in_explorer_button.clicked.connect(self.show_selected_path)
        path_actions.addWidget(self.show_in_explorer_button)
        layout.addLayout(path_actions)
        self.set_scan_result(None)

    def _request_analysis(self) -> None:
        self.analyze_requested.emit(
            self.age_threshold.value(),
            self._large_threshold_bytes,
        )

    def _step_large_threshold(self, direction: int) -> None:
        if self._large_threshold_bytes < 100 * 1024**2:
            increment_mb = 1
        elif self._large_threshold_bytes < 1024 * 1024**2:
            increment_mb = 10
        else:
            increment_mb = 100
        next_value = self._large_threshold_bytes + direction * increment_mb * 1024**2
        self._set_large_threshold(
            min(MAX_LARGE_FILE_THRESHOLD_BYTES, max(MIN_LARGE_FILE_THRESHOLD_BYTES, next_value))
        )

    def _commit_large_threshold_input(self) -> None:
        try:
            threshold_bytes = parse_large_threshold(self.large_file_threshold_input.text())
        except ValueError as error:
            self.status_label.setText(str(error))
            self.large_file_threshold_input.setText(
                format_large_threshold(self._large_threshold_bytes)
            )
            return
        self.status_label.clear()
        self._set_large_threshold(threshold_bytes)

    def _set_large_threshold(self, threshold_bytes: int) -> None:
        if not (
            MIN_LARGE_FILE_THRESHOLD_BYTES
            <= threshold_bytes
            <= MAX_LARGE_FILE_THRESHOLD_BYTES
        ):
            raise ValueError("The large-file threshold must be from 1 MB through 100,000 GB.")
        self._large_threshold_bytes = threshold_bytes
        self.status_label.clear()
        self._update_large_threshold_controls()
        self._thresholds_changed()

    def _update_large_threshold_controls(self) -> None:
        value = format_large_threshold(self._large_threshold_bytes)
        self.large_file_threshold.setToolTip(
            f"Current minimum: {value} ({self._large_threshold_bytes:,} bytes)"
        )
        self.large_file_threshold.setText(value)
        self.large_file_minus_button.setEnabled(
            self._large_threshold_bytes > MIN_LARGE_FILE_THRESHOLD_BYTES
        )
        self.large_file_plus_button.setEnabled(
            self._large_threshold_bytes < MAX_LARGE_FILE_THRESHOLD_BYTES
        )

    def _thresholds_changed(self, *_args: object) -> None:
        if self._all_recommendations is None:
            return
        self._render_recommendations(
            filter_cleanup_recommendations(
                self._all_recommendations,
                age_threshold_days=self.age_threshold.value(),
                large_file_threshold_bytes=self._large_threshold_bytes,
            )
        )

    def set_scan_result(self, result: ScanResult | None) -> None:
        self._all_recommendations = None
        self.recommendations = None
        self.recommendation_model.set_rows(())
        self.recommendation_table.hide()
        self.summary_label.clear()
        self.status_label.clear()
        self._clear_details()
        self.activity_panel.stop()
        self.analyze_button.setEnabled(result is not None)
        self._set_threshold_controls_enabled(True)
        self.category_filter.setEnabled(True)
        self.scan_scope_label.setText(
            "No completed scan available."
            if result is None
            else f"Latest completed scan: {result.display_scope} · {len(result.files):,} scanned file(s)"
        )
        self.empty_state_label.setText(
            "No completed scan available. Complete a scan before analyzing recommendations."
            if result is None
            else "No recommendations yet. Select Analyze Recommendations to review this scan."
        )
        self.empty_state_label.show()

    def set_analyzing(self) -> None:
        self._all_recommendations = None
        self.recommendations = None
        self.recommendation_model.set_rows(())
        self.recommendation_table.hide()
        self.summary_label.clear()
        self._clear_details()
        self.analyze_button.setEnabled(False)
        self._set_threshold_controls_enabled(False)
        self.category_filter.setEnabled(False)
        self.empty_state_label.hide()
        self.activity_panel.start(
            "Preparing review opportunities",
            "Checking eligible files",
        )

    def _set_threshold_controls_enabled(self, enabled: bool) -> None:
        self.age_threshold.setEnabled(enabled)
        self.large_file_threshold_input.setEnabled(enabled)
        self.large_file_plus_button.setEnabled(
            enabled and self._large_threshold_bytes < MAX_LARGE_FILE_THRESHOLD_BYTES
        )
        self.large_file_minus_button.setEnabled(
            enabled and self._large_threshold_bytes > MIN_LARGE_FILE_THRESHOLD_BYTES
        )

    def set_progress(self, progress: RecommendationProgress) -> None:
        self.activity_panel.set_details(
            f"Files checked: {progress.files_checked:,} of {progress.candidate_files:,} candidates"
        )

    def set_result(self, result: CleanupRecommendations) -> None:
        self._all_recommendations = result
        self.activity_panel.stop()
        self.analyze_button.setEnabled(True)
        self._set_threshold_controls_enabled(True)
        self.category_filter.setEnabled(True)
        self._render_recommendations(
            filter_cleanup_recommendations(
                result,
                age_threshold_days=self.age_threshold.value(),
                large_file_threshold_bytes=self._large_threshold_bytes,
            )
        )

    def set_duplicate_analysis(self, analysis: DuplicateAnalysis) -> None:
        if self._all_recommendations is None:
            return
        verified_groups = tuple(
            group for group in analysis.groups if group.verified_copy_count >= 2
        )
        self._all_recommendations = replace(
            self._all_recommendations,
            verified_duplicates=verified_groups,
            duplicate_analysis_pending=False,
        )
        self._render_recommendations(
            filter_cleanup_recommendations(
                self._all_recommendations,
                age_threshold_days=self.age_threshold.value(),
                large_file_threshold_bytes=self._large_threshold_bytes,
            )
        )

    def set_duplicate_pending(self) -> None:
        if self._all_recommendations is None:
            return
        self._all_recommendations = replace(
            self._all_recommendations,
            verified_duplicates=(),
            duplicate_analysis_pending=True,
        )
        self._render_recommendations(
            filter_cleanup_recommendations(
                self._all_recommendations,
                age_threshold_days=self.age_threshold.value(),
                large_file_threshold_bytes=self._large_threshold_bytes,
            )
        )

    def _rows_for(self, result: CleanupRecommendations) -> tuple[_RecommendationRow, ...]:
        rows: list[_RecommendationRow] = []
        for group in result.verified_duplicates:
            rows.append(
                _RecommendationRow(
                    category="Verified duplicates",
                    title=f"{len(group.paths):,} matching path(s)",
                    size_bytes=group.size,
                    age_days=None,
                    copies=group.verified_copy_count,
                    savings_bytes=group.recoverable_bytes,
                    paths=group.paths,
                    reason=(
                        f"{group.verified_copy_count:,} distinct file copies have matching "
                        "full SHA-256 digests."
                    ),
                    condition="Only copies beyond the first distinct copy count toward potential savings.",
                    warning=(
                        "No copy is selected for removal. Hard-linked paths are not counted as "
                        "independently recoverable storage."
                    ),
                )
            )
        for opportunity in result.old_installers:
            rows.append(
                self._installer_row(opportunity)
            )
        for opportunity in result.large_files:
            rows.append(
                self._large_file_row(opportunity)
            )
        return tuple(rows)

    def _installer_row(self, opportunity: FileReviewOpportunity) -> _RecommendationRow:
        return _RecommendationRow(
            category="Older installer",
            title=opportunity.file.name,
            size_bytes=opportunity.file.size,
            age_days=opportunity.modified_days_ago,
            copies=None,
            savings_bytes=None,
            paths=(opportunity.file.path,),
            reason="The file extension identifies an installer-like file under a known personal folder.",
            condition=(
                f"Last-modified age: {format_age_days(opportunity.modified_days_ago).lower()}; "
                f"review threshold: at least {self.age_threshold.value():,} days."
            ),
            warning="Age alone does not show whether an installer is still needed or recently used.",
        )

    def _large_file_row(self, opportunity: FileReviewOpportunity) -> _RecommendationRow:
        return _RecommendationRow(
            category="Large file",
            title=opportunity.file.name,
            size_bytes=opportunity.file.size,
            age_days=opportunity.modified_days_ago,
            copies=None,
            savings_bytes=None,
            paths=(opportunity.file.path,),
            reason="This file is at or above the configured large-file threshold.",
            condition=(
                f"File size: {format_size(opportunity.file.size)}; "
                f"current threshold: {format_large_threshold(self._large_threshold_bytes)}."
            ),
            warning=(
                "Large files are not necessarily unwanted. Verify project, virtual-machine, "
                "and system importance before taking any action."
            ),
        )

    def _render_recommendations(self, result: CleanupRecommendations) -> None:
        self.recommendations = result
        rows = self._rows_for(result)
        self.recommendation_model.set_rows(rows)
        self._apply_category_filter()
        self.summary_label.setText(
            f"Older installers: {len(result.old_installers):,} "
            f"(at least {self.age_threshold.value():,} days old) · "
            f"Large files: {len(result.large_files):,} "
            f"(at least {format_large_threshold(self._large_threshold_bytes)}) · "
            f"Verified duplicate groups: {len(result.verified_duplicates):,} · "
            f"Potential duplicate savings: {format_size(result.duplicate_recoverable_bytes)}."
        )
        duplicate_note = (
            " Duplicate analysis is pending or incomplete; verified duplicates are not included. "
            "Run or complete Find Duplicates on the Duplicates page to add verified results."
            if result.duplicate_analysis_pending
            else ""
        )
        self.status_label.setText(
            f"Checked {result.files_checked:,} eligible file(s). "
            f"{len(result.inaccessible_paths):,} unavailable or changed path(s) were omitted."
            + duplicate_note
        )
        if result.inaccessible_paths:
            self.status_label.setToolTip(
                "\n".join(f"{path}: {reason}" for path, reason in result.inaccessible_paths)
            )
        else:
            self.status_label.setToolTip("")

    def _apply_category_filter(self, *_args: object) -> None:
        selected = self.category_filter.currentText()
        self.recommendation_proxy.set_category(selected)
        visible_rows = self.recommendation_proxy.rowCount()
        has_recommendations = self.recommendation_model.rowCount() > 0
        self.recommendation_table.setVisible(has_recommendations and visible_rows > 0)
        if self._all_recommendations is not None and visible_rows == 0:
            self.empty_state_label.setText(
                "No recommendations match this category and the current thresholds."
                if has_recommendations
                else "No review opportunities match the current thresholds."
            )
            self.empty_state_label.show()
        else:
            self.empty_state_label.hide()
        current_index = self.recommendation_table.currentIndex()
        if current_index.isValid():
            current_row = self.recommendation_proxy.mapToSource(current_index).row()
            current_details = self.recommendation_model.recommendation_at(current_row)
            if current_details != self._selected_row:
                self._show_selected_details(current_index, QModelIndex())
        else:
            if visible_rows == 0:
                self._clear_details()
            else:
                first_visible_index = self.recommendation_proxy.index(0, 0)
                self.recommendation_table.setCurrentIndex(first_visible_index)
                self._show_selected_details(first_visible_index, QModelIndex())

    def _show_selected_details(
        self,
        current_index: QModelIndex,
        _previous_index: QModelIndex,
    ) -> None:
        if not current_index.isValid():
            self._clear_details()
            return
        source_index = self.recommendation_proxy.mapToSource(current_index)
        row = self.recommendation_model.recommendation_at(source_index.row())
        self._selected_row = row
        self.detail_title.setText(f"{row.category}: {row.title}")
        self.detail_reason_label.setText(row.reason)
        self.detail_condition_label.setText(row.condition)
        self.detail_warning_label.setText(row.warning)
        paths_text = (
            "\n".join(f"• {path}" for path in row.paths)
            if len(row.paths) > 1
            else row.paths[0]
            if row.paths
            else ""
        )
        self.detail_paths.setPlainText(paths_text)
        self.detail_paths.setToolTip("\n".join(row.paths))
        self.path_selector.blockSignals(True)
        self.path_selector.clear()
        if len(row.paths) > 1:
            self.path_selector.addItem("Choose a file path…", None)
            for path in row.paths:
                self.path_selector.addItem(path, path)
            self.path_selector.setCurrentIndex(0)
        elif row.paths:
            self.path_selector.addItem(row.paths[0], row.paths[0])
            self.path_selector.setCurrentIndex(0)
        self.path_selector.blockSignals(False)
        self._update_path_action_state()

    def _clear_details(self) -> None:
        self._selected_row = None
        self.detail_title.setText("Recommendation details")
        self.detail_reason_label.setText("Select a recommendation to inspect its reason and threshold.")
        self.detail_condition_label.clear()
        self.detail_warning_label.clear()
        self.detail_paths.clear()
        self.path_selector.blockSignals(True)
        self.path_selector.clear()
        self.path_selector.blockSignals(False)
        self._update_path_action_state()

    def _selected_path(self) -> str | None:
        path = self.path_selector.currentData()
        return path if isinstance(path, str) else None

    def _update_path_action_state(self, *_args: object) -> None:
        enabled = self._selected_path() is not None
        self.copy_path_button.setEnabled(enabled)
        self.show_in_explorer_button.setEnabled(enabled)

    def copy_selected_path(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        QApplication.clipboard().setText(path)
        self.status_label.setText("Full path copied to the clipboard.")

    def show_selected_path(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        try:
            file_stat = _stat_path(path)
        except FileNotFoundError:
            self.status_label.setText("This file no longer exists at its analyzed location.")
            return
        except OSError as error:
            self.status_label.setText(f"Could not access this file: {error}")
            return
        if not stat.S_ISREG(file_stat.st_mode):
            self.status_label.setText("The analyzed path is no longer a regular file.")
            return
        started, _process_id = QProcess.startDetached(
            "explorer.exe",
            ["/select,", path],
        )
        if not started:
            folder = os.path.dirname(path)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
                self.status_label.setText("Windows Explorer could not be started for this location.")
                return
        self.status_label.setText("Opened the file location in Windows Explorer.")

    def open_location(self, _index: QModelIndex) -> None:
        self.show_selected_path()

    def set_failed(self, message: str) -> None:
        self.activity_panel.stop()
        self.analyze_button.setEnabled(True)
        self._set_threshold_controls_enabled(True)
        self.category_filter.setEnabled(True)
        self.status_label.setText(f"Recommendation analysis failed: {message}")

    def set_cancelled(self, message: str) -> None:
        self.activity_panel.stop()
        self.analyze_button.setEnabled(True)
        self._set_threshold_controls_enabled(True)
        self.category_filter.setEnabled(True)
        self.status_label.setText(
            f"Recommendation analysis cancelled. {message} No partial results were committed."
        )
