from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QSize, QStandardPaths, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .core.minigame import MinigameFormat, MinigameProject, MinigameTtsProvider, VocabularyItem
from .core.models import PipelineError, ProgressEvent
from .minigame_voice import MinigameVoiceDialog
from .pipeline.minigame import MinigamePipeline, MinigameResult
from .services.media.minigame_text import MinigameTextLayerService
from .services.media.minigame_theme import MinigameThemeService


class MinigameWorker(QObject):
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(self, pipeline: MinigamePipeline) -> None:
        super().__init__()
        self.pipeline = pipeline

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self.pipeline.run(self._progress))
        except Exception as exc:  # noqa: BLE001 - forward worker failures to UI
            self.failed.emit(exc)
        finally:
            self.finished.emit()

    def _progress(self, event: ProgressEvent) -> None:
        self.progress.emit(round(event.fraction * 100), event.message)


class MinigamePreviewWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(self, theme_id: str, topic: str, total: int, cache_dir: Path) -> None:
        super().__init__()
        self.theme_id = theme_id
        self.topic = topic
        self.total = total
        self.cache_dir = cache_dir

    @Slot()
    def run(self) -> None:
        try:
            manifest = MinigameThemeService().get(self.theme_id)
            project = MinigameProject(
                topic=self.topic,
                theme_id=self.theme_id,
                vocabulary=[VocabularyItem("", "", "", "") for _ in range(self.total)],
            )
            text_path = MinigameTextLayerService().build_hook(project, manifest, self.cache_dir)
            self.succeeded.emit((manifest, text_path, self.topic, self.total))
        except Exception as exc:  # noqa: BLE001 - report preview failure to Qt UI
            self.failed.emit(exc)
        finally:
            self.finished.emit()


