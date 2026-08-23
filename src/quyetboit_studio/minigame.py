from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QSettings,
    QSize,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFontDatabase,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .core.minigame import (
    MinigameContent,
    MinigameFlow,
    MinigameFormat,
    MinigameMultipartContent,
    MinigameProject,
    MinigameTimingProfile,
    MinigameTtsProvider,
    VocabularyItem,
)
from .core.models import PipelineError, ProgressEvent
from .minigame_voice import MinigameVoiceDialog
from .pipeline.minigame import MinigameMultipartPipeline, MinigamePipeline, MinigameResult
from .services.media.ffmpeg import FFmpegRunner
from .services.media.minigame_frame import MinigameVisualFrameService
from .services.media.minigame_mask import MinigameAlphaMaskService
from .services.media.minigame_text import MinigameTextLayerService
from .services.media.minigame_theme import MinigameThemeService


class MinigameThemeDialog(QDialog):
    """Compact visual configuration for the themed minigame renderer."""

    def __init__(
        self,
        *,
        theme_service: MinigameThemeService,
        selected_theme_id: str,
        background_video: str,
        video_theme_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cấu hình giao diện Mini Game")
        self.resize(760, 360)
        self._selected_theme_id = selected_theme_id
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        note = QLabel(
            "Khi chọn video, ứng dụng giữ nguyên nền chữ và chữ của theme rồi đặt chúng lên video."
        )
        note.setObjectName("Subtitle")
        root.addWidget(note)
        root.addWidget(QLabel("Luồng theme tĩnh"))
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, (theme_id, manifest) in enumerate(theme_service.discover().items()):
            card = QToolButton()
            card.setCheckable(True)
            card.setIcon(QIcon(str(manifest.start_screen)))
            card.setIconSize(QSize(72, 128))
            card.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            card.setText(manifest.display_name)
            card.setToolTip(
                f"{manifest.display_name} · hỗ trợ: {', '.join(manifest.supported_formats)}"
            )
            card.setChecked(theme_id == selected_theme_id)
            card.clicked.connect(lambda _checked=False, value=theme_id: self._select(value))
            self.theme_group.addButton(card)
            grid.addWidget(card, 0, index)
        root.addLayout(grid)
        root.addWidget(QLabel("Luồng nền video"))
        background_row = QWidget()
        background_layout = QHBoxLayout(background_row)
        background_layout.setContentsMargins(0, 0, 0, 0)
        self.background_video = QLineEdit(background_video)
        self.background_video.setPlaceholderText("Không chọn: dùng nền ảnh có sẵn của theme")
        choose_video = QPushButton("Chọn video…")
        choose_video.clicked.connect(self._choose_background_video)
        clear_video = QPushButton("Bỏ chọn")
        clear_video.clicked.connect(self.background_video.clear)
        background_layout.addWidget(self.background_video, 1)
        background_layout.addWidget(choose_video)
        background_layout.addWidget(clear_video)
        root.addWidget(background_row)
        self._legacy_video_theme_id = video_theme_id
        background_note = QLabel(
            "Video chạy phía sau toàn bộ Hook, Question/Answer, Score, Review và CTA. "
            "Panel/chữ tự động theo đúng theme phía trên; âm thanh gốc của video không được dùng."
        )
        background_note.setObjectName("Subtitle")
        background_note.setWordWrap(True)
        root.addWidget(background_note)
        controls = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        root.addWidget(controls)

    def _select(self, theme_id: str) -> None:
        self._selected_theme_id = theme_id

    def selected_theme_id(self) -> str:
        return self._selected_theme_id

    def selected_background_video(self) -> str:
        return self.background_video.text().strip()

    def selected_video_theme_id(self) -> str:
        # Retained only so old project payloads round-trip without a schema break.
        return self._legacy_video_theme_id

    def _choose_background_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn video nền", "", "Video (*.mp4 *.mov *.mkv *.webm *.avi)"
        )
        if path:
            self.background_video.setText(path)


