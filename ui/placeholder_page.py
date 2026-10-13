"""Simple placeholder screens for future sections."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PlaceholderPage(QWidget):
    def __init__(self, section: str, description: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(10)
        title = QLabel(section)
        title.setObjectName("pageTitle")
        message = QLabel("Coming soon")
        message.setObjectName("comingSoon")
        detail = QLabel(description)
        detail.setObjectName("mutedText")
        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(message, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(detail, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
