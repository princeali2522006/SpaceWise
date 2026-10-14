"""Shared calm progress panel for long-running read-only analysis."""

from PySide6.QtCore import QElapsedTimer, Signal, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ActivityPanel(QWidget):
    cancel_requested = Signal()

    def __init__(self, cancel_text: str) -> None:
        super().__init__()
        self.setObjectName("analysisProgressPanel")
        self.cancel_text = cancel_text
        self.elapsed_timer = QElapsedTimer()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(1000)
        self.refresh_timer.timeout.connect(self._update_elapsed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(6)
        self.status_label = QLabel()
        self.status_label.setObjectName("sectionTitle")
        layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("scanProgress")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(5)
        layout.addWidget(self.progress_bar)
        footer = QHBoxLayout()
        self.details_label = QLabel()
        self.details_label.setObjectName("mutedText")
        footer.addWidget(self.details_label, 1)
        self.elapsed_label = QLabel("Elapsed: 0.0 s")
        self.elapsed_label.setObjectName("mutedText")
        footer.addWidget(self.elapsed_label)
        self.cancel_button = QPushButton(cancel_text)
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        self.hide()

    def start(self, status: str, details: str) -> None:
        self.status_label.setText(status)
        self.details_label.setText(details)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText(self.cancel_text)
        self.elapsed_timer.start()
        self.elapsed_label.setText("Elapsed: 0.0 s")
        self.refresh_timer.start()
        self.show()

    def set_details(self, details: str) -> None:
        self.details_label.setText(details)

    def set_cancelling(self) -> None:
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancelling…")

    def stop(self) -> None:
        self.refresh_timer.stop()
        self.hide()

    def _update_elapsed(self) -> None:
        self.elapsed_label.setText(f"Elapsed: {self.elapsed_timer.elapsed() / 1000:.1f} s")
