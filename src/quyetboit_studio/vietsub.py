from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .models import (
    STEP_TITLES,
    ProjectModel,
    SrtProvider,
    TranslationContext,
    TranslationProvider,
    TtsProvider,
    VideoSource,
    detect_video_source,
)
from .studio import StudioDialog
from .widgets import Badge, Card, LogPanel
from .workflow import WorkflowController


def button(text: str, primary: bool = False) -> QPushButton:
    result = QPushButton(text)
    if primary:
        result.setObjectName("Primary")
    return result


def set_table(table: QTableWidget, headers: list[str], rows: list[list[str]]) -> None:
    table.setColumnCount(len(headers)); table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows)); table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    for row_index, row in enumerate(rows):
        for col, value in enumerate(row):
            table.setItem(row_index, col, QTableWidgetItem(value))
    table.horizontalHeader().setStretchLastSection(True)


class SettingsDialog(QDialog):
    def __init__(self, title: str, fields: list[tuple[str, QWidget]], parent: QWidget | None = None,
                 note: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title); self.setMinimumWidth(440)
        root = QVBoxLayout(self)
        if note:
            hint = QLabel(note); hint.setWordWrap(True); hint.setObjectName("Subtitle"); root.addWidget(hint)
        form = QFormLayout(); form.setSpacing(12)
        for label, widget in fields: form.addRow(label, widget)
        root.addLayout(form)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        controls.accepted.connect(self.accept); controls.rejected.connect(self.reject); root.addWidget(controls)


class StepRow(QWidget):
    clicked = Signal(int)

    def __init__(self, index: int, title: str, status: str) -> None:
        super().__init__(); self.index = index
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.nav = QPushButton(f"{index + 1:02d}  {title}")
        self.nav.setObjectName("StepButton"); self.nav.setCheckable(True)
        self.nav.clicked.connect(lambda: self.clicked.emit(index))
        self.badge = Badge(status); self.badge.setFixedWidth(78)
        layout.addWidget(self.nav, 1); layout.addWidget(self.badge)


class BaseStep(QWidget):
    changed = Signal()

    def __init__(self, project: ProjectModel, title: str, subtitle: str) -> None:
        super().__init__(); self.project = project
        root = QVBoxLayout(self); root.setContentsMargins(4, 4, 4, 4); root.setSpacing(12)
        heading = QLabel(title); heading.setObjectName("Title"); root.addWidget(heading)
        sub = QLabel(subtitle); sub.setObjectName("Subtitle"); sub.setWordWrap(True); root.addWidget(sub)
        self.content = QVBoxLayout(); self.content.setSpacing(12); root.addLayout(self.content); root.addStretch()


class SourceStep(BaseStep):
    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "1. Nhập video", "Dán link video; nguồn được nhận diện tự động theo domain.")
        card = Card("Nguồn video")
        row = QHBoxLayout(); self.url = QLineEdit(project.source_url); self.url.setPlaceholderText("https://www.douyin.com/video/...")
        paste = button("Dán"); clear = button("Xóa"); analyze = button("Phân tích", True)
        paste.clicked.connect(self._paste); clear.clicked.connect(self.url.clear); analyze.clicked.connect(self.analyze)
        row.addWidget(self.url, 1); row.addWidget(paste); row.addWidget(clear); row.addWidget(analyze); card.layout.addLayout(row)
        self.validation = QLabel("Hỗ trợ Douyin, Xiaohongshu và Bilibili"); self.validation.setObjectName("Subtitle")
        card.layout.addWidget(self.validation); self.content.addWidget(card)
        self.meta = Card("Thông tin video", "Nhấn Phân tích để xem metadata mô phỏng")
        self.meta_grid = QGridLayout(); self.meta.layout.addLayout(self.meta_grid); self.content.addWidget(self.meta)
        download = button("Tải video (mô phỏng)", True); download.clicked.connect(lambda: self.changed.emit())
        self.meta.layout.addWidget(download, 0, Qt.AlignmentFlag.AlignRight)
        if project.metadata: self._show_metadata(project.metadata)

    def _paste(self) -> None:
        from PySide6.QtWidgets import QApplication
        self.url.setText(QApplication.clipboard().text())

    def analyze(self) -> bool:
        try:
            source = detect_video_source(self.url.text())
        except ValueError as exc:
            self.validation.setText(str(exc)); self.validation.setStyleSheet("color:#b42318;")
            return False
        names = {VideoSource.DOUYIN: "Douyin", VideoSource.XIAOHONGSHU: "Xiaohongshu", VideoSource.BILIBILI: "Bilibili"}
        self.project.source_url = self.url.text().strip(); self.project.source = source.value
        self.project.metadata = {"Nền tảng": names[source], "Tiêu đề": "Video mẫu — prototype UI",
                                 "Thời lượng": "02:48", "Độ phân giải": "1080 × 1920", "Dung lượng": "36,4 MB (ước tính)"}
        self.validation.setText(f"✓ URL hợp lệ · Nguồn: {names[source]}"); self.validation.setStyleSheet("color:#027a48;")
        self._show_metadata(self.project.metadata); self.changed.emit(); return True

    def _show_metadata(self, metadata: dict) -> None:
        while self.meta_grid.count():
            item = self.meta_grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for i, (key, value) in enumerate(metadata.items()):
            k = QLabel(key); k.setObjectName("Subtitle"); v = QLabel(str(value)); v.setWordWrap(True)
            self.meta_grid.addWidget(k, i, 0); self.meta_grid.addWidget(v, i, 1)