class MinigamePage(QWidget):
    dirty_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("AppRoot")
        self.thread: QThread | None = None
        self.worker: MinigameWorker | None = None
        self.pipeline: MinigamePipeline | None = None
        self.preview_thread: QThread | None = None
        self.preview_worker: MinigamePreviewWorker | None = None
        self._preview_pending = False
        self._preview_base = QPixmap()
        self._preview_manifest = None
        self.last_outputs: list[Path] = []
        self.project_uuid = MinigameProject().project_uuid
        self._loading_project = False
        default_account = Path.cwd() / "services-account.json"
        self.voice_provider = MinigameTtsProvider.GOOGLE.value
        self.voice_id = "en-US-Neural2-F"
        self.voice_name = "en-US-Neural2-F"
        self.voice_language = "en-US"
        self.voice_gender = ""
        self.voice_family = "Neural2"
        self.voice_categories: list[str] = ["Neural2", "en"]
        self.service_account_path = str(default_account.resolve()) if default_account.is_file() else ""
        self.voice_volume = 100
        self.slow_rate = 0.72
        self.theme_service = MinigameThemeService()
        self.text_layers = MinigameTextLayerService()
        cache_location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        self.preview_cache = Path(cache_location) / "minigame-preview"
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(180)
        self.preview_timer.timeout.connect(self._start_preview_render)
        self._build_ui()
        self._set_word_count(5)
        self._update_preview()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._update_preview()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 26, 34, 26)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("English Mini Game")
        title.setObjectName("Title")
        subtitle = QLabel("Tạo video đoán từ, luyện phát âm và ôn tập từ vựng")
        subtitle.setObjectName("Subtitle")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()
        root.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        columns = QHBoxLayout(content)
        columns.setContentsMargins(0, 16, 0, 0)
        columns.setSpacing(18)
        columns.addWidget(self._configuration_panel(), 3)
        columns.addWidget(self._preview_panel(), 2)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        return card, layout

    def _configuration_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        source_card, source = self._card("1. Giao diện và định dạng")
        source_form = QFormLayout()
        self.aspect = QComboBox()
        self.aspect.addItem("YouTube 16:9", MinigameFormat.YOUTUBE.value)
        self.aspect.addItem("TikTok / Shorts 9:16", MinigameFormat.VERTICAL.value)
        self.aspect.addItem("Xuất cả hai", MinigameFormat.BOTH.value)
        self.aspect.model().item(0).setEnabled(False)
        self.aspect.model().item(2).setEnabled(False)
        self.aspect.setCurrentIndex(1)
        self.aspect.setToolTip("Bộ asset hiện chỉ được thiết kế cho video dọc 9:16.")
        self.aspect.currentIndexChanged.connect(self._update_preview)
        self.theme_id = QComboBox()
        self.theme_cards: list[QToolButton] = []
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        theme_cards_widget = QWidget()
        theme_cards_layout = QGridLayout(theme_cards_widget)
        theme_cards_layout.setContentsMargins(0, 0, 0, 0)
        theme_cards_layout.setSpacing(8)
        for theme_id, manifest in self.theme_service.discover().items():
            thumbnail = QPixmap(str(manifest.start_screen)).scaledToHeight(72)
            self.theme_id.addItem(QIcon(thumbnail), manifest.display_name, theme_id)
            card = QToolButton()
            card.setCheckable(True)
            card.setIcon(QIcon(str(manifest.start_screen)))
            card.setIconSize(QSize(72, 128))
            card.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            palette = " · ".join(manifest.palette)
            card.setText(f"{manifest.display_name}\n9:16\n{palette}")
            card.setToolTip(f"{manifest.display_name} · hỗ trợ: {', '.join(manifest.supported_formats)}")
            index = self.theme_id.count() - 1
            card.clicked.connect(lambda _checked=False, value=index: self.theme_id.setCurrentIndex(value))
            self.theme_group.addButton(card, index)
            self.theme_cards.append(card)
            theme_cards_layout.addWidget(card, index // 3, index % 3)
        self.theme_cards[0].setChecked(True)
        self.theme_id.setVisible(False)
        self.theme_id.currentIndexChanged.connect(self._update_preview)
        self.theme_id.currentIndexChanged.connect(self._sync_theme_card)
        video_row = QWidget()
        video_layout = QHBoxLayout(video_row)
        video_layout.setContentsMargins(0, 0, 0, 0)
        self.video_path = QLineEdit()
        self.video_path.setPlaceholderText("Chọn video MP4/MOV/MKV…")
        self.video_path.textChanged.connect(self._update_preview)
        choose_video = QPushButton("Chọn…")
        choose_video.clicked.connect(self._choose_video)
        video_layout.addWidget(self.video_path, 1)
        video_layout.addWidget(choose_video)
        source.addWidget(theme_cards_widget)
        source_form.addRow("Tỷ lệ", self.aspect)
        source_form.addRow("Video nền legacy", video_row)
        source.addLayout(source_form)
        audio_note = QLabel(
            "Theme dùng ảnh nền có sẵn và chỉ hỗ trợ 9:16. Video nền legacy được giữ để mở project cũ."
        )
        audio_note.setObjectName("Subtitle")
        audio_note.setWordWrap(True)
        source.addWidget(audio_note)
        for aspect in ("16:9", "9:16"):
            row = QHBoxLayout()
            label = QLabel(f"Crop {aspect}")
            x = QSlider(Qt.Orientation.Horizontal)
            y = QSlider(Qt.Orientation.Horizontal)
            for slider in (x, y):
                slider.setRange(0, 100)
                slider.setValue(50)
                slider.valueChanged.connect(self._update_preview)
            row.addWidget(label)
            row.addWidget(QLabel("X"))
            row.addWidget(x)
            row.addWidget(QLabel("Y"))
            row.addWidget(y)
            source.addLayout(row)
            if aspect == "16:9":
                for widget in (x, y):
                    widget.setEnabled(False)
            if aspect == "16:9":
                self.crop_16_x, self.crop_16_y = x, y
            else:
                self.crop_9_x, self.crop_9_y = x, y
        layout.addWidget(source_card)

        content_card, vocabulary_layout = self._card("2. Nội dung bắt buộc")
        top = QHBoxLayout()
        top.addWidget(QLabel("Topic"))
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("Ví dụ: Travel, Food, Animals…")
        self.topic.textChanged.connect(self._update_preview)
        top.addWidget(self.topic, 1)
        top.addWidget(QLabel("Số từ"))
        self.word_count = QSpinBox()
        self.word_count.setRange(1, 50)
        self.word_count.setValue(5)
        self.word_count.valueChanged.connect(self._set_word_count)
        top.addWidget(self.word_count)
        paste = QPushButton("Dán bảng")
        paste.setToolTip("Dán 4 cột: Nghĩa Việt, English, IPA, Câu ví dụ")
        paste.clicked.connect(self._paste_table)
        top.addWidget(paste)
        vocabulary_layout.addLayout(top)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Nghĩa tiếng Việt", "English", "IPA", "Câu ví dụ tiếng Anh"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._update_preview)
        vocabulary_layout.addWidget(self.table)
        layout.addWidget(content_card)

        voice_card, voice = self._card("3. Voice")
        self.voice_summary = QLabel()
        self.voice_summary.setWordWrap(True)
        self.voice_summary.setObjectName("Subtitle")
        self.voice_config_button = QPushButton("Cấu hình Voice…")
        self.voice_config_button.clicked.connect(self._open_voice_dialog)
        voice.addWidget(self.voice_summary)
        voice.addWidget(self.voice_config_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._update_voice_summary()
        layout.addWidget(voice_card)

        output_card, output = self._card("4. Xuất video")
        output_form = QFormLayout()
        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        self.output_folder = QLineEdit(str((Path.cwd() / "output").resolve()))
        choose_folder = QPushButton("Chọn…")
        choose_folder.clicked.connect(self._choose_output_folder)
        folder_layout.addWidget(self.output_folder, 1)
        folder_layout.addWidget(choose_folder)
        self.output_name = QLineEdit("english_minigame")
        output_form.addRow("Thư mục", folder_row)
        output_form.addRow("Tên video", self.output_name)
        output.addLayout(output_form)
        actions = QHBoxLayout()
        self.render_button = QPushButton("Tạo video")
        self.render_button.setObjectName("Primary")
        self.render_button.clicked.connect(self._start_render)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("Danger")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        self.open_button = QPushButton("Mở thư mục")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_output)
        actions.addWidget(self.render_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.open_button)
        output.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        output.addWidget(self.progress)
        self.status = QLabel("Sẵn sàng")
        self.status.setObjectName("Subtitle")
        self.status.setWordWrap(True)
        output.addWidget(self.status)
        layout.addWidget(output_card)
        layout.addStretch()
        return panel

    @Slot(int)
    def _sync_theme_card(self, index: int) -> None:
        if 0 <= index < len(self.theme_cards):
            self.theme_cards[index].setChecked(True)

    def _preview_panel(self) -> QWidget:
        card, layout = self._card("Preview bố cục")
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setWordWrap(False)
        self.preview.setMinimumHeight(520)
        self.preview.setStyleSheet("background:#111827;border:2px solid #8b82ff;border-radius:18px;")
        layout.addWidget(self.preview, 1)
        self.safe_box_debug = QCheckBox("Hiện safe-box (debug)")
        self.safe_box_debug.setObjectName("SafeBoxDebug")
        self.safe_box_debug.toggled.connect(self._display_preview)
        layout.addWidget(self.safe_box_debug, 0, Qt.AlignmentFlag.AlignCenter)
        self.preview_caption = QLabel()
        self.preview_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_caption.setWordWrap(True)
        self.preview_caption.setObjectName("Subtitle")
        layout.addWidget(self.preview_caption)
        self.estimate = QLabel()
        self.estimate.setObjectName("Subtitle")
        self.estimate.setWordWrap(True)
        layout.addWidget(self.estimate)
        return card

    def _set_word_count(self, count: int) -> None:
        if not hasattr(self, "table"):
            return
        old = self.table.rowCount()
        self.table.setRowCount(count)
        for row in range(old, count):
            for column in range(4):
                self.table.setItem(row, column, QTableWidgetItem(""))
        self._update_preview()

    def _paste_table(self) -> None:
        lines = [line for line in QApplication.clipboard().text().splitlines() if line.strip()]
        rows = [line.split("\t") for line in lines]
        if not rows or any(len(row) < 4 for row in rows):
            QMessageBox.warning(self, "Dữ liệu không hợp lệ", "Hãy dán bảng có đủ 4 cột.")
            return
        self.word_count.setValue(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values[:4]):
                self.table.setItem(row_index, column, QTableWidgetItem(value.strip()))

    def _collect_project(self) -> MinigameProject:
        vocabulary = []
        for row in range(self.table.rowCount()):
            values = [
                self.table.item(row, column).text() if self.table.item(row, column) else ""
                for column in range(4)
            ]
            vocabulary.append(VocabularyItem(*values))
        return MinigameProject(
            project_uuid=self.project_uuid,
            topic=self.topic.text(),
            background_video=self.video_path.text(),
            theme_id=str(self.theme_id.currentData()),
            output_format=str(self.aspect.currentData()),
            output_folder=self.output_folder.text(),
            output_name=self.output_name.text(),
            tts_provider=self.voice_provider,
            voice=self.voice_id,
            voice_language=self.voice_language,
            voice_gender=self.voice_gender,
            voice_family=self.voice_family,
            voice_categories=self.voice_categories,
            service_account_path=self.service_account_path,
            volume=self.voice_volume,
            slow_rate=self.slow_rate,
            crop_focus={
                "16:9": [self.crop_16_x.value() / 100, self.crop_16_y.value() / 100],
                "9:16": [self.crop_9_x.value() / 100, self.crop_9_y.value() / 100],
            },
            vocabulary=vocabulary,
        )

    def set_project(self, project: MinigameProject) -> None:
        """Restore persisted Minigame state without restoring credential paths."""
        self._loading_project = True
        try:
            self.project_uuid = project.project_uuid
            theme_index = self.theme_id.findData(project.theme_id)
            self.theme_id.setCurrentIndex(max(0, theme_index))
            format_index = self.aspect.findData(project.output_format)
            self.aspect.setCurrentIndex(format_index if format_index >= 0 else 1)
            self.topic.setText(project.topic)
            self.video_path.setText(project.background_video)
            self.output_folder.setText(project.output_folder)
            self.output_name.setText(project.output_name)
            self.voice_provider = project.tts_provider
            self.voice_id = project.voice
            self.voice_name = project.voice
            self.voice_language = project.voice_language
            self.voice_gender = project.voice_gender
            self.voice_family = project.voice_family
            self.voice_categories = list(project.voice_categories)
            self.voice_volume = project.volume
            self.slow_rate = project.slow_rate
            self.crop_16_x.setValue(round(project.crop_focus.get("16:9", [0.5, 0.5])[0] * 100))
            self.crop_16_y.setValue(round(project.crop_focus.get("16:9", [0.5, 0.5])[1] * 100))
            self.crop_9_x.setValue(round(project.crop_focus.get("9:16", [0.5, 0.5])[0] * 100))
            self.crop_9_y.setValue(round(project.crop_focus.get("9:16", [0.5, 0.5])[1] * 100))
            self.word_count.setValue(len(project.vocabulary) or 5)
            for row, item in enumerate(project.vocabulary):
                for column, value in enumerate(
                    (item.vietnamese, item.english, item.ipa, item.example)
                ):
                    self.table.item(row, column).setText(value)
            self._update_voice_summary()
            self._update_preview()
        finally:
            self._loading_project = False

    def _start_render(self) -> None:
        if self.thread is not None:
            return
        project = self._collect_project()
        try:
            project.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Thiếu dữ liệu", str(exc))
            return
        self.pipeline = MinigamePipeline(project)
        self.thread = QThread(self)
        self.worker = MinigameWorker(self.pipeline)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._render_progress)
        self.worker.succeeded.connect(self._render_succeeded)
        self.worker.failed.connect(self._render_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_finished)
        self.render_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText("Đang chuẩn bị video minigame…")
        self.thread.start()

    @Slot(int, str)
    def _render_progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        if message:
            self.status.setText(message)

    @Slot(object)
    def _render_succeeded(self, result: MinigameResult) -> None:
        self.last_outputs = result.outputs
        self.progress.setValue(100)
        names = ", ".join(path.name for path in result.outputs)
        self.status.setText(f"Hoàn thành {len(result.outputs)} video: {names}")
        self.open_button.setEnabled(True)

    @Slot(object)
    def _render_failed(self, error: BaseException) -> None:
        if isinstance(error, PipelineError):
            message = error.user_message
        else:
            message = str(error)
        self.status.setText(message)
        if not (isinstance(error, PipelineError) and error.error_code == "cancelled"):
            QMessageBox.critical(self, "Không thể tạo minigame", message)

    @Slot()
    def _thread_finished(self) -> None:
        if self.thread:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None
        self.pipeline = None
        self.render_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def stop(self) -> None:
        if self.pipeline:
            self.pipeline.cancel_run()
            self.stop_button.setEnabled(False)
            self.status.setText("Đang dừng tại boundary an toàn…")

    def shutdown(self, timeout_ms: int = 5_000) -> bool:
        """Cancel safely and refuse app shutdown while a worker still owns resources."""
        self.stop()
        render_stopped = self.thread is None or self.thread.wait(timeout_ms)
        preview_stopped = self.preview_thread is None or self.preview_thread.wait(timeout_ms)
        return render_stopped and preview_stopped

    def _open_voice_dialog(self) -> None:
        dialog = MinigameVoiceDialog(
            provider=self.voice_provider,
            service_account_path=self.service_account_path,
            selected_voice_id=self.voice_id,
            selected_language=self.voice_language or "en",
            volume=self.voice_volume,
            slow_rate=self.slow_rate,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        config = dialog.configuration()
        if not config:
            return
        self.voice_provider = str(config["provider"])
        self.service_account_path = str(config["service_account_path"])
        self.voice_id = str(config["voice"])
        self.voice_name = str(config["voice_name"])
        self.voice_language = str(config["voice_language"])
        self.voice_gender = str(config["voice_gender"])
        self.voice_family = str(config["voice_family"])
        self.voice_categories = list(config["voice_categories"])  # type: ignore[arg-type]
        self.voice_volume = int(config["volume"])
        self.slow_rate = float(config["slow_rate"])
        self._update_voice_summary()

    def _update_voice_summary(self) -> None:
        provider = "Google Cloud TTS" if self.voice_provider == "google" else "CapCut TTS"
        categories = ", ".join(self.voice_categories) or "Chưa có danh mục"
        credential = (
            "Service account: sẵn sàng"
            if self.voice_provider == "google" and Path(self.service_account_path).is_file()
            else ""
        )
        self.voice_summary.setText(
            f"{provider}\n{self.voice_name} · {self.voice_language or 'N/A'} · "
            f"{self.voice_family} · {self.voice_gender or 'Chưa rõ giới tính'}\n"
            f"Danh mục: {categories} · Volume {self.voice_volume}% · Slow {round(self.slow_rate * 100)}%"
            f"{' · ' + credential if credential else ''}"
        )

    def _update_preview(self) -> None:
        if not hasattr(self, "preview"):
            return
        self.preview_timer.start()
        if not self._loading_project:
            self.dirty_changed.emit(True)
        total = self.word_count.value() if hasattr(self, "word_count") else 5
        ratio = "9:16"
        seconds = 3 + total * 8 + total * 4 + 8
        outputs = 2 if ratio == MinigameFormat.BOTH.value else 1
        self.estimate.setText(
            f"{total} từ · khoảng {seconds // 60}:{seconds % 60:02d} · {outputs} video đầu ra · "
            "audio nền: tắt"
        )

    @Slot()
    def _start_preview_render(self) -> None:
        if not self.isVisible():
            return
        if self.preview_thread is not None:
            self._preview_pending = True
            return
        topic = self.topic.text().strip() or "YOUR TOPIC"
        total = self.word_count.value()
        theme_id = str(self.theme_id.currentData())
        self.preview_thread = QThread(self)
        self.preview_worker = MinigamePreviewWorker(theme_id, topic, total, self.preview_cache)
        self.preview_worker.moveToThread(self.preview_thread)
        self.preview_thread.started.connect(self.preview_worker.run)
        self.preview_worker.succeeded.connect(self._preview_ready)
        self.preview_worker.failed.connect(self._preview_failed)
        self.preview_worker.finished.connect(self.preview_thread.quit)
        self.preview_worker.finished.connect(self.preview_worker.deleteLater)
        self.preview_thread.finished.connect(self._preview_thread_finished)
        self.preview_thread.start()

    @Slot(object)
    def _preview_ready(self, result: object) -> None:
        manifest, text_path, _topic, _total = result  # type: ignore[misc]
        start = QPixmap(str(manifest.start_screen)).scaled(
            manifest.canvas.width,
            manifest.canvas.height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter = QPainter(start)
        painter.drawPixmap(0, 0, QPixmap(str(text_path)))
        painter.end()
        self._preview_base = start
        self._preview_manifest = manifest
        self._display_preview()
        self.preview_caption.setText("Preview dùng đúng start-screen và typography như output.")

    @Slot(object)
    def _preview_failed(self, error: object) -> None:
        message = error.user_message if isinstance(error, PipelineError) else str(error)
        self.preview_caption.setText(f"Không thể cập nhật preview: {message}")

    @Slot()
    def _preview_thread_finished(self) -> None:
        if self.preview_thread:
            self.preview_thread.deleteLater()
        self.preview_thread = None
        self.preview_worker = None
        if self._preview_pending:
            self._preview_pending = False
            self.preview_timer.start(0)

    @Slot()
    def _display_preview(self) -> None:
        if self._preview_base.isNull():
            return
        image = self._preview_base.copy()
        if self.safe_box_debug.isChecked() and self._preview_manifest is not None:
            painter = QPainter(image)
            painter.setPen(QPen(QColor("#ff4fd8"), 4, Qt.PenStyle.DashLine))
            for name in ("start_title", "start_topic"):
                x1, y1, x2, y2 = self._preview_manifest.slots[name].bounds
                painter.drawRect(x1, y1, x2 - x1, y2 - y1)
            painter.end()
        self.preview.setPixmap(
            image.scaled(
                self.preview.size() if self.preview.size().isValid() else image.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _choose_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn video nền", "", "Video (*.mp4 *.mov *.mkv *.webm)"
        )
        if path:
            self.video_path.setText(path)

    def _choose_output_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục xuất video")
        if path:
            self.output_folder.setText(path)

    def _open_output(self) -> None:
        folder = self.last_outputs[0].parent if self.last_outputs else Path(self.output_folder.text())
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))
