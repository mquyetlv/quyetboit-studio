from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .services.minigame_tts import (
    CapCutVoiceCatalogService,
    CatalogVoice,
    GoogleVoiceCatalogService,
    VoiceCatalogService,
)

STYLE = """
QDialog#MinigameVoiceDialog { background:#f8fafc; }
QLabel#VoiceTitle { color:#111827;font-size:24px;font-weight:700; }
QLabel#VoiceSubtitle,QLabel#MutedText { color:#667085; }
QLabel#FieldLabel { color:#344054;font-weight:600; }
QFrame#ProviderSettings,QFrame#FilterBar,QFrame#VoicePreviewPanel {
 background:#fff;border:1px solid #e5e7eb;border-radius:12px; }
QFrame#ProviderSettings[invalid="true"] { border-color:#f04438;background:#fffbfa; }
QLabel#ConnectionBadge { color:#15803d;background:#f0fdf4;border:1px solid #bbf7d0;
 border-radius:8px;padding:5px 9px;font-weight:600; }
QLabel#ConnectionBadge[error="true"] { color:#b42318;background:#fef3f2;border-color:#fecdca; }
QLineEdit#VoiceSearch { min-height:32px;padding:4px 12px;font-size:14px; }
QPushButton#RefreshVoices { min-height:32px;padding:4px 13px; }
QListWidget#VoiceCatalog { background:#fff;border:0;padding:0;outline:none; }
QListWidget#VoiceCatalog::item,QListWidget#VoiceCatalog::item:selected { border:0;background:transparent; }
QFrame#VoiceCard { background:#fff;border:1px solid #e5e7eb;border-radius:10px; }
QFrame#VoiceCard:hover { background:#fcfaff;border-color:#b692f6; }
QFrame#VoiceCard[selected="true"] { background:#f5f3ff;border:2px solid #7c3aed; }
QLabel#VoiceAvatar { background:#ede9fe;color:#6d28d9;border-radius:18px;font-size:12px;font-weight:800; }
QLabel#PreviewAvatar { background:#ede9fe;color:#6d28d9;border-radius:25px;font-size:15px;font-weight:800; }
QLabel#VoiceName { color:#111827;font-size:13px;font-weight:600; }
QLabel#VoiceMetadata { color:#98a2b3;font-size:11px; }
QLabel#VoiceCheck { color:#fff;background:#7c3aed;border-radius:8px;font-size:10px;font-weight:800; }
QFrame#CatalogSurface { background:#fff;border:1px solid #e5e7eb;border-radius:12px; }
QFrame#CatalogState { background:#fff; }
QFrame#SkeletonBar { background:#f2f4f7;border-radius:8px; }
QPlainTextEdit#PreviewText { min-height:86px; }
QPushButton#PreviewVoice { color:#6d28d9;border-color:#d8b4fe;font-weight:600; }
QPushButton#SaveVoice { background:#7c3aed;color:#fff;border-color:#7c3aed;font-weight:700; }
QPushButton#SaveVoice:hover { background:#6d28d9; }
QSlider::groove:horizontal { height:4px;background:#e5e7eb;border-radius:2px; }
QSlider::sub-page:horizontal { background:#7c3aed;border-radius:2px; }
QSlider::handle:horizontal { background:#7c3aed;border:2px solid #fff;width:14px;
 margin:-6px 0;border-radius:7px; }
"""


def initials(name: str) -> str:
    words = [word for word in name.replace("-", " ").split() if word]
    return "".join(word[0] for word in words[:2]).upper() or "V"


def metadata(voice: CatalogVoice) -> str:
    language = ", ".join(voice.language_codes) or "Không rõ ngôn ngữ"
    gender = voice.gender.title() if voice.gender else "Chưa rõ giới tính"
    return f"{language}  ·  {gender}"


class CatalogTaskThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], object], parent: QWidget | None = None) -> None:
        super().__init__(parent); self.operation = operation

    def run(self) -> None:
        try: self.succeeded.emit(self.operation())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(getattr(exc, "user_message", str(exc)))


