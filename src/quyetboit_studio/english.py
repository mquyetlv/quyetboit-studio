from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class EnglishPlaceholderPage(QWidget):
    """Reusable placeholder template for features in the English section."""

    def __init__(self, title: str, icon: str, description: str) -> None:
        super().__init__()
        self.setObjectName("AppRoot")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 40, 48, 40)
        layout.setSpacing(24)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("Title")
        subtitle = QLabel("Tiếng Anh")
        subtitle.setObjectName("Subtitle")
        heading.addWidget(title_label)
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()
        layout.addLayout(header)

        card = QFrame()
        card.setObjectName("Card")
        card.setMaximumWidth(720)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(48, 52, 48, 52)
        card_layout.setSpacing(14)

        icon_label = QLabel(icon)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("font-size: 58px; color: #6366f1;")
        placeholder_title = QLabel(f"Template {title}")
        placeholder_title.setObjectName("SectionTitle")
        placeholder_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_text = QLabel(description)
        placeholder_text.setObjectName("Subtitle")
        placeholder_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_text.setWordWrap(True)
        placeholder_badge = QLabel("PLACEHOLDER · Nội dung sẽ được bổ sung")
        placeholder_badge.setObjectName("Badge")
        placeholder_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card_layout.addWidget(icon_label)
        card_layout.addWidget(placeholder_title)
        card_layout.addWidget(placeholder_text)
        card_layout.addSpacing(8)
        card_layout.addWidget(placeholder_badge, 0, Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        layout.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