class MinigameFlowDialog(QDialog):
    """Select the sections included in an English mini-game video."""

    FLOW_LABELS = (
        (MinigameFlow.HOOK, "Hook"),
        (MinigameFlow.QUESTION, "Mỗi từ — Question và countdown"),
        (MinigameFlow.ANSWER, "Mỗi từ — Answer"),
        (MinigameFlow.VOICE_LISTEN, "Mỗi từ — Voice: nghe tự nhiên"),
        (MinigameFlow.VOICE_SLOW, "Mỗi từ — Voice: nghe chậm"),
        (MinigameFlow.VOICE_REPEAT, "Mỗi từ — Voice: lặp lại"),
        (MinigameFlow.SCORE, "Chấm điểm"),
        (MinigameFlow.REVIEW_START, "Mở đầu ôn tập"),
        (MinigameFlow.REVIEW, "Ôn tập từng từ"),
        (MinigameFlow.CTA, "CTA / kêu gọi bình luận"),
    )

    def __init__(self, enabled_flows: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cấu hình flow Mini game")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Chỉ các mục được chọn mới xuất hiện trong video. Bỏ Hook sẽ bắt đầu từ "
            "Question/countdown nếu mục đó được chọn."
        )
        note.setObjectName("Subtitle")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.checks: dict[MinigameFlow, QCheckBox] = {}
        for flow, label in self.FLOW_LABELS:
            check = QCheckBox(label)
            check.setChecked(flow.value in enabled_flows)
            self.checks[flow] = check
            layout.addWidget(check)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_flows(self) -> list[str]:
        return [flow.value for flow, check in self.checks.items() if check.isChecked()]


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

    def __init__(
        self,
        theme_id: str,
        video_theme_id: str,
        background_video: str,
        crop_focus: tuple[float, float],
        topic: str,
        total: int,
        cache_dir: Path,
    ) -> None:
        super().__init__()
        self.theme_id = theme_id
        self.video_theme_id = video_theme_id
        self.background_video = background_video
        self.crop_focus = crop_focus
        self.topic = topic
        self.total = total
        self.cache_dir = cache_dir

    @Slot()
    def run(self) -> None:
        try:
            themes = MinigameThemeService()
            manifest = themes.get(self.theme_id)
            project = MinigameProject(
                topic=self.topic,
                theme_id=self.theme_id,
                video_theme_id=self.video_theme_id,
                vocabulary=[VocabularyItem("", "", "", "") for _ in range(self.total)],
            )
            text_path = MinigameTextLayerService().build_hook(project, manifest, self.cache_dir)
            frames = MinigameVisualFrameService()
            if self.background_video:
                background = Path(self.background_video).expanduser().resolve()
                if not background.is_file():
                    raise ValueError("Video nền đã chọn không còn tồn tại.")
                stat = background.stat()
                frame_path = self.cache_dir / (
                    f"video-frame-{stat.st_size}-{stat.st_mtime_ns}-"
                    f"{round(self.crop_focus[0] * 100)}-{round(self.crop_focus[1] * 100)}.png"
                )
                if not frame_path.is_file():
                    partial = frame_path.with_name(f".{frame_path.stem}.partial.png")
                    frame_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        FFmpegRunner().run(
                            [
                                "-ss",
                                "0.100",
                                "-i",
                                str(background),
                                "-frames:v",
                                "1",
                                "-vf",
                                (
                                    "scale=1080:1920:force_original_aspect_ratio=increase,"
                                    f"crop=1080:1920:(in_w-out_w)*{self.crop_focus[0]:.4f}:"
                                    f"(in_h-out_h)*{self.crop_focus[1]:.4f}"
                                ),
                                "-update",
                                "1",
                                str(partial),
                            ],
                            duration_ms=100,
                            step=0,
                        )
                        partial.replace(frame_path)
                    finally:
                        partial.unlink(missing_ok=True)
                mask = MinigameAlphaMaskService().build(manifest, self.cache_dir / "theme-mask")
                preview = frames.build(
                    frame_path,
                    (mask,),
                    self.cache_dir / "frames",
                    layout_key={"theme": manifest.checksum, "background": "video"},
                )
                mode = "video"
            else:
                preview = frames.build(
                    manifest.start_base,
                    (text_path,),
                    self.cache_dir / "frames",
                    layout_key={"theme": manifest.checksum},
                )
                mode = "static"
            self.succeeded.emit((manifest, preview, mode))
        except Exception as exc:  # noqa: BLE001 - report preview failure to Qt UI
            self.failed.emit(exc)
        finally:
            self.finished.emit()


