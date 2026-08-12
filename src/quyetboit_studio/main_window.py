from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .core.minigame import MinigameProject
from .core.preferences import LastUsedSettingsStore
from .english import EnglishPlaceholderPage
from .minigame import MinigamePage
from .persistence import ProjectStore
from .vietsub import VietSubPage


class MainWindow(QMainWindow):
    def __init__(self, settings_store: LastUsedSettingsStore | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Quyết Bồ IT Studio")
        self.resize(1440, 900); self.setMinimumSize(1100, 700)
        self.settings_store = settings_store or LastUsedSettingsStore()
        self.project = self.settings_store.new_project(); self.project_path: Path | None = None; self.dirty = False
        self.store = ProjectStore(); self.autosave_path = Path.cwd() / ".quyetboit-autosave.json"
        self._build_menu(); self._build_ui()
        self.autosave_timer = QTimer(self); self.autosave_timer.setInterval(15_000); self.autosave_timer.timeout.connect(self._autosave); self.autosave_timer.start()

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("Tệp")
        actions = (("Dự án mới", "Ctrl+N", self.new_project), ("Mở project…", "Ctrl+O", self.open_project),
                   ("Lưu", "Ctrl+S", self.save_project), ("Lưu thành…", "Ctrl+Shift+S", self.save_project_as))
        for text, shortcut, slot in actions:
            action = QAction(text, self); action.setShortcut(shortcut); action.triggered.connect(slot); menu.addAction(action)
        recover = QAction("Khôi phục autosave", self); recover.triggered.connect(self.recover_autosave); menu.addAction(recover)
        menu.addSeparator(); close = QAction("Thoát", self); close.triggered.connect(self.close); menu.addAction(close)

    def _build_ui(self) -> None:
        central = QWidget(); central.setObjectName("AppRoot"); self.setCentralWidget(central)
        root = QHBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        self.sidebar = QFrame(); self.sidebar.setObjectName("Sidebar"); self.sidebar.setFixedWidth(236)
        nav = QVBoxLayout(self.sidebar); nav.setContentsMargins(12,16,12,16); nav.setSpacing(6)
        brand = QHBoxLayout(); self.logo = QLabel("QB"); self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter); self.logo.setFixedSize(36,36); self.logo.setStyleSheet("background:#5b5ce2;color:white;border-radius:10px;font-weight:800;")
        self.brand_name = QLabel("Quyết Bồ IT Studio"); self.brand_name.setObjectName("SectionTitle")
        self.toggle = QPushButton("☰"); self.toggle.setFixedWidth(34); self.toggle.clicked.connect(self.toggle_sidebar)
        brand.addWidget(self.logo); brand.addWidget(self.brand_name, 1); brand.addWidget(self.toggle); nav.addLayout(brand); nav.addSpacing(18)
        self.vietsub_nav = self._nav_button("🎬  VietSub", "VietSub"); self.audio_nav = self._nav_button("♫  Audio Creator", "Audio Creator")
        self.vietsub_nav.setChecked(True); self.vietsub_nav.clicked.connect(lambda: self._set_page(0)); self.audio_nav.clicked.connect(lambda: self._set_page(1))
        self.english_expanded = False
        self.english_nav = self._nav_button("🌐  Tiếng Anh  ▸", "Tiếng Anh")
        self.english_nav.clicked.connect(self._toggle_english_menu)
        self.english_submenu = QWidget()
        english_submenu_layout = QVBoxLayout(self.english_submenu)
        english_submenu_layout.setContentsMargins(18, 0, 0, 0)
        english_submenu_layout.setSpacing(4)
        self.mini_game_nav = self._subnav_button("🎮  Mini game", "Mini game", 2)
        self.vocabulary_nav = self._subnav_button("📖  Học từ mới", "Học từ mới", 3)
        self.conversation_nav = self._subnav_button("💬  Hội thoại", "Hội thoại", 4)
        self.english_subnav_buttons = (
            self.mini_game_nav,
            self.vocabulary_nav,
            self.conversation_nav,
        )
        for subnav_button in self.english_subnav_buttons:
            english_submenu_layout.addWidget(subnav_button)
        self.english_submenu.hide()
        nav.addWidget(self.vietsub_nav); nav.addWidget(self.audio_nav); nav.addWidget(self.english_nav)
        nav.addWidget(self.english_submenu); nav.addStretch()
        version = QLabel("VietSub v0.2"); version.setObjectName("Subtitle"); version.setAlignment(Qt.AlignmentFlag.AlignCenter); nav.addWidget(version); self.version_label = version
        root.addWidget(self.sidebar)
        self.pages = QStackedWidget(); self.vietsub = VietSubPage(self.project, self.settings_store); self.vietsub.dirty_changed.connect(self.set_dirty)
        self.vietsub.request_new.connect(self.new_project); self.vietsub.request_open.connect(self.open_project); self.vietsub.request_save.connect(self.save_project)
        self.pages.addWidget(self.vietsub)
        self.pages.addWidget(self._audio_placeholder())
        self.minigame = MinigamePage()
        self.minigame.set_project(MinigameProject.from_dict(self.project.minigame_settings))
        self.minigame.dirty_changed.connect(self.set_dirty)
        self.pages.addWidget(self.minigame)
        self.pages.addWidget(EnglishPlaceholderPage(
            "Học từ mới", "📖", "Khu vực mẫu cho bài học từ vựng, flashcard và ôn tập.",
        ))
        self.pages.addWidget(EnglishPlaceholderPage(
            "Hội thoại", "💬", "Khu vực mẫu cho các bài luyện hội thoại theo tình huống.",
        ))
        root.addWidget(self.pages, 1)

    def _nav_button(self, text: str, tooltip: str) -> QPushButton:
        b = QPushButton(text); b.setObjectName("NavButton"); b.setCheckable(True); b.setToolTip(tooltip); b.setMinimumHeight(44); return b

    def _subnav_button(self, text: str, tooltip: str, page_index: int) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("SubNavButton")
        button.setCheckable(True)
        button.setToolTip(tooltip)
        button.setMinimumHeight(38)
        button.clicked.connect(lambda: self._set_page(page_index))
        return button

    def _audio_placeholder(self) -> QWidget:
        page = QWidget(); page.setObjectName("AppRoot"); layout = QVBoxLayout(page); layout.setContentsMargins(50,50,50,50)
        card = QFrame(); card.setObjectName("Card"); card.setMaximumWidth(620); card_layout = QVBoxLayout(card); card_layout.setContentsMargins(36,42,36,42)
        icon = QLabel("♫"); icon.setStyleSheet("font-size:54px;color:#6366f1;"); icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Audio Creator"); title.setObjectName("Title"); title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("Tính năng đang được phát triển"); sub.setObjectName("Subtitle"); sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(icon); card_layout.addWidget(title); card_layout.addWidget(sub); layout.addStretch(); layout.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter); layout.addStretch(); return page

    def _set_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index); self.vietsub_nav.setChecked(index == 0); self.audio_nav.setChecked(index == 1)
        english_page = 2 <= index <= 4
        self.english_nav.setChecked(english_page)
        for page_index, button in enumerate(self.english_subnav_buttons, start=2):
            button.setChecked(index == page_index)
        if english_page:
            self._set_english_menu_expanded(True)

    def _toggle_english_menu(self) -> None:
        if self.sidebar.width() <= 100:
            self.toggle_sidebar()
        self._set_english_menu_expanded(not self.english_expanded)
        self.english_nav.setChecked(2 <= self.pages.currentIndex() <= 4)

    def _set_english_menu_expanded(self, expanded: bool) -> None:
        self.english_expanded = expanded
        self.english_submenu.setVisible(expanded and self.sidebar.width() > 100)
        self.english_nav.setText("🌐  Tiếng Anh  ▾" if expanded else "🌐  Tiếng Anh  ▸")

    def toggle_sidebar(self) -> None:
        collapsed = self.sidebar.width() > 100
        self.sidebar.setFixedWidth(76 if collapsed else 236); self.brand_name.setVisible(not collapsed); self.version_label.setVisible(not collapsed)
        self.vietsub_nav.setText("🎬" if collapsed else "🎬  VietSub"); self.audio_nav.setText("♫" if collapsed else "♫  Audio Creator")
        self.vietsub_nav.setStyleSheet("text-align:center;" if collapsed else ""); self.audio_nav.setStyleSheet("text-align:center;" if collapsed else "")
        self.english_nav.setText("🌐" if collapsed else ("🌐  Tiếng Anh  ▾" if self.english_expanded else "🌐  Tiếng Anh  ▸"))
        self.english_nav.setStyleSheet("text-align:center;" if collapsed else "")
        self.english_submenu.setVisible(not collapsed and self.english_expanded)

    def set_dirty(self, dirty: bool = True) -> None:
        self.dirty = dirty; marker = " • Chưa lưu" if dirty else " • Đã lưu"
        self.vietsub.project_label.setText(f"{self.project.name}{marker}"); self.setWindowTitle(f"Quyết Bồ IT Studio{' *' if dirty else ''}")

    def _confirm_discard(self) -> bool:
        if not self.dirty: return True
        result = QMessageBox.question(self, "Thay đổi chưa lưu", "Project có thay đổi chưa lưu. Bạn có muốn lưu trước khi tiếp tục?", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if result == QMessageBox.StandardButton.Cancel: return False
        if result == QMessageBox.StandardButton.Save: return self.save_project()
        return True

    def new_project(self) -> None:
        if not self._confirm_discard(): return
        self.project = self.settings_store.new_project(); self.project_path = None; self.vietsub.set_project(self.project); self.minigame.set_project(MinigameProject.from_dict(self.project.minigame_settings)); self.set_dirty(False)

    def open_project(self) -> None:
        if not self._confirm_discard(): return
        path, _ = QFileDialog.getOpenFileName(self, "Mở project", "", "Quyết Bồ Project (*.qbstudio.json *.json)")
        if not path: return
        try: project = self.store.load(path)
        except ValueError as exc:
            QMessageBox.critical(self, "Không thể mở project", str(exc)); return
        self.project = project; self.project_path = Path(path)
        if not project.workspace_path:
            project.workspace_path = str(self.project_path.parent / ".qbstudio-work" / project.project_uuid)
        self.vietsub.set_project(project); self.minigame.set_project(MinigameProject.from_dict(project.minigame_settings)); self.set_dirty(False); self.statusBar().showMessage(f"Đã mở {path}", 5000)

    def save_project(self) -> bool:
        if self.project_path is None: return self.save_project_as()
        self._sync_minigame_project()
        try: self.store.save(self.project, self.project_path)
        except OSError as exc:
            QMessageBox.critical(self, "Không thể lưu", str(exc)); return False
        self.set_dirty(False); self.statusBar().showMessage(f"Đã lưu {self.project_path}", 4000); return True

    def save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Lưu project", f"{self.project.name}.qbstudio.json", "Quyết Bồ Project (*.qbstudio.json)")
        if not path: return False
        if not path.endswith(".json"): path += ".qbstudio.json"
        self.project_path = Path(path); self.project.name = self.project_path.name.removesuffix(".qbstudio.json")
        if not self.project.workspace_path and not any(status == "completed" for status in self.project.step_statuses):
            self.project.workspace_path = str(self.project_path.parent / ".qbstudio-work" / self.project.project_uuid)
            self.vietsub.set_project(self.project)
        return self.save_project()

    def _autosave(self) -> None:
        if self.dirty:
            self._sync_minigame_project()
            try: self.store.autosave(self.project, self.autosave_path)
            except OSError: pass

    def recover_autosave(self) -> None:
        if not self.autosave_path.exists():
            QMessageBox.information(self, "Autosave", "Không có phiên autosave để khôi phục.")
            return
        if not self._confirm_discard(): return
        try: project = self.store.load(self.autosave_path)
        except ValueError as exc:
            QMessageBox.critical(self, "Autosave bị lỗi", str(exc)); return
        self.project = project; self.project_path = None; self.vietsub.set_project(project); self.minigame.set_project(MinigameProject.from_dict(project.minigame_settings)); self.set_dirty(True)
        self.statusBar().showMessage("Đã khôi phục phiên autosave", 5000)

    def _sync_minigame_project(self) -> None:
        self.project.minigame_settings = self.minigame._collect_project().to_safe_dict()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard():
            if self.minigame.shutdown():
                event.accept()
            else:
                QMessageBox.warning(
                    self,
                    "Đang dừng Minigame",
                    "Tác vụ TTS/render chưa dừng xong. Hãy chờ một chút rồi đóng ứng dụng lại.",
                )
                event.ignore()
        else: event.ignore()