class AudioSplitStep(BaseStep):
    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "2. Tách & chia âm thanh", "Tách audio khỏi video và chia nhỏ để xử lý SRT nhanh hơn.")
        summary = Card("Thiết lập", "Đầu vào: video_original.mp4  →  Đầu ra: workspace/audio/")
        config = button("Cấu hình nâng cao"); config.clicked.connect(self.configure); summary.layout.addWidget(config, 0, Qt.AlignmentFlag.AlignRight)
        self.content.addWidget(summary)
        chunks = Card("Các đoạn audio dự kiến")
        table = QTableWidget(); set_table(table, ["Đoạn", "Bắt đầu", "Kết thúc", "Trạng thái"],
            [[f"audio_{i:02d}.wav", f"{i-1:02d}:00", f"{i:02d}:00", "Chờ"] for i in range(1, 4)])
        chunks.layout.addWidget(table); self.content.addWidget(chunks)

    def configure(self) -> None:
        fmt = QComboBox(); fmt.addItems(["wav", "mp3", "m4a"]); fmt.setCurrentText(self.project.audio_settings["format"])
        rate = QComboBox(); rate.addItems(["16000", "22050", "44100", "48000"]); rate.setCurrentText(str(self.project.audio_settings["sample_rate"]))
        channels = QComboBox(); channels.addItems(["Mono", "Stereo"]); channels.setCurrentText(self.project.audio_settings["channels"])
        chunk = QSpinBox(); chunk.setRange(15, 300); chunk.setSuffix(" giây"); chunk.setValue(self.project.audio_settings["chunk_seconds"])
        dlg = SettingsDialog("Cấu hình tách audio", [("Định dạng", fmt), ("Sample rate", rate), ("Kênh", channels), ("Mỗi đoạn", chunk)], self)
        if dlg.exec():
            self.project.audio_settings = {"format": fmt.currentText(), "sample_rate": int(rate.currentText()), "channels": channels.currentText(), "chunk_seconds": chunk.value()}; self.changed.emit()


class SrtStep(BaseStep):
    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "3. Chuyển audio thành SRT", "Chọn một dịch vụ nhận diện giọng nói.")
        provider = Card("Dịch vụ SRT")
        self.group = QButtonGroup(self)
        for value in SrtProvider:
            radio = QRadioButton(value.value); radio.setChecked(project.srt_provider == value.value); self.group.addButton(radio); provider.layout.addWidget(radio)
        api = button("Cấu hình API"); api.clicked.connect(self.api_dialog); provider.layout.addWidget(api, 0, Qt.AlignmentFlag.AlignRight)
        self.group.buttonClicked.connect(self._changed); self.content.addWidget(provider)
        preview = Card("Kết quả mô phỏng")
        view = QPlainTextEdit("1\n00:00:00,000 --> 00:00:03,400\n你好，欢迎来到今天的视频。\n\n2\n00:00:03,500 --> 00:00:07,000\n我们来看看这个有趣的故事。")
        view.setReadOnly(True); preview.layout.addWidget(view); self.content.addWidget(preview)

    def _changed(self, radio: QRadioButton) -> None:
        self.project.srt_provider = radio.text(); self.changed.emit()

    def api_dialog(self) -> None:
        token = QLineEdit(); token.setEchoMode(QLineEdit.EchoMode.Password); cookie = QLineEdit(); cookie.setEchoMode(QLineEdit.EchoMode.Password)
        test = button("Kiểm tra kết nối (mô phỏng)"); test.clicked.connect(lambda: QMessageBox.information(self, "Kết nối", "Kết nối mô phỏng thành công.\nCredential không được lưu."))
        dlg = SettingsDialog("Cấu hình API", [("Token", token), ("Cookie", cookie), ("", test)], self, "Dữ liệu nhạy cảm chỉ tồn tại trong phiên hiện tại.")
        dlg.exec()


