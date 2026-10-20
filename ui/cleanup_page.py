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
    QTimer,
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
    QSizePolicy,
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


class RecommendationTableView(QTableView):
    _COLUMN_PROPORTIONS = (0.14, 0.43, 0.13, 0.15, 0.15)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._size_columns()
        QTimer.singleShot(0, self._size_columns)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._size_columns)

    def _size_columns(self) -> None:
        available_width = self.viewport().width()
        if available_width <= 0 or self.model() is None:
            return
        widths = [
            int(available_width * proportion)
            for proportion in self._COLUMN_PROPORTIONS[:-1]
        ]
        widths.append(available_width - sum(widths))
        for column, width in enumerate(widths):
            self.setColumnWidth(column, max(48, width))


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
        description = QLabel("Review storage opportunities from the latest scan.")
        description.setObjectName("mutedText")
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

        (
            self.duplicate_metric_card,
            self.duplicate_metric_value,
            self.duplicate_metric_detail,
        ) = self._make_metric_card(
            "Verified duplicate groups", "Not analyzed", "Potential savings: unavailable"
        )
        (
            self.large_file_metric_card,
            self.large_file_metric_value,
            self.large_file_metric_detail,
        ) = self._make_metric_card("Large files", "0", "Require review")
        (
            self.installer_metric_card,
            self.installer_metric_value,
            self.installer_metric_detail,
        ) = self._make_metric_card("Older installers", "0", "Require review")
        metrics_layout = QHBoxLayout()
        metrics_layout.setSpacing(10)
        for metric in (
            self.duplicate_metric_card,
            self.large_file_metric_card,
            self.installer_metric_card,
        ):
            metrics_layout.addWidget(metric, 1)
        layout.addLayout(metrics_layout)

        self.scan_scope_label = QLabel("No completed scan available.")
        self.scan_scope_label.setObjectName("pathBanner")
        self.scan_scope_label.setWordWrap(False)
        layout.addWidget(self.scan_scope_label)

        controls = QHBoxLayout()
        controls.setSpacing(5)
        controls.addWidget(QLabel("Show"))
        self.category_filter = QComboBox()
        self.category_filter.addItems(
            ("All recommendations", "Older installer", "Large file", "Verified duplicates")
        )
        self.category_filter.setMinimumWidth(140)
        self.category_filter.currentTextChanged.connect(self._apply_category_filter)
        controls.addWidget(self.category_filter)
        controls.addWidget(QLabel("Age ≥"))
        self.age_threshold = QSpinBox()
        self.age_threshold.setRange(1, 3650)
        self.age_threshold.setValue(90)
        self.age_threshold.setSuffix(" d")
        self.age_threshold.setFixedWidth(92)
        self.age_threshold.setToolTip("Review installer-like files at least this many days old.")
        self.age_threshold.valueChanged.connect(self._thresholds_changed)
        controls.addWidget(self.age_threshold)

        controls.addWidget(QLabel("Large ≥"))
        self.large_file_minus_button = QPushButton("−")
        self.large_file_minus_button.setObjectName("thresholdButton")
        self.large_file_minus_button.setAccessibleName("Decrease large-file threshold")
        self.large_file_minus_button.setToolTip("Decrease the large-file threshold.")
        self.large_file_minus_button.setFixedSize(32, 34)
        self.large_file_minus_button.clicked.connect(lambda: self._step_large_threshold(-1))
        controls.addWidget(self.large_file_minus_button)
        self.large_file_threshold_input = QLineEdit(
            format_large_threshold(self._large_threshold_bytes)
        )
        self.large_file_threshold = self.large_file_threshold_input
        self.large_file_threshold_input.setFixedWidth(100)
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
        self.large_file_plus_button.setFixedSize(32, 34)
        self.large_file_plus_button.clicked.connect(lambda: self._step_large_threshold(1))
        controls.addWidget(self.large_file_plus_button)
        controls.addStretch()
        layout.addLayout(controls)
        self._update_large_threshold_controls()

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
        self.summary_label.setWordWrap(False)
        layout.addWidget(self.summary_label)
        self.safety_label = QLabel("Review only — SpaceWise never deletes or changes scanned files.")
        self.safety_label.setObjectName("mutedText")
        layout.addWidget(self.safety_label)

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
        self.recommendation_table = RecommendationTableView()
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
        self.recommendation_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        header = self.recommendation_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self.recommendation_table.selectionModel().currentChanged.connect(
            self._show_selected_details
        )
        self.recommendation_table.doubleClicked.connect(self.open_location)
        layout.addWidget(self.recommendation_table, 1)

        detail_panel = QWidget()
        detail_panel.setObjectName("detailCard")
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(12, 8, 12, 8)
        detail_layout.setSpacing(4)
        detail_title = QLabel("Recommendation details")
        detail_title.setObjectName("sectionTitle")
        self.detail_title = detail_title
        detail_layout.addWidget(detail_title)
        self.detail_reason_label = QLabel("Select a row to inspect its files.")
        self.detail_reason_label.setWordWrap(False)
        self.detail_reason_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.detail_reason_label)
        self.detail_condition_label = QLabel()
        self.detail_condition_label.setWordWrap(False)
        self.detail_condition_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.detail_condition_label)
        self.detail_warning_label = QLabel()
        self.detail_warning_label.setObjectName("mutedText")
        self.detail_warning_label.setWordWrap(False)
        detail_layout.addWidget(self.detail_warning_label)
        self.detail_paths = QPlainTextEdit()
        self.detail_paths.setReadOnly(True)
        self.detail_paths.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.detail_paths.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.detail_paths.setMaximumHeight(74)
        self.detail_paths.setAccessibleName("Complete recommendation path")
        detail_layout.addWidget(self.detail_paths)
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
        detail_layout.addLayout(path_actions)
        layout.addWidget(detail_panel)
        self.set_scan_result(None)

    @staticmethod
    def _make_metric_card(
        title: str,
        value: str,
        detail: str,
    ) -> tuple[QWidget, QLabel, QLabel]:
        card = QWidget()
        card.setObjectName("statCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("mutedText")
        value_label = QLabel(value)
        value_label.setObjectName("compactMetric")
        detail_label = QLabel(detail)
        detail_label.setObjectName("mutedText")
        card_layout.addWidget(title_label)
        card_layout.addWidget(value_label)
        card_layout.addWidget(detail_label)
        return card, value_label, detail_label

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
        self._reset_metrics()
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

    def _reset_metrics(self) -> None:
        self.duplicate_metric_value.setText("Pending")
        self.duplicate_metric_detail.setText("Potential savings: unavailable")
        self.large_file_metric_value.setText("—")
        self.large_file_metric_detail.setText("Require review")
        self.installer_metric_value.setText("—")
        self.installer_metric_detail.setText("Require review")

    def set_analyzing(self) -> None:
        self._all_recommendations = None
        self.recommendations = None
        self._reset_metrics()
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
                    title=(
                        f"{os.path.basename(group.paths[0])} + "
                        f"{group.verified_copy_count - 1:,} other"
                        f"{'s' if group.verified_copy_count != 2 else ''}"
                    ),
                    size_bytes=group.size,
                    age_days=None,
                    copies=group.verified_copy_count,
                    savings_bytes=group.recoverable_bytes,
                    paths=group.paths,
                    reason=(
                        f"{group.verified_copy_count:,} distinct copies · "
                        f"{format_size(group.recoverable_bytes)} potential savings."
                    ),
                    condition="Verified by matching full SHA-256 content.",
                    warning="Hard-linked paths are not independently recoverable.",
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
            reason="Installer-like file in a known personal folder.",
            condition=(
                f"{format_age_days(opportunity.modified_days_ago)} old · "
                f"threshold {self.age_threshold.value():,} days."
            ),
            warning="Age does not mean unused.",
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
            reason="Meets the configured large-file minimum.",
            condition=(
                f"File size: {format_size(opportunity.file.size)}; "
                f"current threshold: {format_large_threshold(self._large_threshold_bytes)}."
            ),
            warning="Large does not mean unwanted.",
        )

    def _render_recommendations(self, result: CleanupRecommendations) -> None:
        self.recommendations = result
        rows = self._rows_for(result)
        self.recommendation_model.set_rows(rows)
        self._apply_category_filter()
        self.summary_label.setText(
            f"{result.files_checked:,} eligible files checked · "
            f"{len(result.inaccessible_paths):,} unavailable"
        )
        if result.duplicate_analysis_pending:
            self.duplicate_metric_value.setText("Pending")
            self.duplicate_metric_detail.setText("Potential savings: unavailable")
        else:
            self.duplicate_metric_value.setText(
                f"{len(result.verified_duplicates):,}"
            )
            self.duplicate_metric_detail.setText(
                f"Potential savings: {format_size(result.duplicate_recoverable_bytes)}"
            )
        self.large_file_metric_value.setText(f"{len(result.large_files):,}")
        self.large_file_metric_detail.setText(
            f"At least {format_large_threshold(self._large_threshold_bytes)}"
        )
        self.installer_metric_value.setText(f"{len(result.old_installers):,}")
        self.installer_metric_detail.setText(
            f"At least {self.age_threshold.value():,} days old"
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
        self.detail_title.setToolTip("\n".join(row.paths))
        self.detail_reason_label.setText(row.reason)
        self.detail_reason_label.setToolTip(row.reason)
        self.detail_condition_label.setText(row.condition)
        self.detail_condition_label.setToolTip(row.condition)
        self.detail_warning_label.setText(row.warning)
        self.detail_warning_label.setToolTip(row.warning)
        paths_text = (
            "\n".join(
                f"{os.path.basename(path)}  in  {os.path.dirname(path)}"
                for path in row.paths
            )
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
            for path in row.paths:
                self.path_selector.addItem(
                    f"{os.path.basename(path)} — {os.path.dirname(path)}",
                    path,
                )
            self.path_selector.setCurrentIndex(0)
        self.path_selector.blockSignals(False)
        self.path_selector.setVisible(len(row.paths) > 1)
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
        self.path_selector.hide()
        self._update_path_action_state()

    def _selected_path(self) -> str | None:
        if self._selected_row is not None and len(self._selected_row.paths) == 1:
            return self._selected_row.paths[0]
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
