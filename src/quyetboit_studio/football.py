from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .core.football import FootballHighlightInput, FootballRenderConfig
from .core.models import CancellationToken, ProgressEvent
from .minigame_voice import MinigameVoiceDialog
from .pipeline.football import FootballHighlightPipeline
from .services.minigame_tts import CapCutEnglishSpeechService, GoogleCloudSpeechService


class FootballWorker(QObject):
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(self, pipeline: FootballHighlightPipeline) -> None:
        super().__init__(); self.pipeline = pipeline

    @Slot()
    def run(self) -> None:
        try: self.succeeded.emit(self.pipeline.run(self._progress))
        except Exception as exc:  # noqa: BLE001 - report any worker failure to the UI
            self.failed.emit(exc)
        finally: self.finished.emit()

    def _progress(self, event: ProgressEvent) -> None:
        self.progress.emit(round(event.fraction * 100), event.message)


class HighlightFootballPage(QWidget):
    """Configuration page; rendering is added only through a worker pipeline."""

    dirty_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("AppRoot")
        self._loading = False
        self.project_uuid = "highlight-football"
        self.thread: QThread | None = None
        self.worker: FootballWorker | None = None
        self.pipeline: FootballHighlightPipeline | None = None
        self.voice_provider = "capcut"
        self.voice_id = ""
        self.voice_name = "Chưa chọn giọng"
        self.voice_language = "vi-VN"
        self.voice_gender = ""
        self.voice_family = ""
        self.voice_categories: list[str] = []
        self.voice_volume = 100
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 26, 34, 26)
        title = QLabel("Highlight bóng đá")
        title.setObjectName("Title")
        root.addWidget(title)
        subtitle = QLabel("Tạo video 16:9 từ frame YouTube, kịch bản JSON và lời đọc TTS.")
        subtitle.setObjectName("Subtitle")
        root.addWidget(subtitle)

        card = QFrame(); card.setObjectName("Card")
        form = QFormLayout(card); form.setContentsMargins(20, 18, 20, 18); form.setSpacing(12)
        self.youtube_url = QLineEdit(); self.youtube_url.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self.voice_summary = QLabel(); self.voice_summary.setObjectName("Subtitle"); self.voice_summary.setWordWrap(True)
        self.voice_config = QPushButton("Cấu hình voice…"); self.voice_config.clicked.connect(self._open_voice_dialog)
        voice_row = QWidget(); voice_layout = QHBoxLayout(voice_row); voice_layout.setContentsMargins(0, 0, 0, 0); voice_layout.addWidget(self.voice_summary, 1); voice_layout.addWidget(self.voice_config)
        self.burn_subtitles = QCheckBox("Đốt phụ đề từ tts_text"); self.burn_subtitles.setChecked(True)
        self.ken_burns_start = QDoubleSpinBox(); self.ken_burns_start.setRange(1.00, 1.29); self.ken_burns_start.setSingleStep(0.01); self.ken_burns_start.setDecimals(2); self.ken_burns_start.setValue(1.00); self.ken_burns_start.setSuffix("×")
        self.ken_burns_end = QDoubleSpinBox(); self.ken_burns_end.setRange(1.01, 1.30); self.ken_burns_end.setSingleStep(0.01); self.ken_burns_end.setDecimals(2); self.ken_burns_end.setValue(1.15); self.ken_burns_end.setSuffix("×")
        zoom_row = QWidget(); zoom_layout = QHBoxLayout(zoom_row); zoom_layout.setContentsMargins(0, 0, 0, 0); zoom_layout.addWidget(QLabel("Từ")); zoom_layout.addWidget(self.ken_burns_start); zoom_layout.addSpacing(8); zoom_layout.addWidget(QLabel("đến")); zoom_layout.addWidget(self.ken_burns_end); zoom_layout.addStretch()
        self.output_folder = QLineEdit(); self.output_folder.setPlaceholderText("Chọn thư mục xuất")
        browse = QPushButton("Chọn thư mục…"); browse.clicked.connect(self._browse)
        output_row = QWidget(); output_layout = QHBoxLayout(output_row); output_layout.setContentsMargins(0, 0, 0, 0); output_layout.addWidget(self.output_folder, 1); output_layout.addWidget(browse)
        form.addRow("Video YouTube", self.youtube_url); form.addRow("Giọng đọc", voice_row); form.addRow("Phụ đề", self.burn_subtitles); form.addRow("Zoom ảnh (Ken Burns)", zoom_row); form.addRow("Thư mục xuất", output_row)
        root.addWidget(card)
        json_heading = QLabel("Kịch bản JSON")
        json_heading.setObjectName("SectionTitle")
        root.addWidget(json_heading)
        self.json_editor = QPlainTextEdit(); self.json_editor.setPlaceholderText("Mỗi phần tử chứa match_minute, situation_type, frames và tts_text."); self.json_editor.setMinimumHeight(250); self.json_editor.setPlainText(FootballHighlightInput.example_json())
        root.addWidget(self.json_editor, 1)
        actions = QHBoxLayout(); sample = QPushButton("Dùng JSON mẫu"); sample.clicked.connect(lambda: self.json_editor.setPlainText(FootballHighlightInput.example_json()))
        copy = QPushButton("Sao chép JSON mẫu"); copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(FootballHighlightInput.example_json()))
        self.validate_button = QPushButton("Kiểm tra dữ liệu"); self.validate_button.clicked.connect(self.validate)
        actions.addWidget(sample); actions.addWidget(copy); actions.addStretch(); actions.addWidget(self.validate_button); root.addLayout(actions)
        self.status = QLabel("Bước render chưa kích hoạt: hãy kiểm tra dữ liệu trước khi lập job.")
        self.status.setObjectName("Subtitle"); self.status.setWordWrap(True); root.addWidget(self.status)
        job = QFrame(); job.setObjectName("Card")
        job_layout = QHBoxLayout(job); job_layout.setContentsMargins(16, 12, 16, 12); job_layout.setSpacing(10)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0); self.progress.setFormat("Sẵn sàng tạo video")
        self.start_button = QPushButton("▶  Tạo video"); self.start_button.setObjectName("PrimaryButton"); self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("■  Dừng"); self.stop_button.hide(); self.stop_button.clicked.connect(self.stop)
        job_layout.addWidget(self.progress, 1); job_layout.addWidget(self.start_button); job_layout.addWidget(self.stop_button); root.addWidget(job)
        self._update_voice_summary()
        for widget in (self.youtube_url, self.burn_subtitles, self.ken_burns_start, self.ken_burns_end, self.output_folder, self.json_editor):
            signal = getattr(widget, "textChanged", None) or getattr(widget, "currentIndexChanged", None) or getattr(widget, "toggled", None)
            signal.connect(self._changed)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục xuất", self.output_folder.text())
        if path:
            self.output_folder.setText(path)

    def _changed(self, *_args: object) -> None:
        if not self._loading:
            self.dirty_changed.emit(True)

    def _open_voice_dialog(self) -> None:
        default_account = Path.cwd() / "services-account.json"
        dialog = MinigameVoiceDialog(
            provider=self.voice_provider,
            service_account_path=str(default_account) if default_account.is_file() else "",
            selected_voice_id=self.voice_id,
            selected_language=self.voice_language,
            volume=self.voice_volume,
            slow_rate=0.72,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        config = dialog.configuration()
        if not config:
            return
        self.voice_provider = str(config["provider"])
        self.voice_id = str(config["voice"])
        self.voice_name = str(config["voice_name"])
        self.voice_language = str(config["voice_language"])
        self.voice_gender = str(config["voice_gender"])
        self.voice_family = str(config["voice_family"])
        self.voice_categories = [str(value) for value in config["voice_categories"]]  # type: ignore[union-attr]
        self.voice_volume = int(config["volume"])
        self._update_voice_summary()
        self._changed()

    def _update_voice_summary(self) -> None:
        provider = "Google Cloud TTS" if self.voice_provider == "google" else "CapCut TTS"
        details = " · ".join(item for item in (self.voice_language, self.voice_gender, self.voice_family) if item)
        self.voice_summary.setText(f"{provider}\n{self.voice_name}{f' · {details}' if details else ''}\nÂm lượng {self.voice_volume}%")

    def validate(self) -> FootballHighlightInput | None:
        try:
            payload = FootballHighlightInput.from_json(self.youtube_url.text(), self.json_editor.toPlainText())
            FootballRenderConfig(self.ken_burns_start.value(), self.ken_burns_end.value())
        except ValueError as exc:
            self.status.setText(f"Dữ liệu chưa hợp lệ: {exc}")
            return None
        timestamps = sum(len(item.timestamps()) for item in payload.segments)
        self.status.setText(f"Dữ liệu hợp lệ: {len(payload.segments)} phân đoạn, {timestamps} frame. Sẵn sàng cho pipeline nền.")
        return payload

    def start(self) -> None:
        """Validate the job then hand all long-running media work to a QThread."""
        if self.thread is not None:
            self._show_problem("Video đang được tạo. Hãy chờ hoàn tất hoặc bấm Dừng.")
            return
        payload = self.validate()
        if payload is None:
            self._show_problem(self.status.text())
            return
        if not self.voice_id:
            self._show_problem("Chưa thể tạo video: hãy mở Cấu hình voice và chọn một giọng đọc.")
            return
        if not self.output_folder.text().strip():
            self._show_problem("Chưa thể tạo video: hãy chọn thư mục xuất.")
            return
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        workspace = local / "QuyetBoITStudio" / "workspaces" / self.project_uuid / "football"
        def speech_factory():
            if self.voice_provider == "google":
                return GoogleCloudSpeechService(Path.cwd() / "services-account.json", self.voice_id, language_code=self.voice_language, volume=self.voice_volume)
            return CapCutEnglishSpeechService(self.voice_id, volume=self.voice_volume, categories=("vietnamese",))
        self.pipeline = FootballHighlightPipeline(payload, output_folder=Path(self.output_folder.text()).expanduser(), workspace=workspace, speech_factory=speech_factory, burn_subtitles=self.burn_subtitles.isChecked(), cancel=CancellationToken(), tts_suffix=".wav" if self.voice_provider == "google" else ".mp3", voice_cache_key={"provider": self.voice_provider, "voice": self.voice_id, "language": self.voice_language, "volume": self.voice_volume}, render_config=FootballRenderConfig(self.ken_burns_start.value(), self.ken_burns_end.value()))
        self.thread = QThread(self); self.worker = FootballWorker(self.pipeline); self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run); self.worker.progress.connect(self._progress); self.worker.succeeded.connect(self._succeeded); self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self.thread.quit); self.worker.finished.connect(self.worker.deleteLater); self.thread.finished.connect(self._finished)
        self.start_button.hide(); self.stop_button.show(); self.stop_button.setEnabled(True); self.progress.setValue(0); self.progress.setFormat("Đang khởi tạo…"); self.status.setText(f"Đang tạo {len(payload.segments)} phân đoạn ở worker nền…"); self.thread.start()
        self.window().statusBar().showMessage("Highlight bóng đá đã bắt đầu chạy nền.", 5_000) if hasattr(self.window(), "statusBar") else None

    def _show_problem(self, message: str) -> None:
        self.status.setText(message)
        QMessageBox.warning(self, "Chưa thể tạo Highlight bóng đá", message)

    def stop(self) -> None:
        if self.pipeline: self.pipeline.cancel_run(); self.stop_button.setEnabled(False); self.status.setText("Đang dừng an toàn…")

    def _progress(self, value: int, message: str) -> None:
        self.progress.setValue(value); self.progress.setFormat(f"{value}%"); self.status.setText(message)

    def _succeeded(self, result: object) -> None:
        self.progress.setValue(100); self.progress.setFormat("Hoàn thành"); self.status.setText(f"Đã tạo video: {result.output}")

    def _failed(self, error: object) -> None:
        message = getattr(error, "user_message", str(error)); self.status.setText(f"Không thể tạo video: {message}")
        QMessageBox.critical(self, "Tạo Highlight bóng đá thất bại", message)

    def _finished(self) -> None:
        if self.thread: self.thread.deleteLater()
        self.thread = None; self.worker = None; self.pipeline = None; self.stop_button.hide(); self.start_button.show(); self.start_button.setEnabled(True)

    def shutdown(self, timeout_ms: int = 5_000) -> bool:
        """Cancel a running football job before the Qt widget is destroyed."""
        self.stop()
        return self.thread is None or self.thread.wait(timeout_ms)

    def to_safe_dict(self) -> dict[str, object]:
        return {"youtube_url": self.youtube_url.text().strip(), "json": self.json_editor.toPlainText(), "tts_provider": self.voice_provider, "voice": self.voice_id, "voice_name": self.voice_name, "voice_language": self.voice_language, "voice_gender": self.voice_gender, "voice_family": self.voice_family, "voice_categories": self.voice_categories, "voice_volume": self.voice_volume, "burn_subtitles": self.burn_subtitles.isChecked(), "ken_burns_start_zoom": self.ken_burns_start.value(), "ken_burns_end_zoom": self.ken_burns_end.value(), "output_folder": self.output_folder.text().strip()}

    def set_settings(self, settings: dict, project_uuid: str | None = None) -> None:
        self._loading = True
        try:
            if project_uuid:
                self.project_uuid = project_uuid
            self.youtube_url.setText(str(settings.get("youtube_url", "")))
            self.json_editor.setPlainText(str(settings.get("json", FootballHighlightInput.example_json())))
            self.voice_provider = str(settings.get("tts_provider", "capcut"))
            self.voice_id = str(settings.get("voice", "")); self.voice_name = str(settings.get("voice_name", self.voice_id or "Chưa chọn giọng"))
            self.voice_language = str(settings.get("voice_language", "vi-VN")); self.voice_gender = str(settings.get("voice_gender", "")); self.voice_family = str(settings.get("voice_family", ""))
            categories = settings.get("voice_categories", []); self.voice_categories = [str(value) for value in categories] if isinstance(categories, list) else []
            self.voice_volume = int(settings.get("voice_volume", 100)); self.burn_subtitles.setChecked(bool(settings.get("burn_subtitles", True))); self.ken_burns_start.setValue(float(settings.get("ken_burns_start_zoom", 1.00))); self.ken_burns_end.setValue(float(settings.get("ken_burns_end_zoom", 1.15))); self.output_folder.setText(str(settings.get("output_folder", "")))
            self._update_voice_summary()
        finally:
            self._loading = False