class TranslateStep(BaseStep):
    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "4. Dịch SRT sang tiếng Việt", "Chọn translator và giọng điệu phù hợp với nội dung.")
        config = Card("Translator & ngữ cảnh"); form = QFormLayout()
        self.provider = QComboBox(); self.provider.addItems([v.value for v in TranslationProvider]); self.provider.setCurrentText(project.translation_provider)
        self.context = QComboBox(); self.context.addItems([v.value for v in TranslationContext]); self.context.setCurrentText(project.translation_context)
        self.custom = QPlainTextEdit(project.custom_context); self.custom.setPlaceholderText("Mô tả phong cách dịch mong muốn..."); self.custom.setMaximumHeight(80)
        form.addRow("Dịch bằng", self.provider); form.addRow("Giọng điệu", self.context); form.addRow("Context riêng", self.custom); config.layout.addLayout(form)
        advanced = button("Cấu hình translator"); advanced.clicked.connect(self.configure); config.layout.addWidget(advanced, 0, Qt.AlignmentFlag.AlignRight)
        self.content.addWidget(config); self.context.currentTextChanged.connect(self._context_changed); self._context_changed(self.context.currentText())
        self.provider.currentTextChanged.connect(self._save); self.custom.textChanged.connect(self._save)
        preview = Card("Đối chiếu bản dịch")
        table = QTableWidget(); set_table(table, ["Thời gian", "Bản gốc", "Tiếng Việt"], [["00:00–00:03", "你好，欢迎来到今天的视频。", "Xin chào, chào mừng bạn đến với video hôm nay!"], ["00:03–00:07", "我们来看看这个有趣的故事。", "Cùng xem câu chuyện thú vị này nhé!"]]); preview.layout.addWidget(table); self.content.addWidget(preview)

    def _context_changed(self, text: str) -> None:
        self.custom.setVisible(text == TranslationContext.CUSTOM.value); self._save()

    def _save(self) -> None:
        self.project.translation_provider = self.provider.currentText(); self.project.translation_context = self.context.currentText(); self.project.custom_context = self.custom.toPlainText(); self.changed.emit()

    def configure(self) -> None:
        model = QComboBox(); model.addItems(["gemini-2.5-flash", "gemini-2.5-pro", "Browser mặc định"])
        key = QLineEdit(); key.setEchoMode(QLineEdit.EchoMode.Password)
        profile = QLineEdit(); profile.setPlaceholderText("Default")
        SettingsDialog("Cấu hình translator", [("Model", model), ("API key", key), ("Browser profile", profile)], self, "API key không được ghi vào project hoặc log.").exec()


class TtsStep(BaseStep):
    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "5. Tạo voice tiếng Việt", "Dùng SRT tiếng Việt để tổng hợp giọng đọc.")
        card = Card("Text-to-Speech")
        self.provider = QComboBox(); self.provider.addItems([v.value for v in TtsProvider]); self.provider.setCurrentText(project.tts_provider)
        card.layout.addWidget(self.provider); config = button("Cấu hình giọng đọc"); config.clicked.connect(self.configure); card.layout.addWidget(config, 0, Qt.AlignmentFlag.AlignRight)
        self.provider.currentTextChanged.connect(self._save); self.content.addWidget(card)
        lines = Card("Đoạn thoại")
        table = QTableWidget(); set_table(table, ["#", "Nội dung", "Thời lượng", "Trạng thái"], [["1", "Xin chào, chào mừng bạn đến với video hôm nay!", "3,4s", "Chờ"], ["2", "Cùng xem câu chuyện thú vị này nhé!", "3,5s", "Chờ"]]); lines.layout.addWidget(table); self.content.addWidget(lines)

    def _save(self) -> None: self.project.tts_provider = self.provider.currentText(); self.changed.emit()

    def configure(self) -> None:
        voice = QComboBox(); voice.addItems(["vi-VN-Neural2-A", "vi-VN-Neural2-D", "CapCut Nữ trẻ"]); voice.setCurrentText(self.project.tts_settings["voice"])
        speed = QSpinBox(); speed.setRange(50, 200); speed.setSuffix("%"); speed.setValue(round(self.project.tts_settings["speed"] * 100))
        pitch = QSpinBox(); pitch.setRange(-12, 12); pitch.setValue(self.project.tts_settings["pitch"])
        volume = QSpinBox(); volume.setRange(0, 150); volume.setSuffix("%"); volume.setValue(self.project.tts_settings["volume"])
        sample = button("Nghe thử (mô phỏng)"); sample.clicked.connect(lambda: QMessageBox.information(self, "Nghe thử", "Đang phát giọng mẫu mô phỏng."))
        dlg = SettingsDialog("Cấu hình giọng đọc", [("Giọng", voice), ("Tốc độ", speed), ("Pitch", pitch), ("Âm lượng", volume), ("", sample)], self)
        if dlg.exec():
            self.project.tts_settings = {"voice": voice.currentText(), "speed": speed.value()/100, "pitch": pitch.value(), "volume": volume.value()}; self.changed.emit()