class MinigamePage(QWidget):
    dirty_changed = Signal(bool)

    def __init__(self, preferences: QSettings | None = None) -> None:
        super().__init__()
        self.setObjectName("AppRoot")
        self.thread: QThread | None = None
        self.worker: MinigameWorker | None = None
        self.pipeline: MinigamePipeline | MinigameMultipartPipeline | None = None
        self.preview_thread: QThread | None = None
        self.preview_worker: MinigamePreviewWorker | None = None
        self._preview_pending = False
        self._preview_base = QPixmap()
        self._preview_manifest = None
        self.last_outputs: list[Path] = []
        self.project_uuid = MinigameProject().project_uuid
        self._loading_project = False
        self._last_valid_content = MinigameContent.blank(5)
        self._auto_output_name = True
        self.background_video_path = ""
        self.video_theme_id = "video-ocean"
        self.preferences = (
            preferences if preferences is not None else QSettings("QuyetBoIT", "QuyetBoITStudio")
        )
        default_account = Path.cwd() / "services-account.json"
        self.voice_provider = str(
            self.preferences.value("minigame/voice/provider", MinigameTtsProvider.GOOGLE.value)
        )
        self.voice_id = str(self.preferences.value("minigame/voice/id", "en-US-Neural2-F"))
        self.voice_name = str(self.preferences.value("minigame/voice/name", self.voice_id))
        self.voice_language = str(self.preferences.value("minigame/voice/language", "en-US"))
        self.voice_gender = str(self.preferences.value("minigame/voice/gender", ""))
        self.voice_family = str(self.preferences.value("minigame/voice/family", "Neural2"))
        cached_categories = self.preferences.value("minigame/voice/categories", ["Neural2", "en"])
        self.voice_categories = (
            [str(value) for value in cached_categories]
            if isinstance(cached_categories, list)
            else ["Neural2", "en"]
        )
        self.service_account_path = (
            str(default_account.resolve()) if default_account.is_file() else ""
        )
        self.voice_volume = int(self.preferences.value("minigame/voice/volume", 100))
        self.slow_rate = float(self.preferences.value("minigame/voice/slow_rate", 0.72))
        self.theme_service = MinigameThemeService()
        self.text_layers = MinigameTextLayerService()
        cache_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        )
        self.preview_cache = Path(cache_location) / "minigame-preview"
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(180)
        self.preview_timer.timeout.connect(self._start_preview_render)
        self._build_ui()
        self._content_changed()
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

        self.config_scroll = QScrollArea()
        self.config_scroll.setWidgetResizable(True)
        self.config_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        columns = QHBoxLayout(content)
        columns.setContentsMargins(0, 16, 0, 0)
        columns.setSpacing(18)
        columns.addWidget(self._configuration_panel(), 3)
        columns.addWidget(self._preview_panel(), 2)
        self.config_scroll.setWidget(content)
        root.addWidget(self.config_scroll, 1)
        # This dock deliberately lives outside the ScrollArea: long JSON input
        # must never hide the render controls or live progress from the user.
        self.action_dock = self._action_dock()
        root.addWidget(self.action_dock)

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

        theme_card, theme = self._card("1. Cấu hình theme")
        self.theme_id = QComboBox()
        for theme_id, manifest in self.theme_service.discover().items():
            self.theme_id.addItem(manifest.display_name, theme_id)
        cached_theme_id = str(self.preferences.value("minigame/theme_id", "theme-1"))
        cached_theme_index = self.theme_id.findData(cached_theme_id)
        self.theme_id.setCurrentIndex(max(0, cached_theme_index))
        self.theme_id.currentIndexChanged.connect(self._cache_theme_selection)
        self.theme_id.currentIndexChanged.connect(self._update_theme_summary)
        self.theme_id.currentIndexChanged.connect(self._update_preview)
        self.theme_summary = QLabel()
        self.theme_summary.setObjectName("Subtitle")
        self.theme_summary.setWordWrap(True)
        self.theme_config_button = QPushButton("Cấu hình theme…")
        self.theme_config_button.clicked.connect(self._open_theme_dialog)
        theme.addWidget(self.theme_summary)
        theme.addWidget(self.theme_config_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._update_theme_summary()
        layout.addWidget(theme_card)

        flow_card, flow = self._card("2. Cấu hình flow")
        self.enabled_flows = set(self._last_used_flows())
        self.flow_summary = QLabel()
        self.flow_summary.setObjectName("Subtitle")
        self.flow_summary.setWordWrap(True)
        self.flow_config_button = QPushButton("Cấu hình flow…")
        self.flow_config_button.clicked.connect(self._open_flow_dialog)
        flow.addWidget(self.flow_summary)
        flow.addWidget(self.flow_config_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._update_flow_summary()
        layout.addWidget(flow_card)

        source_card, source = self._card("3. Định dạng")
        source_form = QFormLayout()
        self.aspect = QComboBox()
        self.aspect.addItem("YouTube 16:9", MinigameFormat.YOUTUBE.value)
        self.aspect.addItem("TikTok / Shorts 9:16", MinigameFormat.VERTICAL.value)
        self.aspect.addItem("Xuất cả hai", MinigameFormat.BOTH.value)
        self.aspect.model().item(0).setEnabled(False)
        self.aspect.model().item(2).setEnabled(False)
        cached_format = str(
            self.preferences.value("minigame/export/format", MinigameFormat.VERTICAL.value)
        )
        cached_format_index = self.aspect.findData(cached_format)
        if cached_format_index < 0 or not self.aspect.model().item(cached_format_index).isEnabled():
            cached_format_index = 1
        self.aspect.setCurrentIndex(cached_format_index)
        self.aspect.setToolTip("Bộ asset hiện chỉ được thiết kế cho video dọc 9:16.")
        self.aspect.currentIndexChanged.connect(self._cache_export_configuration)
        self.aspect.currentIndexChanged.connect(self._update_preview)
        source_form.addRow("Tỷ lệ", self.aspect)
        source.addLayout(source_form)
        audio_note = QLabel(
            "Theme hiện hỗ trợ video dọc 9:16. Chọn video nền trong Cấu hình theme khi cần thay nền ảnh phần học."
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

        voice_card, voice = self._card("4. Cấu hình giọng đọc")
        self.voice_summary = QLabel()
        self.voice_summary.setWordWrap(True)
        self.voice_summary.setObjectName("Subtitle")
        self.voice_config_button = QPushButton("Cấu hình Voice…")
        self.voice_config_button.clicked.connect(self._open_voice_dialog)
        voice.addWidget(self.voice_summary)
        voice.addWidget(self.voice_config_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._update_voice_summary()
        layout.addWidget(voice_card)

        output_card, output = self._card("5. Xuất video")
        output_form = QFormLayout()
        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        default_output_folder = str((Path.cwd() / "output").resolve())
        cached_output_folder = str(
            self.preferences.value("minigame/export/folder", default_output_folder)
        )
        self.output_folder = QLineEdit(cached_output_folder)
        self.output_folder.editingFinished.connect(self._cache_export_configuration)
        choose_folder = QPushButton("Chọn…")
        choose_folder.clicked.connect(self._choose_output_folder)
        folder_layout.addWidget(self.output_folder, 1)
        folder_layout.addWidget(choose_folder)
        self.output_name = QLineEdit()
        self.output_name.setPlaceholderText("Tự động lấy từ Topic; bạn có thể sửa lại")
        self.output_name.textEdited.connect(self._mark_output_name_custom)
        output_form.addRow("Thư mục", folder_row)
        output_form.addRow("Tên video", self.output_name)
        output.addLayout(output_form)
        layout.addWidget(output_card)

        content_card, vocabulary_layout = self._card("6. Nội dung bắt buộc")
        json_help = QLabel(
            "Nhập một object JSON gồm schemaVersion, topic và words. Mỗi từ có đủ: "
            "vietnamese, english, ipa, wordType, example, exampleVietnamese, level "
            "(Dễ, Trung bình hoặc Khó)."
        )
        json_help.setWordWrap(True)
        json_help.setObjectName("Subtitle")
        vocabulary_layout.addWidget(json_help)
        json_actions = QHBoxLayout()
        load_json = QPushButton("Mở file JSON…")
        load_json.clicked.connect(self._load_content_json)
        format_json = QPushButton("Định dạng JSON")
        format_json.clicked.connect(self._format_content_json)
        copy_example = QPushButton("Sao chép JSON mẫu")
        copy_example.clicked.connect(self._copy_content_example)
        json_actions.addWidget(load_json)
        json_actions.addWidget(format_json)
        json_actions.addWidget(copy_example)
        json_actions.addStretch()
        vocabulary_layout.addLayout(json_actions)
        self.content_tabs = QTabWidget()
        single_tab = QWidget()
        single_layout = QVBoxLayout(single_tab)
        single_layout.setContentsMargins(0, 8, 0, 0)
        self.content_editor = QPlainTextEdit()
        self.content_editor.setObjectName("MinigameJsonEditor")
        self.content_editor.setMinimumHeight(420)
        self.content_editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.content_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.content_editor.setPlaceholderText("Dán JSON nội dung minigame tại đây…")
        self.content_editor.setPlainText(self._last_valid_content.to_json())
        self.content_editor.textChanged.connect(self._content_changed)
        single_layout.addWidget(self.content_editor)
        self.content_status = QLabel()
        self.content_status.setWordWrap(True)
        self.content_status.setObjectName("Subtitle")
        single_layout.addWidget(self.content_status)
        self.content_tabs.addTab(single_tab, "Một video")

        multipart_tab = QWidget()
        multipart_layout = QVBoxLayout(multipart_tab)
        multipart_layout.setContentsMargins(0, 8, 0, 0)
        multipart_help = QLabel(
            "Dán JSON gồm topic, totalParts, totalWords và mảng parts. Mỗi phần được render "
            "qua luồng hiện tại thành một video riêng: <Topic> - Part <partNumber>."
        )
        multipart_help.setWordWrap(True)
        multipart_help.setObjectName("Subtitle")
        multipart_layout.addWidget(multipart_help)
        self.multipart_editor = QPlainTextEdit()
        self.multipart_editor.setObjectName("MinigameMultipartJsonEditor")
        self.multipart_editor.setMinimumHeight(420)
        self.multipart_editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.multipart_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.multipart_editor.setPlaceholderText("Dán JSON nhiều phần tại đây…")
        self.multipart_editor.textChanged.connect(self._multipart_content_changed)
        multipart_layout.addWidget(self.multipart_editor)
        self.multipart_status = QLabel("Chưa nhập dữ liệu nhiều phần.")
        self.multipart_status.setWordWrap(True)
        self.multipart_status.setObjectName("Subtitle")
        multipart_layout.addWidget(self.multipart_status)
        self.content_tabs.addTab(multipart_tab, "Nhiều phần")
        self.content_tabs.currentChanged.connect(self._content_mode_changed)
        vocabulary_layout.addWidget(self.content_tabs)
        layout.addWidget(content_card)

        return panel

    def _action_dock(self) -> QFrame:
        """Persistent render controls shown below the scrollable configuration."""
        action_card = QFrame()
        action_card.setObjectName("MinigameActionDock")
        action_card.setStyleSheet(
            "QFrame#MinigameActionDock { background: #ffffff; border: 1px solid #d0d5dd; "
            "border-radius: 14px; }"
        )
        action_layout = QVBoxLayout(action_card)
        action_layout.setContentsMargins(18, 12, 18, 12)
        action_layout.setSpacing(8)
        actions = QHBoxLayout()
        dock_title = QLabel("Xuất video")
        dock_title.setObjectName("SectionTitle")
        actions.addWidget(dock_title)
        actions.addStretch()
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
        action_layout.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        action_layout.addWidget(self.progress)
        self.status = QLabel("Sẵn sàng")
        self.status.setObjectName("Subtitle")
        self.status.setWordWrap(True)
        action_layout.addWidget(self.status)
        return action_card

    def _open_theme_dialog(self) -> None:
        dialog = MinigameThemeDialog(
            theme_service=self.theme_service,
            selected_theme_id=str(self.theme_id.currentData()),
            background_video=self.background_video_path,
            video_theme_id=self.video_theme_id,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_theme_id = dialog.selected_theme_id()
        index = self.theme_id.findData(selected_theme_id)
        if index < 0:
            return
        self.theme_id.setCurrentIndex(index)
        self.background_video_path = dialog.selected_background_video()
        self.video_theme_id = dialog.selected_video_theme_id()
        self._update_theme_summary()
        self._update_preview()
        self.preferences.setValue("minigame/theme_id", selected_theme_id)

    def _open_flow_dialog(self) -> None:
        dialog = MinigameFlowDialog(self.enabled_flows, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_flows()
        if not selected:
            QMessageBox.warning(self, "Thiếu flow", "Vui lòng chọn ít nhất một flow để tạo video.")
            return
        self.enabled_flows = set(selected)
        self._cache_flow_configuration()
        self._update_flow_summary()
        self._update_preview()

    def _last_used_flows(self) -> list[str]:
        raw = self.preferences.value("minigame/flow/enabled", MinigameFlow.defaults())
        values = raw if isinstance(raw, list) else [raw]
        selected = [str(value) for value in values if str(value) in MinigameFlow.defaults()]
        return selected or MinigameFlow.defaults()

    def _cache_flow_configuration(self) -> None:
        if not self._loading_project:
            self.preferences.setValue("minigame/flow/enabled", sorted(self.enabled_flows))

    def _update_flow_summary(self) -> None:
        labels = [
            label
            for flow, label in MinigameFlowDialog.FLOW_LABELS
            if flow.value in self.enabled_flows
        ]
        self.flow_summary.setText(
            f"Đã chọn {len(labels)}/{len(MinigameFlow)} flow · {', '.join(labels)}"
        )

    def _cache_theme_selection(self) -> None:
        if self._loading_project:
            return
        theme_id = self.theme_id.currentData()
        if theme_id:
            self.preferences.setValue("minigame/theme_id", str(theme_id))

    def _cache_export_configuration(self) -> None:
        if self._loading_project:
            return
        output_format = self.aspect.currentData()
        if output_format:
            self.preferences.setValue("minigame/export/format", str(output_format))
        self.preferences.setValue("minigame/export/folder", self.output_folder.text().strip())

    def _update_theme_summary(self) -> None:
        theme_id = str(self.theme_id.currentData())
        try:
            manifest = self.theme_service.get(theme_id)
        except PipelineError:
            self.theme_summary.setText("Chưa chọn theme hợp lệ.")
            return
        self.theme_summary.setText(
            f"{manifest.display_name} · {' · '.join(manifest.palette)} · "
            f"hỗ trợ {', '.join(manifest.supported_formats)} · "
            f"luồng {'video + nguyên nền chữ theme' if self.background_video_path else 'theme tĩnh'}"
        )

    def _preview_panel(self) -> QWidget:
        card, layout = self._card("Preview bố cục")
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setWordWrap(False)
        self.preview.setMinimumHeight(520)
        self.preview.setStyleSheet(
            "background:#111827;border:2px solid #8b82ff;border-radius:18px;"
        )
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

    @Slot()
    def _content_changed(self) -> None:
        try:
            content = MinigameContent.from_json(self.content_editor.toPlainText())
        except ValueError as exc:
            self.content_status.setText(f"JSON chưa hợp lệ — chưa thể lưu/chạy: {exc}")
        else:
            topic_changed = content.topic.strip() != self._last_valid_content.topic.strip()
            self._last_valid_content = content
            self.content_status.setText(
                f"Cấu trúc JSON hợp lệ · Topic: {content.topic or '(chưa nhập)'} · "
                f"{len(content.words)} từ"
            )
            self._sync_output_name_from_topic(
                content.topic,
                force=topic_changed and not self._loading_project,
            )
        self._update_preview()

    @Slot()
    def _multipart_content_changed(self) -> None:
        try:
            content = MinigameMultipartContent.from_json(self.multipart_editor.toPlainText())
        except ValueError as exc:
            self.multipart_status.setText(f"JSON nhiều phần chưa hợp lệ — chưa thể chạy: {exc}")
        else:
            self.multipart_status.setText(
                f"Cấu trúc JSON hợp lệ · Topic: {content.topic} · "
                f"{len(content.parts)} phần · {content.total_words} từ"
            )
            self._sync_output_name_from_topic(content.topic)
        self._update_preview()

    @Slot(int)
    def _content_mode_changed(self, index: int) -> None:
        self.render_button.setText("Tạo nhiều video" if index == 1 else "Tạo video")
        self._update_preview()

    def _is_multipart_mode(self) -> bool:
        return self.content_tabs.currentIndex() == 1

    def _mark_output_name_custom(self, _value: str) -> None:
        self._auto_output_name = False

    def _sync_output_name_from_topic(self, topic: str, *, force: bool = False) -> None:
        if (force or self._auto_output_name) and topic.strip():
            self._auto_output_name = True
            self.output_name.setText(topic.strip())

    def _load_content_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Mở dữ liệu Minigame", "", "JSON (*.json)")
        if not path:
            return
        source = Path(path)
        try:
            if source.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("File JSON vượt quá giới hạn 2 MiB.")
            value = source.read_text(encoding="utf-8-sig")
            if self._is_multipart_mode():
                MinigameMultipartContent.from_json(value)
            else:
                MinigameContent.from_json(value)
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self, "JSON không hợp lệ", str(exc))
            return
        self._active_content_editor().setPlainText(value)

    def _format_content_json(self) -> None:
        try:
            content = (
                MinigameMultipartContent.from_json(self.multipart_editor.toPlainText())
                if self._is_multipart_mode()
                else MinigameContent.from_json(self.content_editor.toPlainText())
            )
        except ValueError as exc:
            QMessageBox.warning(self, "JSON không hợp lệ", str(exc))
            return
        self._active_content_editor().setPlainText(content.to_json())

    def _copy_content_example(self) -> None:
        if self._is_multipart_mode():
            QApplication.clipboard().setText(MinigameMultipartContent.example().to_json())
        else:
            QApplication.clipboard().setText(MinigameContent.example().to_json())
        target = self.multipart_status if self._is_multipart_mode() else self.content_status
        target.setText("Đã sao chép JSON mẫu vào clipboard.")

    def _active_content_editor(self) -> QPlainTextEdit:
        return self.multipart_editor if self._is_multipart_mode() else self.content_editor

    def _multipart_content(self, *, strict: bool) -> MinigameMultipartContent | None:
        try:
            return MinigameMultipartContent.from_json(self.multipart_editor.toPlainText())
        except ValueError:
            if strict:
                raise
            return None

    def _content(self, *, strict: bool) -> MinigameContent:
        if self._is_multipart_mode():
            content = self._multipart_content(strict=strict)
            if content is not None:
                return content.parts[0][1]
            return self._last_valid_content
        try:
            return MinigameContent.from_json(self.content_editor.toPlainText())
        except ValueError:
            if strict:
                raise
            return self._last_valid_content

    def _collect_project(self, *, strict_content: bool = False) -> MinigameProject:
        content = self._content(strict=strict_content)
        return MinigameProject(
            project_uuid=self.project_uuid,
            topic=content.topic,
            background_video=self.background_video_path,
            theme_id=str(self.theme_id.currentData()),
            video_theme_id=self.video_theme_id,
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
            enabled_flows=sorted(self.enabled_flows),
            vocabulary=list(content.words),
        )

    def set_project(self, project: MinigameProject) -> None:
        """Restore persisted Minigame state without restoring credential paths."""
        defaults = MinigameProject()
        is_new_project = (
            not project.topic
            and not project.vocabulary
            and not project.background_video
            and project.theme_id == defaults.theme_id
            and project.output_format == defaults.output_format
            and project.output_folder == defaults.output_folder
            and project.output_name == defaults.output_name
            and project.tts_provider == defaults.tts_provider
            and project.voice == defaults.voice
            and project.voice_language == defaults.voice_language
            and project.voice_gender == defaults.voice_gender
            and project.voice_family == defaults.voice_family
            and project.voice_categories == defaults.voice_categories
            and project.volume == defaults.volume
            and project.slow_rate == defaults.slow_rate
        )
        self._loading_project = True
        try:
            self.project_uuid = project.project_uuid
            if not is_new_project:
                theme_index = self.theme_id.findData(project.theme_id)
                self.theme_id.setCurrentIndex(max(0, theme_index))
                format_index = self.aspect.findData(project.output_format)
                self.aspect.setCurrentIndex(format_index if format_index >= 0 else 1)
            self.background_video_path = project.background_video
            self.video_theme_id = project.video_theme_id
            if not is_new_project:
                self.output_folder.setText(project.output_folder)
            # MainWindow restores a blank MinigameProject on every new app
            # session.  Treat its empty/legacy/default name as automatic so a
            # Topic entered in the JSON editor can populate this field.  A
            # saved name different from the Topic remains an explicit edit.
            stored_name = project.output_name.strip()
            self._auto_output_name = stored_name in {
                "",
                "english_minigame",
                project.topic.strip(),
            }
            self.output_name.setText(
                project.topic.strip()
                if self._auto_output_name and project.topic.strip()
                else stored_name
            )
            if not is_new_project:
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
            self.enabled_flows = (
                set(self._last_used_flows()) if is_new_project else set(project.enabled_flows)
            )
            self._update_flow_summary()
            content = MinigameContent(
                project.topic, list(project.vocabulary or MinigameContent.blank().words)
            )
            self._last_valid_content = content
            self.content_editor.setPlainText(content.to_json())
            self._update_theme_summary()
            self._update_voice_summary()
            self._update_preview()
        finally:
            self._loading_project = False

    def _start_render(self) -> None:
        if self.thread is not None:
            return
        try:
            project = self._collect_project(strict_content=True)
            multipart_content = (
                self._multipart_content(strict=True) if self._is_multipart_mode() else None
            )
            project.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Thiếu dữ liệu", str(exc))
            return
        self.pipeline = (
            MinigameMultipartPipeline(project, multipart_content)
            if multipart_content is not None
            else MinigamePipeline(project)
        )
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
        cover_names = ", ".join(path.name for path in result.covers)
        self.status.setText(
            f"Hoàn thành {len(result.outputs)} video: {names} · Ảnh bìa: {cover_names}"
        )
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
        self._cache_voice_configuration()
        self._update_voice_summary()

    def _cache_voice_configuration(self) -> None:
        """Persist the last non-sensitive voice selection for the next minigame."""
        self.preferences.setValue("minigame/voice/provider", self.voice_provider)
        self.preferences.setValue("minigame/voice/id", self.voice_id)
        self.preferences.setValue("minigame/voice/name", self.voice_name)
        self.preferences.setValue("minigame/voice/language", self.voice_language)
        self.preferences.setValue("minigame/voice/gender", self.voice_gender)
        self.preferences.setValue("minigame/voice/family", self.voice_family)
        self.preferences.setValue("minigame/voice/categories", self.voice_categories)
        self.preferences.setValue("minigame/voice/volume", self.voice_volume)
        self.preferences.setValue("minigame/voice/slow_rate", self.slow_rate)

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
        total = len(self._last_valid_content.words)
        ratio = "9:16"
        timing = MinigameTimingProfile()
        # TTS varies by text, so the preview uses conservative clip estimates but
        # all fixed boundaries come from the production timing profile.
        per_word_ms = (
            timing.question_intro_ms
            + timing.countdown_ms
            + 1_858
            + timing.post_reveal_sfx_gap_ms
            + 1_200
            + timing.between_listen_slow_ms
            + 1_600
            + timing.between_slow_repeat_ms
            + 1_200
            + timing.after_repeat_ms
        )
        per_review_ms = (
            timing.review_lead_ms
            + 1_200
            + timing.review_word_to_example_ms
            + 2_000
            + timing.review_after_example_min_ms
        )
        seconds = round(
            (
                timing.hook_ms
                + total * (per_word_ms + per_review_ms)
                + timing.score_ms
                + timing.review_start_ms
            )
            / 1_000
        )
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
        topic = self._last_valid_content.topic or "YOUR TOPIC"
        total = len(self._last_valid_content.words)
        theme_id = str(self.theme_id.currentData())
        self.preview_thread = QThread(self)
        self.preview_worker = MinigamePreviewWorker(
            theme_id,
            self.video_theme_id,
            self.background_video_path,
            (self.crop_9_x.value() / 100, self.crop_9_y.value() / 100),
            topic,
            total,
            self.preview_cache,
        )
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
        manifest, preview_path, mode = result  # type: ignore[misc]
        self._preview_base = QPixmap(str(preview_path))
        self._preview_manifest = manifest
        self._display_preview()
        self.preview_caption.setText(
            "Preview Hook dùng video nền và bộ màu đã chọn; ảnh bìa lấy từ frame render này."
            if mode == "video"
            else "Preview dùng đúng start.png và typography như video/ảnh bìa."
        )

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
            for name in ("start_topic",):
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

    def _choose_output_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục xuất video")
        if path:
            self.output_folder.setText(path)
            self._cache_export_configuration()

    def _open_output(self) -> None:
        folder = (
            self.last_outputs[0].parent if self.last_outputs else Path(self.output_folder.text())
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))
