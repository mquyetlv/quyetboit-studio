LIGHT_STYLESHEET = """
* { font-family: "Segoe UI", Arial; font-size: 13px; color: #172033; }
QMainWindow, QDialog, QWidget#AppRoot { background: #f4f6fb; }
QFrame#Card, QFrame#Sidebar, QFrame#LogPanel, QFrame#StudioPanel {
    background: white; border: 1px solid #e3e7ef; border-radius: 12px;
}
QFrame#Sidebar { border-radius: 0; border-top: 0; border-bottom: 0; border-left: 0; }
QLabel#Title { font-size: 22px; font-weight: 700; color: #111827; }
QLabel#Subtitle { color: #667085; }
QLabel#SectionTitle { font-size: 15px; font-weight: 650; }
QLabel#Badge { padding: 4px 9px; border-radius: 9px; background: #eef2ff; color: #4f46e5; }
QPushButton { background: white; border: 1px solid #d8deea; border-radius: 8px; padding: 8px 13px; }
QPushButton:hover { background: #f7f8fc; border-color: #b9c3d6; }
QPushButton:pressed { background: #eef1f7; }
QPushButton:disabled { color: #98a2b3; background: #f5f6f8; }
QPushButton#Primary { background: #5b5ce2; color: white; border-color: #5b5ce2; font-weight: 600; }
QPushButton#Primary:hover { background: #4d4ed2; }
QPushButton#Danger { color: #d92d20; border-color: #fecdca; }
QPushButton#NavButton { text-align: left; padding: 11px 14px; border: 0; background: transparent; }
QPushButton#NavButton:checked { background: #eef2ff; color: #4f46e5; font-weight: 650; }
QPushButton#StepButton { text-align: left; padding: 10px; border: 0; background: transparent; }
QPushButton#StepButton:checked { background: #eef2ff; color: #4f46e5; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
    background: white; border: 1px solid #d8deea; border-radius: 7px; padding: 7px;
    selection-background-color: #c7d2fe;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border-color: #6366f1; }
QProgressBar { border: 0; border-radius: 5px; background: #e9edf5; text-align: center; height: 9px; }
QProgressBar::chunk { background: #6366f1; border-radius: 5px; }
QTableWidget { background: white; alternate-background-color: #f8fafc; border: 1px solid #e3e7ef; border-radius: 8px; gridline-color: #edf0f5; }
QHeaderView::section { background: #f8fafc; border: 0; border-bottom: 1px solid #e3e7ef; padding: 8px; font-weight: 600; }
QTabWidget::pane { border: 1px solid #e3e7ef; border-radius: 8px; background: white; }
QTabBar::tab { padding: 8px 14px; color: #667085; }
QTabBar::tab:selected { color: #4f46e5; border-bottom: 2px solid #6366f1; }
QToolTip { background: #172033; color: white; border: 0; padding: 5px; }
"""