class MixStep(BaseStep):
    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "6. Ghép voice vào video", "Đồng bộ voice tiếng Việt với video gốc.")
        flow = Card("Luồng đầu vào", "video_original.mp4  +  voice_vi.wav  →  video_mixed.mp4")
        self.keep = QCheckBox("Giữ âm thanh gốc"); self.keep.setChecked(project.mix_settings["keep_original"]); flow.layout.addWidget(self.keep)
        self.original = QSlider(Qt.Orientation.Horizontal); self.original.setRange(0, 100); self.original.setValue(project.mix_settings["original_volume"])
        self.voice = QSlider(Qt.Orientation.Horizontal); self.voice.setRange(0, 150); self.voice.setValue(project.mix_settings["voice_volume"])
        flow.layout.addWidget(QLabel("Âm lượng gốc")); flow.layout.addWidget(self.original); flow.layout.addWidget(QLabel("Âm lượng voice")); flow.layout.addWidget(self.voice)
        for control in (self.keep, self.original, self.voice):
            signal = control.toggled if isinstance(control, QCheckBox) else control.valueChanged; signal.connect(self._save)
        self.content.addWidget(flow)

    def _save(self) -> None:
        self.project.mix_settings = {"keep_original": self.keep.isChecked(), "original_volume": self.original.value(), "voice_volume": self.voice.value()}; self.changed.emit()


class StudioStep(BaseStep):
    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "7. Chỉnh sửa trong Studio", "Tinh chỉnh hình ảnh, logo, subtitle và text overlay trong một workspace riêng.")
        card = Card("Quyết Bồ Video Studio", "Preview · Media assets · Properties · Timeline nhiều layer")
        features = QLabel("Tốc độ & Pitch  •  Zoom & Lật  •  Màu sắc  •  Logo  •  Chữ đầu video\nPhụ đề  •  Overlay chữ chạy  •  Undo/Redo  •  Kéo-thả layer")
        features.setWordWrap(True); card.layout.addWidget(features)
        open_btn = button("Mở Video Studio", True); open_btn.clicked.connect(self.open_studio); card.layout.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.summary = QLabel(); self.summary.setObjectName("Subtitle"); card.layout.addWidget(self.summary); self.content.addWidget(card); self._refresh()

    def open_studio(self) -> None:
        dlg = StudioDialog(self.project.studio_settings, self); dlg.settings_applied.connect(self._apply); dlg.exec()

    def _apply(self, settings: dict) -> None:
        self.project.studio_settings = settings; self._refresh(); self.changed.emit()

    def _refresh(self) -> None:
        s = self.project.studio_settings; self.summary.setText(f"Đã cấu hình: {len(s.get('layers', []))} layer · tốc độ {s.get('speed', 1)}× · zoom {s.get('zoom', 100)}%")


