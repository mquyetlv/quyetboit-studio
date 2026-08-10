from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
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
    QVBoxLayout,
    QWidget,
)

from .core.minigame import MinigameFormat, MinigameProject, MinigameTtsProvider, VocabularyItem
from .core.models import PipelineError, ProgressEvent
from .minigame_voice import MinigameVoiceDialog
from .pipeline.minigame import MinigamePipeline, MinigameResult


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


class MinigamePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("AppRoot")
        self.thread: QThread | None = None
        self.worker: MinigameWorker | None = None
        self.pipeline: MinigamePipeline | None = None
        self.last_outputs: list[Path] = []
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
        self._build_ui()
        self._set_word_count(5)
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

        source_card, source = self._card("1. Định dạng và video nền")
        source_form = QFormLayout()
        self.aspect = QComboBox()
        self.aspect.addItem("YouTube 16:9", MinigameFormat.YOUTUBE.value)
        self.aspect.addItem("TikTok / Shorts 9:16", MinigameFormat.VERTICAL.value)
        self.aspect.addItem("Xuất cả hai", MinigameFormat.BOTH.value)
        self.aspect.setCurrentIndex(1)
        self.aspect.currentIndexChanged.connect(self._update_preview)
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
        source_form.addRow("Tỷ lệ", self.aspect)
        source_form.addRow("Video nền", video_row)
        source.addLayout(source_form)
        audio_note = QLabel("Âm thanh video nền luôn được tắt. Video ngắn sẽ tự loop đến hết minigame.")
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

    def _preview_panel(self) -> QWidget:
        card, layout = self._card("Preview bố cục")
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setWordWrap(True)
        self.preview.setMinimumHeight(520)
        self.preview.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #211d46,stop:1 #5145a8);"
            "color:white;border:2px solid #8b82ff;border-radius:18px;padding:30px;"
            "font-size:22px;font-weight:700;"
        )
        layout.addWidget(self.preview, 1)
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
            topic=self.topic.text(),
            background_video=self.video_path.text(),
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
        return self.thread is None or self.thread.wait(timeout_ms)

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
        total = self.word_count.value() if hasattr(self, "word_count") else 5
        topic = self.topic.text().strip() if hasattr(self, "topic") else ""
        topic = topic or "YOUR TOPIC"
        ratio = self.aspect.currentData() if hasattr(self, "aspect") else "9:16"
        self.preview.setText(
            f"CAN YOU GET\n{total}/{total}?\n\nTopic: {topic}\n\n"
            "●  ●  ◉  ○  ○\n\nQUESTION 1\n\nNghĩa tiếng Việt\n\n3  ·  2  ·  1"
        )
        seconds = 3 + total * 8 + total * 4 + 8
        outputs = 2 if ratio == MinigameFormat.BOTH.value else 1
        self.estimate.setText(
            f"{total} từ · khoảng {seconds // 60}:{seconds % 60:02d} · {outputs} video đầu ra · "
            "audio nền: tắt"
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
