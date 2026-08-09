from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
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
    QListWidget,
    QListWidgetItem,
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .core.preferences import LastUsedSettingsStore
from .models import (
    STEP_TITLES,
    ProjectModel,
    SourceMode,
    SrtProvider,
    TranslationContext,
    TranslationProvider,
    TtsProvider,
    VideoSource,
    detect_video_source,
)
from .pipeline import PipelineDependencies, VietSubPipeline
from .services.capcut import CapCutTtsService, Voice
from .services.douyin import DouyinDownloader, SeriesMetadata
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


class ServiceTaskThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.operation = operation

    def run(self) -> None:
        try:
            self.succeeded.emit(self.operation())  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 - surface provider failures in the dialog
            message = getattr(exc, "user_message", str(exc))
            self.failed.emit(message)


class VoiceCatalogDialog(QDialog):
    FILTERS = (
        ("Tất cả", "all"), ("Giọng nam", "male"), ("Giọng nữ", "female"),
        ("Tiếng Việt", "vietnamese"), ("Tiếng Anh", "english"),
        ("Tiếng Trung", "zhongwen"), ("Tiếng Indonesia", "indonesian"),
        ("Tiếng Thái", "taiyu"), ("Xu hướng", "trend"), ("TikTok", "TikTok"),
        ("Thuyết minh", "narration"), ("Anime", "anime"),
    )

    def __init__(
        self, service: CapCutTtsService, selected_speaker: str, settings: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.selected_speaker = selected_speaker
        self.voices: list[Voice] = []
        self._tasks: list[ServiceTaskThread] = []
        self.setWindowTitle("Cấu hình giọng đọc CapCut")
        self.resize(680, 620)
        root = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.category = QComboBox()
        for label, key in self.FILTERS:
            self.category.addItem(label, key)
        self.search = QLineEdit(); self.search.setPlaceholderText("Tìm theo tên giọng…")
        self.refresh = button("Làm mới danh sách")
        filters.addWidget(self.category); filters.addWidget(self.search, 1); filters.addWidget(self.refresh)
        root.addLayout(filters)
        self.list = QListWidget(); root.addWidget(self.list, 1)
        self.status = QLabel("Đang tải danh sách giọng…"); self.status.setObjectName("Subtitle")
        root.addWidget(self.status)
        form = QFormLayout()
        self.speed = QSpinBox(); self.speed.setRange(50, 200); self.speed.setSuffix("%")
        self.speed.setValue(round(float(settings.get("speed", 1)) * 100))
        self.pitch = QSpinBox(); self.pitch.setRange(-12, 12); self.pitch.setValue(int(settings.get("pitch", 0)))
        self.volume = QSpinBox(); self.volume.setRange(0, 150); self.volume.setSuffix("%")
        self.volume.setValue(int(settings.get("volume", 100)))
        self.concurrency = QSpinBox(); self.concurrency.setRange(1, 50)
        self.concurrency.setValue(max(1, min(50, int(settings.get("concurrency", 25)))))
        for label, widget in (("Tốc độ", self.speed), ("Pitch", self.pitch),
                              ("Âm lượng", self.volume), ("Số request đồng thời", self.concurrency)):
            form.addRow(label, widget)
        root.addLayout(form)
        actions = QHBoxLayout(); self.preview = button("▶ Nghe thử")
        actions.addWidget(self.preview); actions.addStretch(); root.addLayout(actions)
        self.controls = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        self.controls.accepted.connect(self.accept); self.controls.rejected.connect(self.reject)
        root.addWidget(self.controls)
        self.category.currentIndexChanged.connect(self._filter)
        self.search.textChanged.connect(self._filter)
        self.refresh.clicked.connect(lambda: self._load(True))
        self.preview.clicked.connect(self._preview)
        self.list.itemDoubleClicked.connect(lambda _item: self._preview())
        self._set_busy(True); self._load(False)

    def _start_task(self, operation: object, callback: object) -> None:
        task = ServiceTaskThread(operation, self)
        self._tasks.append(task)
        task.succeeded.connect(callback)  # type: ignore[arg-type]
        task.failed.connect(self._failed)
        task.finished.connect(lambda: self._finish_task(task))
        task.start()

    def _finish_task(self, task: ServiceTaskThread) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        task.deleteLater()
        self._set_busy(bool(self._tasks))

    def _set_busy(self, busy: bool) -> None:
        self.refresh.setEnabled(not busy)
        self.preview.setEnabled(not busy and self.list.currentItem() is not None)
        save = self.controls.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(not busy and self.list.currentItem() is not None)

    def _load(self, refresh: bool) -> None:
        self.status.setText("Đang tải danh sách giọng CapCut…")
        self._set_busy(True)
        self._start_task(lambda: self.service.list_voices(refresh=refresh), self._loaded)

    def _loaded(self, voices: object) -> None:
        self.voices = list(voices)  # type: ignore[arg-type]
        self._filter()
        fetched_at = getattr(self.service, "catalog_fetched_at", 0)
        fetched_text = (
            datetime.fromtimestamp(fetched_at, UTC).astimezone().strftime("%d/%m/%Y %H:%M:%S")
            if fetched_at else "không xác định"
        )
        self.status.setText(
            f"Có {len(self.voices)} giọng · lấy lúc {fetched_text} · bấm Làm mới để gọi lại API"
        )

    @staticmethod
    def _is_gender(voice: Voice, expected: str) -> bool:
        value = voice.gender.casefold()
        aliases = {"male": ("male", "nam", "man"), "female": ("female", "nữ", "nu", "woman")}
        return any(alias in value for alias in aliases[expected]) or f"{expected}_voice" in voice.categories

    def _filter(self) -> None:
        key = str(self.category.currentData() or "all")
        query = self.search.text().strip().casefold()
        self.list.clear()
        selected_item: QListWidgetItem | None = None
        for voice in self.voices:
            matches_category = (
                key == "all" or (key in {"male", "female"} and self._is_gender(voice, key))
                or key in voice.categories
            )
            if not matches_category or (query and query not in voice.name.casefold()):
                continue
            details = ", ".join(voice.categories) or voice.category
            item = QListWidgetItem(f"{voice.name}  ·  {details}")
            item.setData(Qt.ItemDataRole.UserRole, voice)
            self.list.addItem(item)
            if voice.speaker == self.selected_speaker:
                selected_item = item
        if selected_item:
            self.list.setCurrentItem(selected_item)
        elif self.list.count():
            self.list.setCurrentRow(0)
        self._set_busy(bool(self._tasks))

    def selected_voice(self) -> Voice | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _preview(self) -> None:
        voice = self.selected_voice()
        if not voice:
            return
        self.status.setText(f"Đang lấy audio nghe thử: {voice.name}…")
        self._set_busy(True)
        self._start_task(lambda: self.service.preview_url(voice), self._play_preview)

    def _play_preview(self, url: object) -> None:
        if not QDesktopServices.openUrl(QUrl(str(url))):
            self.status.setText("Không mở được trình phát audio mặc định.")
        else:
            self.status.setText("Đã mở audio nghe thử bằng trình phát mặc định.")

    def _failed(self, message: str) -> None:
        self.status.setText(message)

    def reject(self) -> None:
        if self._tasks:
            self.status.setText("Vui lòng chờ request hiện tại kết thúc trước khi đóng.")
            return
        super().reject()


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
    def __init__(
        self, project: ProjectModel, downloader: DouyinDownloader | None = None,
    ) -> None:
        super().__init__(
            project, "1. Nhập video",
            "Chọn tải một tập, toàn bộ Series hoặc gộp nhiều link thành một video nguồn.",
        )
        self.downloader = downloader or DouyinDownloader()
        self._tasks: list[ServiceTaskThread] = []
        self._loading_series = False
        if not project.__dict__.get("_douyin_cookies_file"):
            saved_cookie_path = ""
            try:
                from .services.credentials import SecureValueStore
                saved_cookie_path = SecureValueStore().load("douyin_cookie_path")
            except Exception:  # noqa: BLE001 - unavailable credential backend is non-fatal
                saved_cookie_path = ""
            if saved_cookie_path and Path(saved_cookie_path).is_file():
                project.__dict__["_douyin_cookies_file"] = saved_cookie_path

        card = Card("Nguồn video")
        self.tabs = QTabWidget()
        self.tabs.addTab(self._single_tab(), "Tải từng tập")
        self.tabs.addTab(self._series_tab(), "Tải Theo Series")
        self.tabs.addTab(self._multi_tab(), "Tải nhiều Videos")
        for index, mode in enumerate(SourceMode):
            self.tabs.tabBar().setTabData(index, mode.value)
        mode_index = next(
            (index for index in range(self.tabs.count())
             if self.tabs.tabBar().tabData(index) == project.source_mode),
            -1,
        )
        self.tabs.setCurrentIndex(max(mode_index, 0))
        self.tabs.currentChanged.connect(self._source_mode_changed)
        card.layout.addWidget(self.tabs)
        self.validation = QLabel("Pipeline hiện hỗ trợ nguồn Douyin")
        self.validation.setObjectName("Subtitle")
        card.layout.addWidget(self.validation)
        auth_row = QHBoxLayout()
        auth = button("Cookie / profile trình duyệt"); auth.clicked.connect(self.configure_auth)
        self.auth_status = QLabel(); self.auth_status.setObjectName("Subtitle")
        auth_row.addWidget(auth); auth_row.addWidget(self.auth_status, 1)
        card.layout.addLayout(auth_row); self.content.addWidget(card)
        self._refresh_auth_status()
        self.meta = Card(
            "Thông tin video", "Metadata đầy đủ sẽ được yt-dlp xác nhận khi chạy bước tải.",
        )
        self.meta_grid = QGridLayout(); self.meta.layout.addLayout(self.meta_grid); self.content.addWidget(self.meta)
        download = button("Sẵn sàng tải", True); download.clicked.connect(self.analyze)
        self.meta.layout.addWidget(download, 0, Qt.AlignmentFlag.AlignRight)
        if project.metadata:
            self._show_metadata(project.metadata)

    def _single_tab(self) -> QWidget:
        page = QWidget(); row = QHBoxLayout(page); row.setContentsMargins(8, 10, 8, 10)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.url = QLineEdit(self.project.source_url)
        self.url.setPlaceholderText("https://www.douyin.com/video/...")
        paste = button("Dán"); clear = button("Xóa")
        paste.clicked.connect(lambda: self._paste_to(self.url)); clear.clicked.connect(self.url.clear)
        row.addWidget(self.url, 1); row.addWidget(paste); row.addWidget(clear)
        return page

    def _series_tab(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(8, 10, 8, 10)
        row = QHBoxLayout()
        self.series_url = QLineEdit(self.project.source_url if self.project.source_mode == SourceMode.SERIES else "")
        self.series_url.setPlaceholderText("Link một tập hoặc https://www.douyin.com/collection/...")
        paste = button("Dán"); self.load_series_btn = button("Tải danh sách tập", True)
        paste.clicked.connect(lambda: self._paste_to(self.series_url))
        self.load_series_btn.clicked.connect(self._load_series)
        row.addWidget(self.series_url, 1); row.addWidget(paste); row.addWidget(self.load_series_btn)
        layout.addLayout(row)
        self.series_status = QLabel("Dán link một tập thuộc Series/合集 rồi tải danh sách.")
        self.series_status.setObjectName("Subtitle"); layout.addWidget(self.series_status)
        self.series_table = QTableWidget(0, 4)
        self.series_table.setHorizontalHeaderLabels(["Chọn", "Tập", "Tiêu đề", "Thời lượng"])
        self.series_table.verticalHeader().setVisible(False)
        self.series_table.horizontalHeader().setStretchLastSection(True)
        self.series_table.itemChanged.connect(self._series_selection_changed)
        layout.addWidget(self.series_table)
        if self.project.source_mode == SourceMode.SERIES and self.project.source_urls:
            self._populate_saved_series(self.project.source_urls)
        return page

    def _multi_tab(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(8, 10, 8, 10)
        note = QLabel("Mỗi dòng một link. Video sẽ được tải và nối theo đúng thứ tự bên dưới.")
        note.setObjectName("Subtitle"); layout.addWidget(note)
        initial = self.project.source_urls if self.project.source_mode == SourceMode.MULTI else []
        self.multi_urls = QPlainTextEdit("\n".join(initial))
        self.multi_urls.setPlaceholderText(
            "https://www.douyin.com/video/...\nhttps://www.douyin.com/video/..."
        )
        layout.addWidget(self.multi_urls)
        paste = button("Dán danh sách từ clipboard")
        paste.clicked.connect(lambda: self._paste_to(self.multi_urls))
        layout.addWidget(paste, 0, Qt.AlignmentFlag.AlignRight)
        return page

    @staticmethod
    def _paste_to(target: QWidget) -> None:
        from PySide6.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        if isinstance(target, QLineEdit):
            target.setText(text)
        elif isinstance(target, QPlainTextEdit):
            target.setPlainText(text)

    def _source_mode_changed(self, index: int) -> None:
        mode = str(self.tabs.tabBar().tabData(index) or SourceMode.SINGLE.value)
        if self.project.source_mode != mode:
            self.project.source_mode = mode
            self.changed.emit()

    def _load_series(self) -> None:
        url = self.series_url.text().strip()
        try:
            if detect_video_source(url) is not VideoSource.DOUYIN:
                raise ValueError("Series hiện chỉ hỗ trợ Douyin.")
        except ValueError as exc:
            self.series_status.setText(str(exc)); self.series_status.setStyleSheet("color:#b42318;")
            return
        self.load_series_btn.setEnabled(False)
        self.series_status.setText("Đang tải danh sách tập từ Douyin…")
        self.series_status.setStyleSheet("")
        cookie_file = self.project.__dict__.get("_douyin_cookies_file")
        task = ServiceTaskThread(
            lambda: self.downloader.resolve_series(
                url, (self.project.browser, self.project.browser_profile), cookie_file,
            ),
            self,
        )
        self._tasks.append(task)
        task.succeeded.connect(self._series_loaded)
        task.failed.connect(self._series_failed)
        task.finished.connect(lambda: self._finish_series_task(task))
        task.start()

    def _finish_series_task(self, task: ServiceTaskThread) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        task.deleteLater()
        self.load_series_btn.setEnabled(not self._tasks)

    def _series_loaded(self, value: object) -> None:
        series = value if isinstance(value, SeriesMetadata) else None
        if series is None:
            self._series_failed("Dữ liệu Series trả về không hợp lệ.")
            return
        self._loading_series = True
        try:
            self.series_table.setRowCount(len(series.episodes))
            for row, episode in enumerate(series.episodes):
                selected = QTableWidgetItem()
                selected.setFlags(selected.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                selected.setCheckState(Qt.CheckState.Checked)
                selected.setData(Qt.ItemDataRole.UserRole, episode.webpage_url)
                self.series_table.setItem(row, 0, selected)
                self.series_table.setItem(row, 1, QTableWidgetItem(str(row + 1)))
                self.series_table.setItem(row, 2, QTableWidgetItem(episode.title))
                duration = f"{round(episode.duration_seconds)} giây" if episode.duration_seconds else "—"
                self.series_table.setItem(row, 3, QTableWidgetItem(duration))
        finally:
            self._loading_series = False
        self.series_status.setText(f"{series.title} · {len(series.episodes)} tập")
        self.series_status.setStyleSheet("color:#027a48;")
        self.project.source_mode = SourceMode.SERIES.value
        self.project.source_url = self.series_url.text().strip()
        self.project.source_urls = [episode.webpage_url for episode in series.episodes]
        self.project.source = VideoSource.DOUYIN.value
        self.changed.emit()

    def _series_failed(self, message: str) -> None:
        self.series_status.setText(message)
        self.series_status.setStyleSheet("color:#b42318;")

    def _populate_saved_series(self, urls: list[str]) -> None:
        self._loading_series = True
        try:
            self.series_table.setRowCount(len(urls))
            for row, url in enumerate(urls):
                selected = QTableWidgetItem()
                selected.setFlags(selected.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                selected.setCheckState(Qt.CheckState.Checked)
                selected.setData(Qt.ItemDataRole.UserRole, url)
                self.series_table.setItem(row, 0, selected)
                self.series_table.setItem(row, 1, QTableWidgetItem(str(row + 1)))
                self.series_table.setItem(row, 2, QTableWidgetItem("Tập đã lưu trong project"))
                self.series_table.setItem(row, 3, QTableWidgetItem("—"))
        finally:
            self._loading_series = False
        self.series_status.setText(f"Đã khôi phục {len(urls)} tập từ project")

    def _selected_series_urls(self) -> list[str]:
        output: list[str] = []
        for row in range(self.series_table.rowCount()):
            item = self.series_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                url = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
                if url:
                    output.append(url)
        return output

    def _series_selection_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_series or item.column() != 0:
            return
        self.project.source_urls = self._selected_series_urls()
        self.changed.emit()

    def configure_auth(self) -> None:
        browser = QComboBox(); browser.addItems(["chrome", "edge"]); browser.setCurrentText(self.project.browser)
        profile = QLineEdit(self.project.browser_profile)
        profile.setPlaceholderText("Default, Profile 6 hoặc tên hiển thị")
        cookie_path = QLineEdit(str(self.project.__dict__.get("_douyin_cookies_file", "")))
        cookie_path.setPlaceholderText("Không bắt buộc — cookies.txt hoặc JSON export")
        cookie_row = QWidget(); cookie_layout = QHBoxLayout(cookie_row); cookie_layout.setContentsMargins(0, 0, 0, 0)
        browse = button("Chọn…")
        browse.clicked.connect(lambda: self._choose_cookie_file(cookie_path))
        cookie_layout.addWidget(cookie_path, 1); cookie_layout.addWidget(browse)
        dlg = SettingsDialog(
            "Đăng nhập Douyin", [("Trình duyệt", browser), ("Profile", profile),
                                  ("Cookie thủ công", cookie_row)], self,
            "Ứng dụng tự đổi tên hiển thị của Chrome/Edge sang thư mục profile thật. "
            "File cookie chỉ dùng trong RAM và không được chép vào project/workspace.",
        )
        if dlg.exec():
            self.project.browser = browser.currentText()
            self.project.browser_profile = profile.text().strip() or "Default"
            selected = cookie_path.text().strip()
            from .services.credentials import SecureValueStore
            credential_store = SecureValueStore()
            if selected:
                self.project.__dict__["_douyin_cookies_file"] = selected
                credential_store.save("douyin_cookie_path", selected)
            else:
                self.project.__dict__.pop("_douyin_cookies_file", None)
                credential_store.delete("douyin_cookie_path")
            self._refresh_auth_status(); self.changed.emit()

    def _choose_cookie_file(self, target: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file cookie", "", "Cookie export (*.json *.txt);;Tất cả file (*)"
        )
        if path:
            target.setText(path)

    def _refresh_auth_status(self) -> None:
        cookie_file = self.project.__dict__.get("_douyin_cookies_file")
        if cookie_file:
            self.auth_status.setText(f"Cookie thủ công: {Path(cookie_file).name}")
            return
        from .services.douyin import resolve_browser_profile
        resolved = resolve_browser_profile(self.project.browser, self.project.browser_profile)
        suffix = f" → {resolved}" if resolved != self.project.browser_profile else ""
        self.auth_status.setText(f"{self.project.browser}: {self.project.browser_profile}{suffix}")

    def analyze(self) -> bool:
        mode = str(
            self.tabs.tabBar().tabData(self.tabs.currentIndex()) or SourceMode.SINGLE.value
        )
        if mode == SourceMode.SINGLE.value:
            seed_url = self.url.text().strip()
            urls = [seed_url] if seed_url else []
        elif mode == SourceMode.SERIES.value:
            seed_url = self.series_url.text().strip()
            urls = self._selected_series_urls()
            if not urls:
                self.validation.setText("Hãy tải danh sách Series và chọn ít nhất một tập.")
                self.validation.setStyleSheet("color:#b42318;")
                return False
        else:
            seed_url = ""
            urls = [line.strip() for line in self.multi_urls.toPlainText().splitlines() if line.strip()]
            urls = list(dict.fromkeys(urls))
            if len(urls) < 2:
                self.validation.setText("Chế độ Tải nhiều Videos cần ít nhất hai link.")
                self.validation.setStyleSheet("color:#b42318;")
                return False
        if not urls:
            self.validation.setText("Hãy nhập link video Douyin.")
            self.validation.setStyleSheet("color:#b42318;")
            return False
        try:
            sources = [detect_video_source(url) for url in urls]
            if seed_url:
                detect_video_source(seed_url)
        except ValueError as exc:
            self.validation.setText(str(exc)); self.validation.setStyleSheet("color:#b42318;")
            return False
        if any(source is not VideoSource.DOUYIN for source in sources):
            self.validation.setText("Pipeline thật hiện chỉ hỗ trợ các link Douyin.")
            self.validation.setStyleSheet("color:#b54708;")
            return False
        previous = (
            self.project.source_mode, self.project.source_url,
            tuple(self.project.source_urls), self.project.source,
        )
        self.project.source_mode = mode
        self.project.source_url = seed_url or urls[0]
        self.project.source_urls = [] if mode == SourceMode.SINGLE.value else urls
        self.project.source = VideoSource.DOUYIN.value
        labels = {
            SourceMode.SINGLE.value: "Tải từng tập",
            SourceMode.SERIES.value: "Tải Theo Series",
            SourceMode.MULTI.value: "Tải nhiều Videos và gộp",
        }
        self.project.metadata = {
            "Nền tảng": "Douyin", "Chế độ": labels[mode], "Số video": len(urls),
            "Trạng thái": "Sẵn sàng probe bằng yt-dlp",
        }
        self.validation.setText(f"✓ {len(urls)} link Douyin hợp lệ · {labels[mode]}")
        self.validation.setStyleSheet("color:#027a48;")
        self._show_metadata(self.project.metadata)
        current = (
            self.project.source_mode, self.project.source_url,
            tuple(self.project.source_urls), self.project.source,
        )
        if previous != current:
            self.changed.emit()
        return True

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
    session_changed = Signal(object)
    login_failed = Signal(str)

    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "3. Chuyển audio thành SRT", "Chọn một dịch vụ nhận diện giọng nói.")
        provider = Card("Dịch vụ SRT")
        self.group = QButtonGroup(self)
        for value in SrtProvider:
            radio = QRadioButton(value.value); radio.setChecked(project.srt_provider == value.value); self.group.addButton(radio); provider.layout.addWidget(radio)
        api = button("Cấu hình API"); api.clicked.connect(self.api_dialog); provider.layout.addWidget(api, 0, Qt.AlignmentFlag.AlignRight)
        self.connection_status = QLabel("Chưa kiểm tra phiên CapCut"); self.connection_status.setObjectName("Subtitle")
        provider.layout.addWidget(self.connection_status)
        self.session_changed.connect(self._login_succeeded)
        self.login_failed.connect(self._login_failed)
        self.group.buttonClicked.connect(self._changed); self.content.addWidget(provider)
        preview = Card("Xem trước định dạng kết quả")
        view = QPlainTextEdit("1\n00:00:00,000 --> 00:00:03,400\n你好，欢迎来到今天的视频。\n\n2\n00:00:03,500 --> 00:00:07,000\n我们来看看这个有趣的故事。")
        view.setReadOnly(True); preview.layout.addWidget(view); self.content.addWidget(preview)

    def _changed(self, radio: QRadioButton) -> None:
        self.project.srt_provider = radio.text(); self.changed.emit()

    def api_dialog(self) -> None:
        email = QLineEdit(); email.setPlaceholderText("email@example.com")
        password = QLineEdit(); password.setEchoMode(QLineEdit.EchoMode.Password)
        dlg = SettingsDialog("Đăng nhập CapCut", [("Email", email), ("Password", password)], self,
                             "Password không được lưu. Cookie phiên chỉ lưu trong Windows Credential Manager.")
        if not dlg.exec():
            return
        import threading

        username, secret = email.text(), password.text()
        self.connection_status.setText("Đang đăng nhập CapCut…")

        def login_worker() -> None:
            try:
                from .services.capcut import CapCutAuth, CapCutSessionStore
                session = CapCutAuth().login(username, secret)
                CapCutSessionStore().save(session)
                self.session_changed.emit(session)
            except Exception as exc:  # noqa: BLE001 - thread boundary forwards provider/keyring errors
                self.login_failed.emit(getattr(exc, "user_message", str(exc)))

        threading.Thread(target=login_worker, name="capcut-login", daemon=True).start()

    def _login_succeeded(self, session: object) -> None:
        self.project.__dict__["_capcut_session"] = session
        self.connection_status.setText("✓ Đã kết nối CapCut")

    def _login_failed(self, message: str) -> None:
        self.connection_status.setText("Đăng nhập CapCut thất bại")
        QMessageBox.critical(self, "Không thể đăng nhập", message)


class TranslateStep(BaseStep):
    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "4. Dịch SRT sang tiếng Việt", "Chọn translator và giọng điệu phù hợp với nội dung.")
        config = Card("Translator & ngữ cảnh"); form = QFormLayout()
        self.provider = QComboBox(); self.provider.addItems([v.value for v in TranslationProvider]); self.provider.setCurrentText(project.translation_provider)
        self.context = QComboBox(); self.context.addItems([v.value for v in TranslationContext]); self.context.setCurrentText(project.translation_context)
        self.custom = QPlainTextEdit(project.custom_context); self.custom.setPlaceholderText("Mô tả phong cách dịch mong muốn..."); self.custom.setMaximumHeight(80)
        form.addRow("Dịch bằng", self.provider); form.addRow("Giọng điệu", self.context); form.addRow("Context riêng", self.custom); config.layout.addLayout(form)
        advanced = button("Cấu hình translator"); advanced.clicked.connect(self.configure); config.layout.addWidget(advanced, 0, Qt.AlignmentFlag.AlignRight)
        self.retry_status = QLabel(); self.retry_status.setObjectName("Subtitle")
        self.retry_status.setWordWrap(True); config.layout.addWidget(self.retry_status)
        self.content.addWidget(config); self.context.currentTextChanged.connect(self._context_changed); self._context_changed(self.context.currentText())
        self.provider.currentTextChanged.connect(self._save); self.custom.textChanged.connect(self._save)
        preview = Card("Đối chiếu bản dịch")
        table = QTableWidget(); set_table(table, ["Thời gian", "Bản gốc", "Tiếng Việt"], [["00:00–00:03", "你好，欢迎来到今天的视频。", "Xin chào, chào mừng bạn đến với video hôm nay!"], ["00:03–00:07", "我们来看看这个有趣的故事。", "Cùng xem câu chuyện thú vị này nhé!"]]); preview.layout.addWidget(table); self.content.addWidget(preview)

    def set_partial_failure(self, value: dict | None) -> None:
        if not value:
            self.retry_status.clear()
            return
        failed = len(value.get("failed_item_ids", []))
        self.retry_status.setText(
            f"Đã dịch {value.get('completed', 0)}/{value.get('total', 0)} cue · "
            f"{failed} cue thất bại. Bấm Thử lại để chỉ xử lý phần lỗi."
        )

    def _context_changed(self, text: str) -> None:
        self.custom.setVisible(text == TranslationContext.CUSTOM.value); self._save()

    def _save(self) -> None:
        self.project.translation_provider = self.provider.currentText(); self.project.translation_context = self.context.currentText(); self.project.custom_context = self.custom.toPlainText(); self.changed.emit()

    def configure(self) -> None:
        model = QComboBox(); model.addItems([self.project.gemini_model, "gemini-3.6-flash", "gemini-2.5-flash"]); model.setCurrentText(self.project.gemini_model)
        key = QLineEdit(); key.setEchoMode(QLineEdit.EchoMode.Password)
        batch = QSpinBox(); batch.setRange(10, 200); batch.setValue(self.project.translation_batch_size)
        concurrency = QSpinBox(); concurrency.setRange(1, 8); concurrency.setValue(self.project.translation_concurrency)
        dlg = SettingsDialog("Cấu hình Gemini", [("Model", model), ("API key override", key),
                             ("Batch size", batch), ("Concurrency", concurrency)], self,
                             "Để trống để dùng GEMINI_API_KEY hoặc key đã lưu. Key nhập mới được "
                             "lưu trong Windows Credential Manager, không ghi vào project/log.")
        if dlg.exec():
            self.project.gemini_model = model.currentText()
            self.project.translation_batch_size = batch.value()
            self.project.translation_concurrency = concurrency.value()
            if key.text():
                self.project.__dict__["_gemini_api_key"] = key.text()
                from .services.credentials import SecureValueStore
                SecureValueStore().save("gemini_api_key", key.text())
            self.changed.emit()


class TtsStep(BaseStep):
    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "5. Tạo voice tiếng Việt", "Dùng SRT tiếng Việt để tổng hợp giọng đọc.")
        self._service_provider = lambda: None
        card = Card("Text-to-Speech")
        self.provider = QComboBox(); self.provider.addItems([v.value for v in TtsProvider]); self.provider.setCurrentText(project.tts_provider)
        card.layout.addWidget(self.provider); config = button("Cấu hình giọng đọc"); config.clicked.connect(self.configure); card.layout.addWidget(config, 0, Qt.AlignmentFlag.AlignRight)
        self.retry_status = QLabel(); self.retry_status.setObjectName("Subtitle")
        self.retry_status.setWordWrap(True); card.layout.addWidget(self.retry_status)
        self.provider.currentTextChanged.connect(self._save); self.content.addWidget(card)
        lines = Card("Đoạn thoại")
        table = QTableWidget(); set_table(table, ["#", "Nội dung", "Thời lượng", "Trạng thái"], [["1", "Xin chào, chào mừng bạn đến với video hôm nay!", "3,4s", "Chờ"], ["2", "Cùng xem câu chuyện thú vị này nhé!", "3,5s", "Chờ"]]); lines.layout.addWidget(table); self.content.addWidget(lines)

    def set_partial_failure(self, value: dict | None) -> None:
        if not value:
            self.retry_status.clear()
            return
        failed = len(value.get("failed_item_ids", []))
        self.retry_status.setText(
            f"Đã tạo {value.get('completed', 0)}/{value.get('total', 0)} audio · "
            f"{failed} audio thất bại. Bấm Thử lại để chỉ xử lý phần lỗi."
        )

    def _save(self) -> None: self.project.tts_provider = self.provider.currentText(); self.changed.emit()

    def set_service_provider(self, provider: object) -> None:
        self._service_provider = provider

    def configure(self) -> None:
        if self.provider.currentText() == TtsProvider.CAPCUT.value:
            service = self._service_provider()  # type: ignore[operator]
            if not isinstance(service, CapCutTtsService):
                QMessageBox.warning(
                    self, "Chưa kết nối CapCut",
                    "Hãy đăng nhập CapCut ở bước 3 trước khi tải danh sách giọng.",
                )
                return
            dialog = VoiceCatalogDialog(
                service, self.project.capcut_voice or str(self.project.tts_settings.get("voice", "")),
                self.project.tts_settings, self,
            )
            if dialog.exec():
                selected = dialog.selected_voice()
                if not selected:
                    return
                self.project.capcut_voice = selected.speaker
                self.project.tts_settings = {
                    "voice": selected.speaker,
                    "speed": dialog.speed.value() / 100,
                    "pitch": dialog.pitch.value(),
                    "volume": dialog.volume.value(),
                    "concurrency": dialog.concurrency.value(),
                }
                self.changed.emit()
            return
        voice = QComboBox(); voice.addItems(["vi-VN-Neural2-A", "vi-VN-Neural2-D", "CapCut Nữ trẻ"]); voice.setCurrentText(self.project.tts_settings["voice"])
        speed = QSpinBox(); speed.setRange(50, 200); speed.setSuffix("%"); speed.setValue(round(self.project.tts_settings["speed"] * 100))
        pitch = QSpinBox(); pitch.setRange(-12, 12); pitch.setValue(self.project.tts_settings["pitch"])
        volume = QSpinBox(); volume.setRange(0, 150); volume.setSuffix("%"); volume.setValue(self.project.tts_settings["volume"])
        sample = button("Kiểm tra voice"); sample.clicked.connect(lambda: QMessageBox.information(self, "Voice", "Voice sẽ được kiểm tra với CapCut khi chạy bước TTS."))
        dlg = SettingsDialog("Cấu hình giọng đọc", [("Giọng", voice), ("Tốc độ", speed), ("Pitch", pitch), ("Âm lượng", volume), ("", sample)], self)
        if dlg.exec():
            self.project.tts_settings = {
                "voice": voice.currentText(), "speed": speed.value()/100,
                "pitch": pitch.value(), "volume": volume.value(),
                "concurrency": int(self.project.tts_settings.get("concurrency", 25)),
            }; self.changed.emit()


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
        card = Card("Quyết Bồ Layer Studio", "Preview · Media assets · Properties · Layer")
        features = QLabel("Tốc độ & Pitch  •  Zoom & Lật  •  Màu sắc  •  Logo  •  Text cố định phía trên\nPhụ đề  •  Chữ chạy  •  Undo/Redo  •  Kéo-thả layer")
        features.setWordWrap(True); card.layout.addWidget(features)
        self.open_btn = button("Mở Layer Studio", True); self.open_btn.clicked.connect(self.open_studio); card.layout.addWidget(self.open_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.summary = QLabel(); self.summary.setObjectName("Subtitle"); card.layout.addWidget(self.summary); self.content.addWidget(card); self._refresh()
        self._video_provider = None
        self._edit_guard = None

    def set_video_provider(self, provider: object) -> None:
        self._video_provider = provider

    def set_edit_guard(self, guard: object) -> None:
        self._edit_guard = guard

    def open_studio(self) -> None:
        if callable(self._edit_guard) and not self._edit_guard():
            QMessageBox.warning(
                self, "Pipeline đang chạy",
                "Hãy dừng hoặc chờ tác vụ hiện tại hoàn thành trước khi đổi cấu hình Studio.",
            )
            return
        video_path = self._video_provider() if callable(self._video_provider) else None
        dlg = StudioDialog(self.project.studio_settings, self, video_path=video_path)
        dlg.settings_applied.connect(self._apply); dlg.exec()

    def _apply(self, settings: dict) -> None:
        self.project.studio_settings = settings; self._refresh(); self.changed.emit()

    def _refresh(self) -> None:
        s = self.project.studio_settings; self.summary.setText(f"Đã cấu hình: {len(s.get('layers', []))} layer · tốc độ {s.get('speed', 1)}× · zoom {s.get('zoom', 100)}%")


class ExportStep(BaseStep):
    def __init__(self, project: ProjectModel) -> None:
        super().__init__(project, "8. Xuất video", "Cấu hình file đầu ra và kiểm tra video render.")
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
            QMessageBox.information(self, "Output", f"Thư mục chưa được tạo:\n{folder}")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))


class VietSubPage(QWidget):
    dirty_changed = Signal(bool)
    request_new = Signal(); request_open = Signal(); request_save = Signal()

    def __init__(
        self, project: ProjectModel, settings_store: LastUsedSettingsStore | None = None,
    ) -> None:
        super().__init__(); self.setObjectName("AppRoot"); self.project = project; self.current_step = 0
        self.settings_store = settings_store or LastUsedSettingsStore()
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
        self.run_all_btn = button("▶ Chạy tất cả các bước", True); self.pause_btn = button("Tạm dừng"); self.resume_btn = button("Tiếp tục"); self.stop_btn = button("Dừng"); self.reset_all_btn = button("Đặt lại")
        self.force_btn = button("Bỏ qua cache"); self.workspace_btn = button("Mở workspace"); self.clear_cache_btn = button("Dọn cache")
        for b in (self.run_all_btn, self.pause_btn, self.resume_btn, self.stop_btn, self.reset_all_btn,
                  self.force_btn, self.workspace_btn, self.clear_cache_btn): row.addWidget(b)
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
        self.force_btn.clicked.connect(self._run_force)
        self.workspace_btn.clicked.connect(self._open_workspace)
        self.clear_cache_btn.clicked.connect(self._clear_cache)

    def set_project(self, project: ProjectModel) -> None:
        self.project = project; self.project_label.setText(f"{project.name}  •  Chưa lưu")
        while self.stack.count(): self.stack.removeWidget(self.stack.widget(0))
        for row in self.step_rows: row.deleteLater()
        self.step_rows.clear()
        dependencies = PipelineDependencies()
        self.pages = [SourceStep(project, dependencies.downloader), AudioSplitStep(project), SrtStep(project), TranslateStep(project), TtsStep(project), MixStep(project), StudioStep(project), ExportStep(project)]
        for i, page in enumerate(self.pages):
            self.stack.addWidget(page)
            row = StepRow(i, STEP_TITLES[i], project.step_statuses[i]); row.clicked.connect(self.select_step); self.step_rows.append(row); self.step_panel.layout.insertWidget(i + 1, row)
        self.pages[2].session_changed.connect(self._capcut_session_changed)
        pipeline = VietSubPipeline(project, dependencies=dependencies)
        self._configure_services(pipeline)
        self.controller = WorkflowController(project, pipeline); self._connect_controller(); self.select_step(0)
        for index, page in enumerate(self.pages):
            page.changed.connect(lambda index=index: self._config_changed(index))
        self.pages[4].set_service_provider(lambda: pipeline.services.tts)
        self.pages[6].set_video_provider(pipeline.studio_preview_path)
        self.pages[6].set_edit_guard(
            lambda: self.controller.active_step is None and self.controller.thread is None
        )
        self.log.append_log("INFO", "Project đã sẵn sàng với pipeline xử lý thật.")

    def _config_changed(self, step: int) -> None:
        try:
            self.settings_store.save(self.project)
        except OSError as exc:
            self.log.append_log("WARNING", f"Không lưu được cấu hình gần nhất: {exc}")
        if isinstance(self.controller.service, VietSubPipeline):
            self.controller.service.cache.invalidate(step)
        self.controller.reset_step(step)
        self.log.append_log(
            "INFO", f"Cấu hình bước {step + 1} đã đổi; bước này và downstream cần chạy lại."
        )

    def _capcut_session_changed(self, session: object) -> None:
        self.project.__dict__["_capcut_session"] = session
        self._configure_services(self.controller.service)

    def _configure_services(self, pipeline: object) -> None:
        if not isinstance(pipeline, VietSubPipeline):
            return
        from .services.gemini import GeminiClient, GeminiTranslationService
        key = self.project.__dict__.get("_gemini_api_key")
        if not key:
            try:
                from .services.credentials import SecureValueStore
                key = SecureValueStore().load("gemini_api_key")
            except Exception:  # noqa: BLE001 - environment variable remains a valid fallback
                key = ""
        gemini = GeminiClient(key)
        pipeline.services.translator = GeminiTranslationService(gemini)
        session = self.project.__dict__.get("_capcut_session")
        if session is None:
            try:
                from .services.capcut import CapCutSessionStore
                session = CapCutSessionStore().load()
            except Exception:  # noqa: BLE001 - unavailable OS credential backend is non-fatal
                session = None
            if session is not None:
                self.project.__dict__["_capcut_session"] = session
        if session is not None:
            from .services.capcut import (
                CapCutCaptionService,
                CapCutEditClient,
                CapCutSessionStore,
                CapCutTtsService,
                VodUploader,
            )
            client = CapCutEditClient(session, session_store=CapCutSessionStore())
            pipeline.services.captions = CapCutCaptionService(client, VodUploader(client))
            pipeline.services.tts = CapCutTtsService(client)

    def _connect_controller(self) -> None:
        self.controller.step_status_changed.connect(self._status_changed); self.controller.step_progress_changed.connect(self._step_progress_changed)
        self.controller.overall_progress_changed.connect(self.overall.setValue); self.controller.log_emitted.connect(self.log.append_log)
        self.controller.dirty_changed.connect(self.dirty_changed); self.controller.workflow_completed.connect(self._completed)
        self.controller.partial_failure_changed.connect(self._partial_failure_changed)

    def select_step(self, index: int) -> None:
        self.current_step = index; self.stack.setCurrentIndex(index)
        for i, row in enumerate(self.step_rows): row.nav.setChecked(i == index)
        self.step_badge.set_status(self.project.step_statuses[index]); self.step_progress.setValue(100 if self.project.step_statuses[index] == "completed" else 0)
        allowed, reason = self.controller.can_run(index); self.run_step_btn.setEnabled(allowed); self.run_step_btn.setToolTip(reason)
        self._partial_failure_changed(index, self.controller.partial_failure(index))

    def _run_step(self) -> None:
        if self.current_step == 0 and not self.pages[0].analyze(): return
        if self.current_step == 3 and self.project.translation_context == TranslationContext.CUSTOM.value and not self.project.custom_context.strip():
            QMessageBox.warning(self, "Thiếu context", "Vui lòng nhập context dịch tùy chỉnh."); return
        if self.current_step == 7 and not self.pages[7].validate(): return
        self._configure_services(self.controller.service)
        self.controller.run_step(self.current_step)

    def _run_all(self) -> None:
        if not self.pages[0].analyze(): self.select_step(0); return
        if self.project.translation_context == TranslationContext.CUSTOM.value and not self.project.custom_context.strip():
            self.select_step(3)
            QMessageBox.warning(self, "Thiếu context", "Vui lòng nhập context dịch tùy chỉnh.")
            return
        if not self.pages[7].validate():
            self.select_step(7)
            return
        self._configure_services(self.controller.service)
        self.controller.run_all()

    def _run_force(self) -> None:
        if not isinstance(self.controller.service, VietSubPipeline):
            return
        self.controller.service.force = True
        self._run_all()

    def _open_workspace(self) -> None:
        if not isinstance(self.controller.service, VietSubPipeline):
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.service.workspace)))

    def _clear_cache(self) -> None:
        if not isinstance(self.controller.service, VietSubPipeline) or self.controller.active_step is not None:
            return
        answer = QMessageBox.question(self, "Dọn cache", "Xóa toàn bộ artifact trung gian của project?")
        if answer == QMessageBox.StandardButton.Yes:
            cache = self.controller.service.cache
            cache.clear_files()
            self.controller.reset()
            self.log.append_log("INFO", "Đã dọn artifact trung gian trong workspace.")

    def _status_changed(self, index: int, status: str) -> None:
        self.step_rows[index].badge.set_status(status)
        if index == self.current_step: self.step_badge.set_status(status)
        allowed, reason = self.controller.can_run(self.current_step); self.run_step_btn.setEnabled(allowed); self.run_step_btn.setToolTip(reason)

    def _partial_failure_changed(self, index: int, value: object) -> None:
        partial = value if isinstance(value, dict) else None
        page = self.pages[index]
        setter = getattr(page, "set_partial_failure", None)
        if setter:
            setter(partial)
        if index == self.current_step:
            failed = len(partial.get("failed_item_ids", [])) if partial else 0
            self.retry_btn.setText(f"Thử lại {failed} phần lỗi" if failed else "Chạy lại")

    def _step_progress_changed(self, index: int, value: int) -> None:
        if index == self.current_step: self.step_progress.setValue(value)

    def _completed(self) -> None:
        if isinstance(self.controller.service, VietSubPipeline):
            self.controller.service.force = False
        path = self.project.output.get("video", "")
        self.pages[7].output.setText(f"✓ Hoàn thành: {path}")
        self.pages[7].open_folder.setEnabled(True)
        QMessageBox.information(self, "Hoàn thành", f"Pipeline đã hoàn thành.\n\n{path}")