class ExportStep(BaseStep):
    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "8. Xuất video", "Cấu hình file đầu ra và mô phỏng quá trình render.")
        card = Card("Thiết lập xuất video"); form = QFormLayout(); e = project.export_settings
        folder_row = QWidget(); folder_layout = QHBoxLayout(folder_row); folder_layout.setContentsMargins(0,0,0,0)
        self.folder = QLineEdit(e["folder"]); browse = button("Chọn..."); browse.clicked.connect(self._browse); folder_layout.addWidget(self.folder); folder_layout.addWidget(browse)
        self.filename = QLineEdit(e["filename"]); self.format = QComboBox(); self.format.addItems(["mp4", "mov", "mkv"]); self.format.setCurrentText(e["format"])
        self.resolution = QComboBox(); self.resolution.addItems(["720p", "1080p", "1440p", "4K"]); self.resolution.setCurrentText(e["resolution"])
        self.fps = QComboBox(); self.fps.addItems(["24", "25", "30", "50", "60"]); self.fps.setCurrentText(str(e["fps"]))
        self.quality = QComboBox(); self.quality.addItems(["Nhanh", "Cân bằng", "Cao"]); self.quality.setCurrentText(e["quality"])
        for label, widget in (("Thư mục", folder_row), ("Tên file", self.filename), ("Định dạng", self.format), ("Độ phân giải", self.resolution), ("FPS", self.fps), ("Chất lượng", self.quality)): form.addRow(label, widget)
        card.layout.addLayout(form); self.output = QLabel("Output sẽ hiển thị sau khi hoàn thành."); self.output.setObjectName("Subtitle"); card.layout.addWidget(self.output); self.content.addWidget(card)
        self.open_folder = button("Mở thư mục output"); self.open_folder.setEnabled(False)
        self.open_folder.clicked.connect(self._open_output_folder); card.layout.addWidget(self.open_folder, 0, Qt.AlignmentFlag.AlignRight)
        for widget in (self.folder, self.filename, self.format, self.resolution, self.fps, self.quality):
            signal = widget.textChanged if isinstance(widget, QLineEdit) else widget.currentTextChanged; signal.connect(self._save)

    def _browse(self) -> None:
        value = QFileDialog.getExistingDirectory(self, "Chọn thư mục xuất")
        if value: self.folder.setText(value)

    def _save(self) -> None:
        self.project.export_settings = {"folder": self.folder.text(), "filename": self.filename.text(), "format": self.format.currentText(), "resolution": self.resolution.currentText(), "fps": int(self.fps.currentText()), "quality": self.quality.currentText()}; self.changed.emit()

    def validate(self) -> bool:
        if not self.folder.text().strip() or not self.filename.text().strip():
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập thư mục và tên file xuất."); return False
        return True

    def _open_output_folder(self) -> None:
        folder = Path(self.folder.text())
        if not folder.exists():
            QMessageBox.information(self, "Output mô phỏng", f"Prototype chưa tạo thư mục thật:\n{folder}")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))