class ProviderSettings(QFrame):
    choose_requested = Signal()

    def __init__(self, provider: str, path: str) -> None:
        super().__init__(); self.setObjectName("ProviderSettings"); self.setProperty("invalid", False)
        grid = QGridLayout(self); grid.setContentsMargins(18, 14, 18, 14); grid.setSpacing(8)
        self.account_label = QLabel("Service account")
        for col, label in enumerate((QLabel("Provider"), self.account_label)):
            label.setObjectName("FieldLabel"); grid.addWidget(label, 0, col)
        self.provider = QComboBox(); self.provider.setAccessibleName("Provider giọng đọc")
        self.provider.addItem("Google Cloud Text-to-Speech", "google")
        self.provider.addItem("CapCut Text-to-Speech", "capcut")
        self.provider.setCurrentIndex(max(0, self.provider.findData(provider)))
        self.path = QLineEdit(path); self.path.setPlaceholderText("Chọn services-account.json…")
        self.path.setAccessibleName("Đường dẫn Google service account")
        self.choose = QPushButton("▣  Chọn file"); self.choose.clicked.connect(self.choose_requested)
        self.badge = QLabel("Chưa kiểm tra"); self.badge.setObjectName("ConnectionBadge")
        self.badge.setProperty("error", False)
        grid.addWidget(self.provider, 1, 0); grid.addWidget(self.path, 1, 1)
        grid.addWidget(self.choose, 1, 2); grid.addWidget(self.badge, 1, 3)
        grid.setColumnStretch(0, 4); grid.setColumnStretch(1, 5)

    def show_google(self, visible: bool) -> None:
        for widget in (self.account_label, self.path, self.choose, self.badge): widget.setVisible(visible)

    def set_state(self, state: str, text: str) -> None:
        invalid = state == "error"; self.setProperty("invalid", invalid)
        self.badge.setProperty("error", invalid); self.badge.setText(text)
        for widget in (self, self.badge):
            widget.style().unpolish(widget); widget.style().polish(widget)


class FilterBar(QFrame):
    def __init__(self) -> None:
        super().__init__(); self.setObjectName("FilterBar")
        grid = QGridLayout(self); grid.setContentsMargins(16, 11, 16, 11); grid.setSpacing(8)
        for col, text in enumerate(("Ngôn ngữ", "Giới tính", "Tìm kiếm")):
            label = QLabel(text); label.setObjectName("FieldLabel"); grid.addWidget(label, 0, col)
        self.language = QComboBox(); self.gender = QComboBox()
        self.search = QLineEdit(); self.search.setObjectName("VoiceSearch")
        self.search.setClearButtonEnabled(True); self.search.setPlaceholderText("Tìm tên giọng đọc…")
        self.refresh = QPushButton("↻  Làm mới từ Google"); self.refresh.setObjectName("RefreshVoices")
        grid.addWidget(self.language, 1, 0); grid.addWidget(self.gender, 1, 1)
        grid.addWidget(self.search, 1, 2); grid.addWidget(self.refresh, 1, 3)
        grid.setColumnMinimumWidth(0, 155); grid.setColumnMinimumWidth(1, 135); grid.setColumnStretch(2, 1)


class VoiceCard(QFrame):
    def __init__(self, voice: CatalogVoice) -> None:
        super().__init__(); self.setObjectName("VoiceCard"); self.setProperty("selected", False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row = QHBoxLayout(self); row.setContentsMargins(9, 7, 9, 7); row.setSpacing(9)
        avatar = QLabel(initials(voice.name)); avatar.setObjectName("VoiceAvatar")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter); avatar.setFixedSize(36, 36); row.addWidget(avatar)
        details = QVBoxLayout(); details.setSpacing(1)
        name = QLabel(voice.name); name.setObjectName("VoiceName"); name.setToolTip(voice.name)
        meta = QLabel(metadata(voice)); meta.setObjectName("VoiceMetadata")
        details.addWidget(name); details.addWidget(meta); row.addLayout(details, 1)
        self.check = QLabel("✓"); self.check.setObjectName("VoiceCheck")
        self.check.setAlignment(Qt.AlignmentFlag.AlignCenter); self.check.setFixedSize(16, 16)
        self.check.hide(); row.addWidget(self.check, 0, Qt.AlignmentFlag.AlignTop)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected); self.check.setVisible(selected)
        self.style().unpolish(self); self.style().polish(self)


