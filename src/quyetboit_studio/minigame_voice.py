from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .services.minigame_tts import (
    CapCutVoiceCatalogService,
    CatalogVoice,
    GoogleVoiceCatalogService,
    VoiceCatalogService,
)


class CatalogTaskThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.operation = operation

    def run(self) -> None:
        try:
            self.succeeded.emit(self.operation())
        except Exception as exc:  # noqa: BLE001 - provider failures must reach the dialog
            self.failed.emit(getattr(exc, "user_message", str(exc)))


class MinigameVoiceDialog(QDialog):
    def __init__(
        self,
        *,
        provider: str,
        service_account_path: str,
        selected_voice_id: str,
        selected_language: str,
        volume: int,
        slow_rate: float,
        parent: QWidget | None = None,
        factories: dict[str, Callable[[str], VoiceCatalogService]] | None = None,
        auto_load: bool = True,
    ) -> None:
        super().__init__(parent)
        self.selected_voice_id = selected_voice_id
        self.selected_language = selected_language
        self.voices: list[CatalogVoice] = []
        self.catalog_service: VoiceCatalogService | None = None
        self._tasks: list[CatalogTaskThread] = []
        self.factories = factories or {
            "google": lambda path: GoogleVoiceCatalogService(path),
            "capcut": lambda _path: CapCutVoiceCatalogService(),
        }
        self.setWindowTitle("Cấu hình Voice cho English Mini Game")
        self.resize(900, 700)
        self._build_ui(provider, service_account_path, volume, slow_rate)
        if auto_load:
            self._load(False)

    def _build_ui(
        self, provider: str, service_account_path: str, volume: int, slow_rate: float
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        provider_form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItem("Google Cloud Text-to-Speech", "google")
        self.provider.addItem("CapCut Text-to-Speech", "capcut")
        provider_index = self.provider.findData(provider)
        self.provider.setCurrentIndex(max(0, provider_index))
        account_row = QWidget()
        account_layout = QHBoxLayout(account_row)
        account_layout.setContentsMargins(0, 0, 0, 0)
        self.service_account = QLineEdit(service_account_path)
        self.service_account.setPlaceholderText("Chọn services-account.json…")
        choose_account = QPushButton("Chọn…")
        choose_account.clicked.connect(self._choose_service_account)
        account_layout.addWidget(self.service_account, 1)
        account_layout.addWidget(choose_account)
        self.account_label = QLabel("Service account")
        provider_form.addRow("Provider", self.provider)
        provider_form.addRow(self.account_label, account_row)
        self.account_row = account_row
        root.addLayout(provider_form)

        filter_row = QHBoxLayout()
        self.language = QComboBox()
        self.family = QComboBox()
        self.category = QComboBox()
        self.gender = QComboBox()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm theo tên hoặc voice ID…")
        self.refresh = QPushButton("Tải lại catalog")
        for widget, label in (
            (self.language, "Ngôn ngữ"),
            (self.family, "Loại voice"),
            (self.category, "Danh mục"),
            (self.gender, "Giới tính"),
        ):
            column = QVBoxLayout()
            caption = QLabel(label)
            caption.setObjectName("Subtitle")
            column.addWidget(caption)
            column.addWidget(widget)
            filter_row.addLayout(column)
        search_column = QVBoxLayout()
        search_caption = QLabel("Tìm kiếm")
        search_caption.setObjectName("Subtitle")
        search_column.addWidget(search_caption)
        search_column.addWidget(self.search)
        filter_row.addLayout(search_column, 1)
        filter_row.addWidget(self.refresh, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(filter_row)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        root.addWidget(self.list, 1)
        self.status = QLabel("Sẵn sàng tải catalog voice.")
        self.status.setObjectName("Subtitle")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        settings = QFormLayout()
        self.volume = QSpinBox()
        self.volume.setRange(0, 150)
        self.volume.setSuffix("%")
        self.volume.setValue(max(0, min(150, volume)))
        self.slow_speed = QSpinBox()
        self.slow_speed.setRange(50, 95)
        self.slow_speed.setSuffix("%")
        self.slow_speed.setValue(round(max(0.5, min(0.95, slow_rate)) * 100))
        settings.addRow("Âm lượng", self.volume)
        settings.addRow("Tốc độ lần đọc chậm", self.slow_speed)
        root.addLayout(settings)

        actions = QHBoxLayout()
        self.preview = QPushButton("▶ Nghe thử")
        actions.addWidget(self.preview)
        actions.addStretch()
        root.addLayout(actions)
        self.controls = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        self.controls.accepted.connect(self.accept)
        self.controls.rejected.connect(self.reject)
        root.addWidget(self.controls)

        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.refresh.clicked.connect(lambda: self._load(True))
        self.language.currentIndexChanged.connect(self._filter)
        self.family.currentIndexChanged.connect(self._filter)
        self.category.currentIndexChanged.connect(self._filter)
        self.gender.currentIndexChanged.connect(self._filter)
        self.search.textChanged.connect(self._filter)
        self.list.currentItemChanged.connect(
            lambda _current, _previous: self._set_busy(bool(self._tasks))
        )
        self.list.itemDoubleClicked.connect(lambda _item: self._preview())
        self.preview.clicked.connect(self._preview)
        self._update_provider_fields()
        self._set_busy(False)

    def _provider_changed(self) -> None:
        self.selected_voice_id = ""
        self.voices = []
        self.list.clear()
        self._update_provider_fields()
        self._load(False)

    def _update_provider_fields(self) -> None:
        google = self.provider.currentData() == "google"
        self.account_label.setVisible(google)
        self.account_row.setVisible(google)

    def _choose_service_account(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn Google service account", "", "JSON (*.json)"
        )
        if path:
            self.service_account.setText(path)

    def _load(self, refresh: bool) -> None:
        if self._tasks:
            return
        provider = str(self.provider.currentData())
        credential = self.service_account.text().strip()
        self.status.setText(
            "Đang tải toàn bộ voice Google TTS…"
            if provider == "google"
            else "Đang tải các danh mục voice CapCut…"
        )
        self._set_busy(True)

        def operation() -> tuple[VoiceCatalogService, list[CatalogVoice]]:
            service = self.factories[provider](credential)
            return service, service.list_voices(refresh=refresh)

        self._start_task(operation, self._loaded)

    def _start_task(
        self, operation: Callable[[], object], callback: Callable[[object], None]
    ) -> None:
        task = CatalogTaskThread(operation, self)
        self._tasks.append(task)
        task.succeeded.connect(callback)
        task.failed.connect(self._failed)
        task.finished.connect(lambda: self._finish_task(task))
        task.start()

    def _finish_task(self, task: CatalogTaskThread) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        task.deleteLater()
        self._set_busy(bool(self._tasks))

    def _loaded(self, result: object) -> None:
        service, voices = result  # type: ignore[misc]
        self.catalog_service = service
        self.voices = list(voices)
        self._rebuild_filters()
        self._filter()
        provider_name = "Google TTS" if self.provider.currentData() == "google" else "CapCut"
        self.status.setText(f"Đã tải {len(self.voices)} voice từ {provider_name}.")

    def _rebuild_filters(self) -> None:
        language_codes = sorted({code for voice in self.voices for code in voice.language_codes})
        families = sorted({voice.family for voice in self.voices if voice.family})
        categories = sorted({item for voice in self.voices for item in voice.categories})
        genders = sorted({voice.gender for voice in self.voices if voice.gender})
        for combo in (self.language, self.family, self.category, self.gender):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Tất cả", "")
        self.language.addItem("English — tất cả khu vực", "en")
        for code in language_codes:
            self.language.addItem(code, code)
        for value in families:
            self.family.addItem(value, value)
        for value in categories:
            self.category.addItem(value, value)
        for value in genders:
            self.gender.addItem(value.title(), value)
        language_index = self.language.findData(self.selected_language)
        if language_index < 0 and self.selected_language.startswith("en"):
            language_index = self.language.findData("en")
        self.language.setCurrentIndex(max(0, language_index))
        for combo in (self.language, self.family, self.category, self.gender):
            combo.blockSignals(False)

    def _filter(self) -> None:
        language = str(self.language.currentData() or "")
        family = str(self.family.currentData() or "")
        category = str(self.category.currentData() or "")
        gender = str(self.gender.currentData() or "")
        query = self.search.text().strip().casefold()
        self.list.clear()
        selected_item: QListWidgetItem | None = None
        for voice in self.voices:
            language_match = not language or any(
                code == language or (language == "en" and code.startswith("en-"))
                for code in voice.language_codes
            )
            if (
                not language_match
                or (family and voice.family != family)
                or (category and category not in voice.categories)
                or (gender and voice.gender != gender)
                or (query and query not in f"{voice.name} {voice.voice_id}".casefold())
            ):
                continue
            details = " · ".join(
                value
                for value in (
                    ", ".join(voice.language_codes) or "Không rõ ngôn ngữ",
                    voice.family,
                    voice.gender.title(),
                    ", ".join(voice.categories),
                    f"{voice.sample_rate_hz} Hz" if voice.sample_rate_hz else "",
                )
                if value
            )
            item = QListWidgetItem(f"{voice.name}\n{details}")
            item.setData(Qt.ItemDataRole.UserRole, voice)
            self.list.addItem(item)
            if voice.voice_id == self.selected_voice_id:
                selected_item = item
        if selected_item:
            self.list.setCurrentItem(selected_item)
        elif self.list.count():
            self.list.setCurrentRow(0)
        self._set_busy(bool(self._tasks))

    def selected_voice(self) -> CatalogVoice | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def configuration(self) -> dict[str, object]:
        voice = self.selected_voice()
        if voice is None:
            return {}
        selected_filter = str(self.language.currentData() or "")
        selected_language = voice.primary_language
        if selected_filter in voice.language_codes:
            selected_language = selected_filter
        elif selected_filter == "en":
            selected_language = next(
                (code for code in voice.language_codes if code == "en-US"),
                next((code for code in voice.language_codes if code.startswith("en-")), selected_language),
            )
        return {
            "provider": str(self.provider.currentData()),
            "service_account_path": self.service_account.text().strip(),
            "voice": voice.voice_id,
            "voice_name": voice.name,
            "voice_language": selected_language,
            "voice_gender": voice.gender,
            "voice_family": voice.family,
            "voice_categories": list(voice.categories),
            "volume": self.volume.value(),
            "slow_rate": self.slow_speed.value() / 100,
        }

    def _preview(self) -> None:
        voice = self.selected_voice()
        if voice is None or self.catalog_service is None:
            return
        self.status.setText(f"Đang tạo audio nghe thử: {voice.name}…")
        self._set_busy(True)
        self._start_task(lambda: self.catalog_service.preview(voice), self._play_preview)

    def _play_preview(self, target: object) -> None:
        value = str(target)
        url = QUrl(value) if value.startswith(("http://", "https://")) else QUrl.fromLocalFile(value)
        if QDesktopServices.openUrl(url):
            self.status.setText("Đã mở audio nghe thử bằng trình phát mặc định.")
        else:
            self.status.setText("Không mở được audio nghe thử.")

    def _failed(self, message: str) -> None:
        self.status.setText(message)

    def _set_busy(self, busy: bool) -> None:
        has_voice = self.list.currentItem() is not None
        self.refresh.setEnabled(not busy)
        self.provider.setEnabled(not busy)
        self.preview.setEnabled(not busy and has_voice)
        save = self.controls.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(not busy and has_voice)

    def reject(self) -> None:
        if self._tasks:
            self.status.setText("Vui lòng chờ request hiện tại kết thúc trước khi đóng.")
            return
        super().reject()
