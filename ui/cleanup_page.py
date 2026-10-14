"""Read-only cleanup review opportunities."""

import os

from PySide6.QtCore import Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.recommendations import (
    CleanupRecommendations,
    RecommendationProgress,
)
from core.scanner import ScanResult
from ui.activity_panel import ActivityPanel
from ui.formatting import format_size


class CleanupPage(QWidget):
    analyze_requested = Signal(int, int)
    cancel_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._row_paths: dict[int, tuple[str, ...]] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(12)
        title = QLabel("Cleanup")
        title.setObjectName("pageTitle")
        description = QLabel(
            "Review possible storage opportunities. Nothing is deleted, moved, or marked safe to remove."
        )
        description.setObjectName("mutedText")
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Installer age:"))
        self.age_threshold = QSpinBox()
        self.age_threshold.setRange(1, 3650)
        self.age_threshold.setValue(90)
        self.age_threshold.setSuffix(" days")
        controls.addWidget(self.age_threshold)
        controls.addWidget(QLabel("Large file threshold:"))
        self.large_file_threshold = QDoubleSpinBox()
        self.large_file_threshold.setRange(0.01, 100000.0)
        self.large_file_threshold.setDecimals(2)
        self.large_file_threshold.setValue(1.0)
        self.large_file_threshold.setSuffix(" GB")
        controls.addWidget(self.large_file_threshold)
        controls.addStretch()
        self.analyze_button = QPushButton("Analyze Recommendations")
        self.analyze_button.setObjectName("primaryButton")
        self.analyze_button.clicked.connect(self._request_analysis)
        controls.addWidget(self.analyze_button)
        layout.addLayout(controls)

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
            "Every item requires your review. Old modification times do not establish last use. "
            "Large-file and installer sizes are review opportunities, not guaranteed savings. "
            "Only verified duplicates contribute to the separate potential-savings estimate."
        )
        self.safety_label.setObjectName("mutedText")
        self.safety_label.setWordWrap(True)
        layout.addWidget(self.safety_label)
        self.status_label = QLabel()
        self.status_label.setObjectName("mutedText")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.recommendation_table = QTableWidget(0, 8)
        self.recommendation_table.setHorizontalHeaderLabels(
            [
                "Review opportunity",
                "Filename",
                "Size",
                "Category",
                "Modified (approx.)",
                "Potential savings",
                "Reason for review",
                "Full path(s)",
            ]
        )
        self.recommendation_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recommendation_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recommendation_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.recommendation_table.setAlternatingRowColors(True)
        self.recommendation_table.setShowGrid(False)
        self.recommendation_table.verticalHeader().hide()
        self.recommendation_table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((170, 160, 105, 150, 145, 135, 330)):
            self.recommendation_table.setColumnWidth(column, width)
        self.recommendation_table.cellDoubleClicked.connect(self.open_location)
        layout.addWidget(self.recommendation_table, 1)
        self.set_scan_result(None)

    def _request_analysis(self) -> None:
        threshold_bytes = round(self.large_file_threshold.value() * 1000**3)
        self.analyze_requested.emit(self.age_threshold.value(), threshold_bytes)

    def set_scan_result(self, result: ScanResult | None) -> None:
        self.recommendations = None
        self._row_paths.clear()
        self.recommendation_table.setRowCount(0)
        self.summary_label.clear()
        self.status_label.clear()
        self.activity_panel.stop()
        self.analyze_button.setEnabled(result is not None)
        self.age_threshold.setEnabled(True)
        self.large_file_threshold.setEnabled(True)
        self.scan_scope_label.setText(
            "No completed scan available."
            if result is None
            else f"Latest completed scan: {result.display_scope} · {len(result.files):,} scanned file(s)"
        )

    def set_analyzing(self) -> None:
        self.recommendations = None
        self._row_paths.clear()
        self.recommendation_table.setRowCount(0)
        self.summary_label.clear()
        self.analyze_button.setEnabled(False)
        self.age_threshold.setEnabled(False)
        self.large_file_threshold.setEnabled(False)
        self.activity_panel.start(
            "Preparing review opportunities",
            "Checking eligible files",
        )

    def set_progress(self, progress: RecommendationProgress) -> None:
        self.activity_panel.set_details(
            f"Files checked: {progress.files_checked:,} of {progress.candidate_files:,} candidates"
        )

    def set_result(self, result: CleanupRecommendations) -> None:
        self.recommendations = result
        self.activity_panel.stop()
        self.analyze_button.setEnabled(True)
        self.age_threshold.setEnabled(True)
        self.large_file_threshold.setEnabled(True)
        self.recommendation_table.setRowCount(0)
        self._row_paths.clear()
        rows: list[tuple[str, str, int, str, str, str, str, str, tuple[str, ...]]] = []
        for group in result.verified_duplicates:
            paths = group.paths
            rows.append(
                (
                    "Verified duplicate group",
                    f"{len(paths):,} matching paths",
                    group.size,
                    "Multiple",
                    "Not evaluated",
                    format_size(group.recoverable_bytes),
                    "Same-size files verified with matching full SHA-256 digests.",
                    "\n".join(paths),
                    paths,
                )
            )
        for opportunity in result.old_installers:
            file = opportunity.file
            rows.append(
                (
                    "Old installer-like file",
                    file.name,
                    file.size,
                    file.category,
                    f"~{opportunity.modified_days_ago:,} days ago",
                    "Not estimated",
                    f"Extension suggests an installer; modification is about {opportunity.modified_days_ago:,} "
                    "days old and does not indicate last use.",
                    file.path,
                    (file.path,),
                )
            )
        for opportunity in result.large_files:
            file = opportunity.file
            rows.append(
                (
                    "Large file — review",
                    file.name,
                    file.size,
                    file.category,
                    f"~{opportunity.modified_days_ago:,} days ago",
                    "Not estimated",
                    "At or above the configured large-file threshold; size alone does not make a file safe to remove.",
                    file.path,
                    (file.path,),
                )
            )
        self.recommendation_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            kind, name, size, category, age, savings, reason, paths_text, paths = values
            display = (
                kind,
                name,
                format_size(size),
                category,
                age,
                savings,
                reason,
                paths_text,
            )
            for column, text in enumerate(display):
                item = QTableWidgetItem(text)
                if column == 7:
                    item.setToolTip(paths_text)
                self.recommendation_table.setItem(row, column, item)
            self._row_paths[row] = paths

        self.summary_label.setText(
            f"{len(result.verified_duplicates):,} verified duplicate group(s) · "
            f"{format_size(result.duplicate_recoverable_bytes)} potential duplicate savings · "
            f"{len(result.old_installers):,} old installer-like file(s) · "
            f"{len(result.large_files):,} large file(s) for review."
        )
        if result.duplicate_analysis_pending:
            duplicate_note = (
                " Duplicate analysis has not been run; verified duplicates are not included. "
                "Use Find Duplicates on the Duplicates page to add verified results."
            )
        else:
            duplicate_note = ""
        self.status_label.setText(
            f"Checked {result.files_checked:,} eligible file(s). "
            f"{len(result.inaccessible_paths):,} unavailable or changed path(s) were omitted."
            + duplicate_note
        )
        if result.inaccessible_paths:
            self.status_label.setToolTip(
                "\n".join(f"{path}: {reason}" for path, reason in result.inaccessible_paths)
            )

    def set_failed(self, message: str) -> None:
        self.activity_panel.stop()
        self.analyze_button.setEnabled(True)
        self.age_threshold.setEnabled(True)
        self.large_file_threshold.setEnabled(True)
        self.status_label.setText(f"Recommendation analysis failed: {message}")

    def set_cancelled(self, message: str) -> None:
        self.activity_panel.stop()
        self.analyze_button.setEnabled(True)
        self.age_threshold.setEnabled(True)
        self.large_file_threshold.setEnabled(True)
        self.status_label.setText(
            f"Recommendation analysis cancelled. {message} No partial results were committed."
        )

    def open_location(self, row: int, column: int = 0) -> None:
        if self.recommendation_table.item(row, column) is None:
            return
        paths = self._row_paths.get(row, ())
        if not paths:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(paths[0]))):
            self.status_label.setText("Windows could not open this file location.")