class CatalogState(QFrame):
    def __init__(self) -> None:
        super().__init__(); self.setObjectName("CatalogState")
        root = QVBoxLayout(self); root.setContentsMargins(24, 24, 24, 24)
        self.skeleton = QWidget(); grid = QGridLayout(self.skeleton); grid.setSpacing(8)
        for index in range(16):
            bar = QFrame(); bar.setObjectName("SkeletonBar"); bar.setMinimumHeight(56)
            grid.addWidget(bar, index // 4, index % 4)
        self.icon = QLabel("◌"); self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setStyleSheet("font-size:28px;color:#7c3aed;")
        self.message = QLabel(); self.message.setObjectName("MutedText")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter); self.message.setWordWrap(True)
        root.addWidget(self.skeleton, 1); root.addWidget(self.icon); root.addWidget(self.message); root.addStretch()

    def loading(self) -> None:
        self.skeleton.show(); self.icon.setText("◌"); self.message.setText("Đang tải danh sách giọng đọc mới nhất…")

    def show_message(self, icon: str, text: str) -> None:
        self.skeleton.hide(); self.icon.setText(icon); self.message.setText(text)


class VoicePreviewPanel(QFrame):
    preview_requested = Signal()
    DEFAULT_TEXT = "Hello! This is a sample preview of the voice.\nThis is how it will sound in your video."

    def __init__(self, volume: int, rate: float) -> None:
        super().__init__(); self.setObjectName("VoicePreviewPanel")
        self.setMinimumWidth(330); self.setMaximumWidth(390)
        root = QVBoxLayout(self); root.setContentsMargins(18, 18, 18, 18); root.setSpacing(11)
        identity = QHBoxLayout(); self.avatar = QLabel("V"); self.avatar.setObjectName("PreviewAvatar")
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter); self.avatar.setFixedSize(50, 50)
        names = QVBoxLayout(); self.name = QLabel("Chưa chọn giọng"); self.name.setObjectName("VoiceName")
        self.name.setWordWrap(True); self.meta = QLabel("Chọn một voice trong danh sách")
        self.meta.setObjectName("VoiceMetadata"); names.addWidget(self.name); names.addWidget(self.meta)
        identity.addWidget(self.avatar); identity.addLayout(names, 1); root.addLayout(identity)
        label = QLabel("Nội dung nghe thử"); label.setObjectName("FieldLabel"); root.addWidget(label)
        self.text = QPlainTextEdit(self.DEFAULT_TEXT); self.text.setObjectName("PreviewText"); root.addWidget(self.text)
        self.count = QLabel(); self.count.setObjectName("MutedText"); self.count.setAlignment(Qt.AlignmentFlag.AlignRight)
        root.addWidget(self.count); self.text.textChanged.connect(self._limit)
        self.volume_slider, self.volume = self._setting(root, "Âm lượng", 0, 150, max(0, min(150, volume)))
        value = round(max(0.5, min(0.95, rate)) * 100)
        self.speed_slider, self.slow_speed = self._setting(
            root, "Tốc độ lần đọc chậm", 50, 95, value
        )
        root.addStretch(); self.preview = QPushButton("▶  Nghe thử"); self.preview.setObjectName("PreviewVoice")
        self.preview.clicked.connect(self.preview_requested); root.addWidget(self.preview); self._limit()

    @staticmethod
    def _setting(root: QVBoxLayout, text: str, low: int, high: int, value: int) -> tuple[QSlider, QSpinBox]:
        label = QLabel(text); label.setObjectName("FieldLabel"); root.addWidget(label)
        row = QHBoxLayout(); slider = QSlider(Qt.Orientation.Horizontal); slider.setRange(low, high); slider.setValue(value)
        spin = QSpinBox(); spin.setRange(low, high); spin.setSuffix("%"); spin.setValue(value); spin.setFixedWidth(76)
        slider.valueChanged.connect(spin.setValue); spin.valueChanged.connect(slider.setValue)
        row.addWidget(slider, 1); row.addWidget(spin); root.addLayout(row); return slider, spin

    def _limit(self) -> None:
        value = self.text.toPlainText()
        if len(value) > 300:
            self.text.blockSignals(True); self.text.setPlainText(value[:300]); self.text.blockSignals(False); value = value[:300]
        self.count.setText(f"{len(value)} / 300")

    def set_voice(self, voice: CatalogVoice | None) -> None:
        self.avatar.setText(initials(voice.name) if voice else "V")
        self.name.setText(voice.name if voice else "Chưa chọn giọng")
        self.meta.setText(metadata(voice) if voice else "Chọn một voice trong danh sách")

    def preview_text(self) -> str: return self.text.toPlainText().strip() or self.DEFAULT_TEXT


