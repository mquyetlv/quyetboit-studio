from __future__ import annotations

import sys

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import LIGHT_STYLESHEET


def create_app(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("Quyết Bồ IT Studio")
    app.setOrganizationName("Quyết Bồ IT")
    app.setStyle("Fusion")
    app.setStyleSheet(LIGHT_STYLESHEET)
    QLocale.setDefault(QLocale(QLocale.Language.Vietnamese, QLocale.Country.Vietnam))
    return app


def main() -> int:
    app = create_app()
    window = MainWindow()
    window.show()
    return app.exec()
