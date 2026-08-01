from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class Card(QFrame):
    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(10)
        if title:
            label = QLabel(title)
            label.setObjectName("SectionTitle")
            self.layout.addWidget(label)
        if subtitle:
            label = QLabel(subtitle)
            label.setObjectName("Subtitle")
            label.setWordWrap(True)
            self.layout.addWidget(label)


class Badge(QLabel):
    COLORS: ClassVar[dict[str, tuple[str, str]]] = {
        "pending": ("#f2f4f7", "#667085"), "ready": ("#eef2ff", "#4f46e5"),
        "running": ("#eff8ff", "#175cd3"), "paused": ("#fffaeb", "#b54708"),
        "completed": ("#ecfdf3", "#027a48"), "failed": ("#fef3f2", "#b42318"),
        "cancelled": ("#f2f4f7", "#344054"),
    }
    LABELS: ClassVar[dict[str, str]] = {"pending": "Chờ", "ready": "Sẵn sàng", "running": "Đang chạy",
              "paused": "Tạm dừng", "completed": "Hoàn thành", "failed": "Lỗi",
              "cancelled": "Đã hủy"}

    def __init__(self, status: str = "pending") -> None:
        super().__init__()
        self.setObjectName("Badge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_status(status)

    def set_status(self, status: str) -> None:
        bg, fg = self.COLORS.get(status, self.COLORS["pending"])
        self.setText(self.LABELS.get(status, status))
        self.setStyleSheet(f"padding:4px 9px;border-radius:9px;background:{bg};color:{fg};")


class LogPanel(QFrame):
    COLORS: ClassVar[dict[str, str]] = {"INFO": "#344054", "WARNING": "#b54708", "ERROR": "#b42318", "SUCCESS": "#027a48"}

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("LogPanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        header = QHBoxLayout()
        title = QLabel("Nhật ký hoạt động")
        title.setObjectName("SectionTitle")
        clear = QPushButton("Xóa")
        copy = QPushButton("Sao chép")
        clear.clicked.connect(lambda: self.viewer.clear())
        copy.clicked.connect(lambda: self.viewer.selectAll() or self.viewer.copy())
        header.addWidget(title)
        header.addStretch()
        header.addWidget(copy)
        header.addWidget(clear)
        root.addLayout(header)
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setMaximumBlockCount(500)
        self.viewer.setMinimumHeight(130)
        root.addWidget(self.viewer)

    def append_log(self, level: str, message: str) -> None:
        cursor = self.viewer.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.COLORS.get(level, "#344054")))
        cursor.insertText(f"[{datetime.now().astimezone():%H:%M:%S}] {level:<7} {message}\n", fmt)
        self.viewer.setTextCursor(cursor)
        self.viewer.ensureCursorVisible()