class MinigameVoiceDialog(QDialog):
    def __init__(self, *, provider: str, service_account_path: str, selected_voice_id: str,
                 selected_language: str, volume: int, slow_rate: float, parent: QWidget | None = None,
                 factories: dict[str, Callable[[str], VoiceCatalogService]] | None = None,
                 auto_load: bool = True) -> None:
        super().__init__(parent); self.selected_voice_id = selected_voice_id; self.selected_language = selected_language
        self.voices: list[CatalogVoice] = []; self.catalog_service: VoiceCatalogService | None = None
        self._tasks: list[CatalogTaskThread] = []; self._catalog_loading = False
        self.factories = factories or {"google": lambda path: GoogleVoiceCatalogService(path),
                                       "capcut": lambda _path: CapCutVoiceCatalogService()}
        self.setObjectName("MinigameVoiceDialog"); self.setWindowTitle("Cấu hình Voice cho English Mini Game")
        self.setMinimumSize(1_080, 720); self.resize(1_480, 900); self.setStyleSheet(STYLE)
        self._build_ui(provider, service_account_path, volume, slow_rate)
        if auto_load: self._load(False)

    def _build_ui(self, provider: str, path: str, volume: int, rate: float) -> None:
        root = QVBoxLayout(self); root.setContentsMargins(24, 20, 24, 18); root.setSpacing(14)
        header = QHBoxLayout(); icon = QLabel("▥"); icon.setAlignment(Qt.AlignmentFlag.AlignCenter); icon.setFixedSize(34, 34)
        icon.setStyleSheet("background:#ede9fe;color:#7c3aed;border-radius:10px;font-size:18px;font-weight:700;")
        text = QVBoxLayout(); title = QLabel("Chọn giọng đọc"); title.setObjectName("VoiceTitle")
        sub = QLabel("Chọn một giọng phù hợp, nghe thử và lưu cấu hình cho video."); sub.setObjectName("VoiceSubtitle")
        text.addWidget(title); text.addWidget(sub); header.addWidget(icon); header.addLayout(text); header.addStretch(); root.addLayout(header)
        self.provider_settings = ProviderSettings(provider, path); root.addWidget(self.provider_settings)
        self.provider = self.provider_settings.provider; self.service_account = self.provider_settings.path
        self.account_label = self.provider_settings.account_label; self.account_row = self.service_account
        self.provider_settings.choose_requested.connect(self._choose_service_account)
        self.filter_bar = FilterBar(); root.addWidget(self.filter_bar)
        self.language = self.filter_bar.language; self.gender = self.filter_bar.gender
        self.search = self.filter_bar.search; self.refresh = self.filter_bar.refresh
        content = QHBoxLayout(); content.setSpacing(14); surface = QFrame(); surface.setObjectName("CatalogSurface")
        box = QVBoxLayout(surface); box.setContentsMargins(10, 10, 10, 10); self.catalog_stack = QStackedWidget()
        self.list = QListWidget(); self.list.setObjectName("VoiceCatalog"); self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust); self.list.setMovement(QListView.Movement.Static)
        self.list.setWrapping(True); self.list.setGridSize(QSize(246, 62)); self.list.setSpacing(4)
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.catalog_state = CatalogState(); self.catalog_stack.addWidget(self.list); self.catalog_stack.addWidget(self.catalog_state)
        box.addWidget(self.catalog_stack); content.addWidget(surface, 7)
        self.preview_panel = VoicePreviewPanel(volume, rate); content.addWidget(self.preview_panel, 3); root.addLayout(content, 1)
        self.volume = self.preview_panel.volume; self.slow_speed = self.preview_panel.slow_speed; self.preview = self.preview_panel.preview
        footer = QHBoxLayout(); footer.addWidget(QLabel("ⓘ")); self.status = QLabel("Sẵn sàng tải catalog voice.")
        self.status.setObjectName("MutedText"); footer.addWidget(self.status, 1)
        self.controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        self.controls.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        save = self.controls.button(QDialogButtonBox.StandardButton.Save); save.setText("Lưu & đóng"); save.setObjectName("SaveVoice")
        self.controls.accepted.connect(self.accept); self.controls.rejected.connect(self.reject); footer.addWidget(self.controls); root.addLayout(footer)
        self.provider.currentIndexChanged.connect(self._provider_changed); self.refresh.clicked.connect(lambda: self._load(True))
        self.language.currentIndexChanged.connect(self._filter); self.gender.currentIndexChanged.connect(self._filter)
        self.search.textChanged.connect(self._filter); self.list.currentItemChanged.connect(self._selection_changed)
        self.list.itemDoubleClicked.connect(lambda _item: self._preview()); self.preview_panel.preview_requested.connect(self._preview)
        self._update_provider_fields(); self._show_message("⌁", "Chọn provider và tải danh sách giọng đọc."); self._set_busy(False)

    def _provider_changed(self) -> None:
        self.selected_voice_id = ""; self.voices = []; self.list.clear(); self.preview_panel.set_voice(None)
        self._update_provider_fields(); self._load(False)

    def _update_provider_fields(self) -> None:
        google = self.provider.currentData() == "google"; self.provider_settings.show_google(google)
        self.refresh.setText("↻  Làm mới từ Google" if google else "↻  Làm mới catalog")

    def _choose_service_account(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn Google service account", "", "JSON (*.json)")
        if path: self.service_account.setText(path); self.provider_settings.set_state("idle", "Chưa kiểm tra")

    def _load(self, refresh: bool) -> None:
        if self._tasks: return
        provider = str(self.provider.currentData()); credential = self.service_account.text().strip()
        self._catalog_loading = True; self.catalog_state.loading(); self.catalog_stack.setCurrentWidget(self.catalog_state)
        self.status.setText("Đang tải toàn bộ voice Google TTS…" if provider == "google" else "Đang tải voice CapCut…")
        self._set_busy(True)
        def operation() -> tuple[VoiceCatalogService, list[CatalogVoice]]:
            service = self.factories[provider](credential); return service, service.list_voices(refresh=refresh)
        self._start_task(operation, self._loaded)

    def _start_task(self, operation: Callable[[], object], callback: Callable[[object], None]) -> None:
        task = CatalogTaskThread(operation, self); self._tasks.append(task); task.succeeded.connect(callback)
        task.failed.connect(self._failed); task.finished.connect(lambda: self._finish_task(task)); task.start()

    def _finish_task(self, task: CatalogTaskThread) -> None:
        if task in self._tasks: self._tasks.remove(task)
        task.deleteLater(); self._set_busy(bool(self._tasks))

    def _loaded(self, result: object) -> None:
        service, voices = result  # type: ignore[misc]
        self._catalog_loading = False; self.catalog_service = service; self.voices = list(voices)
        self._rebuild_filters(); self._filter(); provider = "Google TTS" if self.provider.currentData() == "google" else "CapCut"
        self.status.setText(f"Đã tải {len(self.voices)} voice từ {provider}.")
        if self.provider.currentData() == "google": self.provider_settings.set_state("success", "✓ Đã kết nối")

    def _rebuild_filters(self) -> None:
        languages = sorted({code for voice in self.voices for code in voice.language_codes})
        genders = sorted({voice.gender for voice in self.voices if voice.gender})
        for combo in (self.language, self.gender): combo.blockSignals(True); combo.clear(); combo.addItem("Tất cả", "")
        self.language.addItem("English — tất cả khu vực", "en")
        for code in languages: self.language.addItem(code, code)
        for value in genders: self.gender.addItem(value.title(), value)
        index = self.language.findData(self.selected_language)
        if index < 0 and self.selected_language.startswith("en"): index = self.language.findData("en")
        self.language.setCurrentIndex(max(0, index))
        for combo in (self.language, self.gender): combo.blockSignals(False)

    def _filter(self) -> None:
        if self._catalog_loading: return
        language = str(self.language.currentData() or ""); gender = str(self.gender.currentData() or "")
        query = self.search.text().strip().casefold(); self.list.clear(); chosen: QListWidgetItem | None = None
        for voice in self.voices:
            language_match = not language or any(code == language or (language == "en" and code.startswith("en-")) for code in voice.language_codes)
            if not language_match or (gender and voice.gender != gender) or (query and query not in voice.name.casefold()): continue
            item = QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole, voice); item.setSizeHint(QSize(242, 58))
            self.list.addItem(item); self.list.setItemWidget(item, VoiceCard(voice))
            if voice.voice_id == self.selected_voice_id: chosen = item
        if not self.list.count(): self.preview_panel.set_voice(None); self._show_message("⌕", "Không tìm thấy voice phù hợp. Hãy đổi bộ lọc hoặc từ khóa.")
        else:
            self.catalog_stack.setCurrentWidget(self.list); self.list.setCurrentItem(chosen) if chosen else self.list.setCurrentRow(0)
        self._set_busy(bool(self._tasks))

    def _show_message(self, icon: str, text: str) -> None:
        self.catalog_state.show_message(icon, text); self.catalog_stack.setCurrentWidget(self.catalog_state)

    def _selection_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        for item, selected in ((previous, False), (current, True)):
            if item is not None and isinstance(card := self.list.itemWidget(item), VoiceCard): card.set_selected(selected)
        voice = current.data(Qt.ItemDataRole.UserRole) if current else None
        if isinstance(voice, CatalogVoice): self.selected_voice_id = voice.voice_id; self.preview_panel.set_voice(voice)
        else: self.preview_panel.set_voice(None)
        self._set_busy(bool(self._tasks))

    @staticmethod
    def _initials(name: str) -> str: return initials(name)

    def selected_voice(self) -> CatalogVoice | None:
        item = self.list.currentItem(); return item.data(Qt.ItemDataRole.UserRole) if item else None

    def configuration(self) -> dict[str, object]:
        voice = self.selected_voice()
        if voice is None: return {}
        selected_filter = str(self.language.currentData() or ""); selected_language = voice.primary_language
        if selected_filter in voice.language_codes: selected_language = selected_filter
        elif selected_filter == "en":
            selected_language = next((c for c in voice.language_codes if c == "en-US"), next((c for c in voice.language_codes if c.startswith("en-")), selected_language))
        return {"provider": str(self.provider.currentData()), "service_account_path": self.service_account.text().strip(),
                "voice": voice.voice_id, "voice_name": voice.name, "voice_language": selected_language,
                "voice_gender": voice.gender, "voice_family": voice.family, "voice_categories": list(voice.categories),
                "volume": self.volume.value(), "slow_rate": self.slow_speed.value() / 100}

    def _preview(self) -> None:
        voice = self.selected_voice()
        if voice is None or self.catalog_service is None: return
        self.status.setText(f"Đang tạo audio nghe thử: {voice.name}…"); self._set_busy(True)
        self._start_task(lambda: self.catalog_service.preview(voice, self.preview_panel.preview_text()), self._play_preview)

    def _play_preview(self, target: object) -> None:
        value = str(target); url = QUrl(value) if value.startswith(("http://", "https://")) else QUrl.fromLocalFile(value)
        self.status.setText("Đã mở audio nghe thử bằng trình phát mặc định." if QDesktopServices.openUrl(url) else "Không mở được audio nghe thử.")

    def _failed(self, message: str) -> None:
        self.status.setText(message)
        if self._catalog_loading:
            self._catalog_loading = False; self._show_message("!", f"Không thể tải catalog.\n{message}")
            if self.provider.currentData() == "google": self.provider_settings.set_state("error", "Lỗi kết nối")

    def _set_busy(self, busy: bool) -> None:
        has_voice = self.list.currentItem() is not None
        for widget in (self.refresh, self.provider, self.service_account, self.provider_settings.choose,
                       self.language, self.gender, self.search): widget.setEnabled(not busy)
        self.preview.setEnabled(not busy and has_voice)
        self.preview.setText("◌  Đang tạo audio…" if busy and not self._catalog_loading else "▶  Nghe thử")
        self.refresh.setText("◌  Đang tải…" if self._catalog_loading else ("↻  Làm mới từ Google" if self.provider.currentData() == "google" else "↻  Làm mới catalog"))
        self.controls.button(QDialogButtonBox.StandardButton.Save).setEnabled(not busy and has_voice)

    def reject(self) -> None:
        if self._tasks: self.status.setText("Vui lòng chờ request hiện tại kết thúc trước khi đóng."); return
        super().reject()
