"""Read-only duplicate-file analysis page."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.duplicates import DuplicateAnalysis, DuplicateProgress
from core.scanner import ScanResult
from ui.activity_panel import ActivityPanel
from ui.formatting import format_size


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
        description = QLabel(
            "Compare equal-size files with SHA-256. Analysis is read-only; no copy is selected for removal."
        )
        description.setObjectName("mutedText")
        description.setWordWrap(True)
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
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        self.status_label = QLabel()
        self.status_label.setObjectName("mutedText")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.groups_table = QTableWidget(0, 5)
        self.groups_table.setHorizontalHeaderLabels(
            ["Status", "Verified copies", "File size", "Potential savings", "Full paths / reason"]
        )
        self.groups_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.groups_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.groups_table.setAlternatingRowColors(True)
        self.groups_table.setShowGrid(False)
        self.groups_table.verticalHeader().hide()
        self.groups_table.horizontalHeader().setStretchLastSection(True)
        self.groups_table.setColumnWidth(0, 150)
        self.groups_table.setColumnWidth(1, 180)
        self.groups_table.setColumnWidth(2, 110)
        self.groups_table.setColumnWidth(3, 155)
        layout.addWidget(self.groups_table, 1)
        self.set_scan_result(None)

    def set_scan_result(self, result: ScanResult | None) -> None:
        self.analysis = None
        self.groups_table.setRowCount(0)
        self.summary_label.clear()
        self.status_label.clear()
        self.activity_panel.stop()
        self.find_button.setEnabled(result is not None)
        if result is None:
            self.scan_scope_label.setText(
                "No completed scan available. Scan a folder, your files, or a drive first."
            )
        else:
            self.scan_scope_label.setText(
                f"Latest completed scan: {result.display_scope} · {len(result.files):,} scanned file(s)"
            )

    def set_analyzing(self, candidate_count: int | None = None) -> None:
        self.analysis = None
        self.groups_table.setRowCount(0)
        self.summary_label.clear()
        self.find_button.setEnabled(False)
        self.personal_only.setEnabled(False)
        self.activity_panel.start(
            "Analyzing your storage",
            (
                "Finding same-size candidate files"
                if candidate_count is None
                else f"Files checked: 0 of {candidate_count:,} candidates"
            ),
        )
        self.status_label.setText(
            "Hashing is limited to scanned files that share the same byte size."
        )

    def set_progress(self, progress: DuplicateProgress) -> None:
        self.activity_panel.set_details(
            f"Files checked: {progress.files_checked:,} of {progress.candidate_files:,} candidates"
        )

    def set_result(self, result: DuplicateAnalysis) -> None:
        self.analysis = result
        self.activity_panel.stop()
        self.personal_only.setEnabled(True)
        self.find_button.setEnabled(True)
        rows: list[tuple[str, str, str, str, str]] = []
        for group in result.groups:
            paths = "\n".join(group.paths)
            rows.append(
                (
                    "Verified duplicate",
                    f"{group.verified_copy_count:,} distinct copy/copies · {len(group.paths):,} path(s)",
                    format_size(group.size),
                    format_size(group.recoverable_bytes),
                    paths,
                )
            )
        for item in result.unverified_files:
            rows.append(
                (
                    "Unverified",
                    "Not counted",
                    "Not verified",
                    "Not included",
                    f"{item.path}\nReason: {item.reason}",
                )
            )
        self.groups_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            paths = values[-1]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 4:
                    item.setToolTip(paths)
                self.groups_table.setItem(row, column, item)
        self.summary_label.setText(
            f"{len(result.groups):,} verified duplicate group(s) · "
            f"{format_size(result.potential_recoverable_bytes)} potential recoverable space"
        )
        if result.groups and any(group.verified_copy_count < len(group.paths) for group in result.groups):
            hard_link_note = " Hard-linked paths are shown but do not count as separate copies."
        else:
            hard_link_note = ""
        self.status_label.setText(
            f"Checked {result.candidate_files:,} same-size candidate file(s); "
            f"hashed {result.hashed_files:,} distinct file(s). "
            f"{len(result.unverified_files):,} file(s) could not be verified. "
            "Zero-byte files contribute no savings." + hard_link_note
        )
        if result.unverified_files:
            self.status_label.setToolTip(
                "\n".join(f"{item.path}: {item.reason}" for item in result.unverified_files)
            )

    def set_no_candidates(self, message: str) -> None:
        self.activity_panel.stop()
        self.personal_only.setEnabled(True)
        self.find_button.setEnabled(True)
        self.status_label.setText(message)

    def set_failed(self, message: str) -> None:
        self.activity_panel.stop()
        self.personal_only.setEnabled(True)
        self.find_button.setEnabled(True)
        self.status_label.setText(f"Duplicate analysis failed: {message}")

    def set_cancelled(self, message: str) -> None:
        self.activity_panel.stop()
        self.personal_only.setEnabled(True)
        self.find_button.setEnabled(True)
        self.status_label.setText(f"Duplicate analysis cancelled. {message} No partial results were committed.")