class VietSubPage(QWidget):
    dirty_changed = Signal(bool)
    request_new = Signal(); request_open = Signal(); request_save = Signal()

    def __init__(self, project: ProjectModel) -> None:
        super().__init__(); self.setObjectName("AppRoot"); self.project = project; self.current_step = 0
        self._build_ui(); self.set_project(project)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self); root.setContentsMargins(22, 18, 22, 18); root.setSpacing(12)
        header = QHBoxLayout(); title_box = QVBoxLayout(); title = QLabel("VietSub Automation"); title.setObjectName("Title")
        self.project_label = QLabel(); self.project_label.setObjectName("Subtitle"); title_box.addWidget(title); title_box.addWidget(self.project_label)
        header.addLayout(title_box); header.addStretch()
        for text, signal in (("Dự án mới", self.request_new), ("Mở", self.request_open), ("Lưu", self.request_save)):
            b = button(text, text == "Lưu"); b.clicked.connect(signal.emit); header.addWidget(b)
        root.addLayout(header)
        controls = Card(); row = QHBoxLayout()
        self.run_all_btn = button("▶ Chạy tất cả", True); self.pause_btn = button("Tạm dừng"); self.resume_btn = button("Tiếp tục"); self.stop_btn = button("Dừng"); self.reset_all_btn = button("Đặt lại")
        for b in (self.run_all_btn, self.pause_btn, self.resume_btn, self.stop_btn, self.reset_all_btn): row.addWidget(b)
        row.addStretch(); row.addWidget(QLabel("Tổng tiến trình")); self.overall = QProgressBar(); self.overall.setFixedWidth(220); row.addWidget(self.overall); controls.layout.addLayout(row); root.addWidget(controls)
        middle = QHBoxLayout(); middle.setSpacing(12)
        self.step_panel = Card("Quy trình 8 bước"); self.step_panel.setFixedWidth(300); self.step_rows: list[StepRow] = []
        middle.addWidget(self.step_panel)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.stack = QStackedWidget(); self.scroll.setWidget(self.stack); middle.addWidget(self.scroll, 1); root.addLayout(middle, 1)
        action = Card(); ar = QHBoxLayout(); self.step_badge = Badge("ready"); self.step_progress = QProgressBar(); self.step_progress.setValue(0)
        self.run_step_btn = button("Chạy bước này", True); self.retry_btn = button("Chạy lại"); self.reset_step_btn = button("Đặt lại bước")
        ar.addWidget(self.step_badge); ar.addWidget(self.step_progress, 1); ar.addWidget(self.run_step_btn); ar.addWidget(self.retry_btn); ar.addWidget(self.reset_step_btn); action.layout.addLayout(ar); root.addWidget(action)
        self.log = LogPanel(); root.addWidget(self.log)
        self.run_all_btn.clicked.connect(self._run_all); self.run_step_btn.clicked.connect(self._run_step)
        self.pause_btn.clicked.connect(lambda: self.controller.pause()); self.resume_btn.clicked.connect(lambda: self.controller.resume())
        self.stop_btn.clicked.connect(lambda: self.controller.stop()); self.retry_btn.clicked.connect(lambda: self.controller.retry(self.current_step))
        self.reset_step_btn.clicked.connect(lambda: self.controller.reset_step(self.current_step)); self.reset_all_btn.clicked.connect(lambda: self.controller.reset())

    def set_project(self, project: ProjectModel) -> None:
        self.project = project; self.project_label.setText(f"{project.name}  •  Chưa lưu")
        while self.stack.count(): self.stack.removeWidget(self.stack.widget(0))
        for row in self.step_rows: row.deleteLater()
        self.step_rows.clear()
        self.pages = [SourceStep(project), AudioSplitStep(project), SrtStep(project), TranslateStep(project), TtsStep(project), MixStep(project), StudioStep(project), ExportStep(project)]
        for i, page in enumerate(self.pages):
            page.changed.connect(lambda: self.dirty_changed.emit(True)); self.stack.addWidget(page)
            row = StepRow(i, STEP_TITLES[i], project.step_statuses[i]); row.clicked.connect(self.select_step); self.step_rows.append(row); self.step_panel.layout.insertWidget(i + 1, row)
        self.controller = WorkflowController(project); self._connect_controller(); self.select_step(0); self.log.append_log("INFO", "Project đã sẵn sàng. Đây là chế độ mô phỏng UI.")

    def _connect_controller(self) -> None:
        self.controller.step_status_changed.connect(self._status_changed); self.controller.step_progress_changed.connect(self._step_progress_changed)
        self.controller.overall_progress_changed.connect(self.overall.setValue); self.controller.log_emitted.connect(self.log.append_log)
        self.controller.dirty_changed.connect(self.dirty_changed); self.controller.workflow_completed.connect(self._completed)

    def select_step(self, index: int) -> None:
        self.current_step = index; self.stack.setCurrentIndex(index)
        for i, row in enumerate(self.step_rows): row.nav.setChecked(i == index)
        self.step_badge.set_status(self.project.step_statuses[index]); self.step_progress.setValue(100 if self.project.step_statuses[index] == "completed" else 0)
        allowed, reason = self.controller.can_run(index); self.run_step_btn.setEnabled(allowed); self.run_step_btn.setToolTip(reason)

    def _run_step(self) -> None:
        if self.current_step == 0 and not self.pages[0].analyze(): return
        if self.current_step == 3 and self.project.translation_context == TranslationContext.CUSTOM.value and not self.project.custom_context.strip():
            QMessageBox.warning(self, "Thiếu context", "Vui lòng nhập context dịch tùy chỉnh."); return
        if self.current_step == 7 and not self.pages[7].validate(): return
        self.controller.run_step(self.current_step)

    def _run_all(self) -> None:
        if not self.pages[0].analyze(): self.select_step(0); return
        self.controller.run_all()

    def _status_changed(self, index: int, status: str) -> None:
        self.step_rows[index].badge.set_status(status)
        if index == self.current_step: self.step_badge.set_status(status)
        allowed, reason = self.controller.can_run(self.current_step); self.run_step_btn.setEnabled(allowed); self.run_step_btn.setToolTip(reason)

    def _step_progress_changed(self, index: int, value: int) -> None:
        if index == self.current_step: self.step_progress.setValue(value)

    def _completed(self) -> None:
        e = self.project.export_settings; path = str(Path(e["folder"]) / f'{e["filename"]}.{e["format"]}')
        self.project.output = {"video": path}; self.pages[7].output.setText(f"✓ Hoàn thành mô phỏng: {path}")
        self.pages[7].open_folder.setEnabled(True)
        QMessageBox.information(self, "Hoàn thành", f"Pipeline mô phỏng đã hoàn thành.\n\n{path}")
