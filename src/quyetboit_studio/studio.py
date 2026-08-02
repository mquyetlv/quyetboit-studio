from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .core.studio import new_studio_layer, normalize_studio_settings
from .widgets import Card

ASSET_KINDS = {
    "Logo thương hiệu": "logo",
    "Overlay ánh sáng": "overlay",
    "Text cố định phía trên": "intro_text",
    "Chữ chạy quanh video": "scroll_text",
}


class PreviewCanvas(QFrame):
    asset_dropped = Signal(str)

    def __init__(self, video_path: str | Path | None = None) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumSize(420, 300)
        self.setStyleSheet("background:#101828;border-radius:12px;border:2px dashed #475467;")
        layout = QVBoxLayout(self)
        self.video = QVideoWidget()
        self.video.setMinimumSize(400, 260)
        layout.addWidget(self.video, 1)
        controls = QHBoxLayout()
        self.play_button = QPushButton("▶ Phát preview")
        self.play_button.clicked.connect(self.toggle_playback)
        self.status = QLabel("Preview video sau bước 6; hiệu ứng được render khi chạy bước 7.")
        self.status.setObjectName("Subtitle")
        controls.addWidget(self.play_button)
        controls.addWidget(self.status, 1)
        layout.addLayout(controls)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.5)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.playbackStateChanged.connect(self._playback_changed)
        path = Path(video_path).resolve() if video_path else None
        if path and path.is_file():
            self.player.setSource(QUrl.fromLocalFile(str(path)))
        else:
            self.play_button.setEnabled(False)
            self.status.setText("Hãy hoàn thành bước 6 để phát preview video đã ghép voice.")

    def toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _playback_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText(
            "⏸ Tạm dừng" if state == QMediaPlayer.PlaybackState.PlayingState else "▶ Phát preview"
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasText() or event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        text = event.mimeData().text() or ""
        self.asset_dropped.emit(text.splitlines()[0])
        event.acceptProposedAction()


class StudioDialog(QDialog):
    settings_applied = Signal(dict)

    def __init__(
        self, settings: dict, parent: QWidget | None = None,
        *, video_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quyết Bồ Video Studio")
        self.resize(1380, 860)
        self.setMinimumSize(1080, 700)
        self.original = normalize_studio_settings(settings)
        self.state = deepcopy(self.original)
        self.history: list[dict] = []
        self.future: list[dict] = []
        self.loading = False
        self._build_ui(video_path)
        self._restore(self.state)

    def _build_ui(self, video_path: str | Path | None) -> None:
        root = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        title = QLabel("Video Studio")
        title.setObjectName("Title")
        toolbar.addWidget(title)
        toolbar.addStretch()
        for text, slot in (("Hoàn tác", self.undo), ("Làm lại", self.redo), ("Đặt lại", self.reset_form)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        root.addLayout(toolbar)

        split = QSplitter(Qt.Orientation.Horizontal)
        assets = Card("Tài nguyên", "Kéo hoặc nhấp đúp để thêm layer")
        self.assets = QListWidget()
        self.assets.addItems(ASSET_KINDS)
        self.assets.setDragEnabled(True)
        self.assets.itemDoubleClicked.connect(lambda item: self.add_layer(item.text()))
        assets.layout.addWidget(self.assets)
        self.remove_button = QPushButton("Xóa layer đã chọn")
        self.remove_button.clicked.connect(self.remove_layer)
        assets.layout.addWidget(self.remove_button)
        assets.setMinimumWidth(210)
        split.addWidget(assets)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        self.preview = PreviewCanvas(video_path)
        self.preview.asset_dropped.connect(self.add_layer)
        center_layout.addWidget(self.preview, 3)
        timeline = Card("Layer", "Kéo-thả để đổi thứ tự; bỏ dấu chọn để tắt layer")
        self.layers = QListWidget()
        self.layers.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.layers.currentItemChanged.connect(self._layer_selected)
        self.layers.itemChanged.connect(self._layer_checked)
        timeline.layout.addWidget(self.layers)
        center_layout.addWidget(timeline, 2)
        split.addWidget(center)

        props = Card("Thuộc tính", "Cấu hình video và layer đang chọn")
        form = QFormLayout()
        self.speed = QDoubleSpinBox(); self.speed.setRange(.25, 4); self.speed.setSingleStep(.05); self.speed.setSuffix("×")
        self.pitch = QSpinBox(); self.pitch.setRange(-12, 12); self.pitch.setSuffix(" semitone")
        self.zoom = QSlider(Qt.Orientation.Horizontal); self.zoom.setRange(100, 200)
        self.color = QSlider(Qt.Orientation.Horizontal); self.color.setRange(0, 200)
        self.flip_h = QCheckBox("Lật ngang")
        self.flip_v = QCheckBox("Lật dọc")
        form.addRow("Tốc độ", self.speed); form.addRow("Pitch", self.pitch)
        form.addRow("Zoom", self.zoom); form.addRow("Bão hòa màu", self.color)
        form.addRow(self.flip_h); form.addRow(self.flip_v)
        props.layout.addLayout(form)
        self.selected_label = QLabel("Layer: Chưa chọn")
        self.selected_label.setObjectName("Title")
        props.layout.addWidget(self.selected_label)
        layer_form = QFormLayout()
        self.asset_path = QLineEdit(); self.asset_path.setReadOnly(True)
        self.browse_button = QPushButton("Chọn ảnh…"); self.browse_button.clicked.connect(self.choose_asset)
        path_row = QHBoxLayout(); path_row.addWidget(self.asset_path, 1); path_row.addWidget(self.browse_button)
        self.text = QLineEdit()
        self.position = QComboBox(); self.position.addItems([
            "center", "top", "bottom", "top-left", "top-right", "bottom-left", "bottom-right",
        ])
        self.full_duration = QCheckBox("Hiển thị xuyên suốt video")
        self.layer_start = QDoubleSpinBox(); self.layer_start.setRange(0, 36000); self.layer_start.setSuffix(" giây")
        self.layer_duration = QDoubleSpinBox(); self.layer_duration.setRange(.1, 36000); self.layer_duration.setSuffix(" giây")
        self.margin = QSpinBox(); self.margin.setRange(0, 1000); self.margin.setSuffix(" px")
        self.width_percent = QSpinBox(); self.width_percent.setRange(5, 100); self.width_percent.setSuffix("%")
        self.layer_height = QSpinBox(); self.layer_height.setRange(0, 2160); self.layer_height.setSuffix(" px")
        self.opacity = QSpinBox(); self.opacity.setRange(0, 100); self.opacity.setSuffix("%")
        self.font_size = QSpinBox(); self.font_size.setRange(12, 200); self.font_size.setSuffix(" px")
        self.font_color = QLineEdit()
        self.background_color = QLineEdit()
        self.background_opacity = QSpinBox(); self.background_opacity.setRange(0, 100); self.background_opacity.setSuffix("%")
        layer_form.addRow("File ảnh", path_row); layer_form.addRow("Nội dung", self.text)
        layer_form.addRow("Vị trí", self.position); layer_form.addRow(self.full_duration)
        layer_form.addRow("Bắt đầu", self.layer_start); layer_form.addRow("Thời lượng", self.layer_duration)
        layer_form.addRow("Margin so với lề", self.margin)
        layer_form.addRow("Width theo video", self.width_percent)
        layer_form.addRow("Height", self.layer_height)
        layer_form.addRow("Độ trong ảnh", self.opacity); layer_form.addRow("Cỡ chữ", self.font_size)
        layer_form.addRow("Màu chữ", self.font_color); layer_form.addRow("Màu nền", self.background_color)
        layer_form.addRow("Độ trong nền", self.background_opacity)
        props.layout.addLayout(layer_form)
        props.layout.addStretch()
        split.addWidget(props)
        split.setSizes([220, 800, 320])
        root.addWidget(split)

        self.speed.valueChanged.connect(lambda value: self._change_global("speed", value))
        self.pitch.valueChanged.connect(lambda value: self._change_global("pitch", value))
        self.zoom.valueChanged.connect(lambda value: self._change_global("zoom", value))
        self.color.valueChanged.connect(lambda value: self._change_global("color", value))
        self.flip_h.toggled.connect(lambda value: self._change_global("flip_horizontal", value))
        self.flip_v.toggled.connect(lambda value: self._change_global("flip_vertical", value))
        self.text.editingFinished.connect(lambda: self._change_layer("text", self.text.text()))
        self.position.currentTextChanged.connect(lambda value: self._change_layer("position", value))
        self.full_duration.toggled.connect(self._full_duration_changed)
        self.layer_start.valueChanged.connect(lambda value: self._change_layer("start", value))
        self.layer_duration.valueChanged.connect(lambda value: self._change_layer("duration", value))
        self.margin.valueChanged.connect(lambda value: self._change_layer("margin", value))
        self.width_percent.valueChanged.connect(
            lambda value: self._change_layer("width_percent", value)
        )
        self.layer_height.valueChanged.connect(lambda value: self._change_layer("height", value))
        self.opacity.valueChanged.connect(lambda value: self._change_layer("opacity", value))
        self.font_size.valueChanged.connect(lambda value: self._change_layer("font_size", value))
        self.font_color.editingFinished.connect(
            lambda: self._change_layer("font_color", self.font_color.text())
        )
        self.background_color.editingFinished.connect(
            lambda: self._change_layer("background_color", self.background_color.text())
        )
        self.background_opacity.valueChanged.connect(
            lambda value: self._change_layer("background_opacity", value)
        )

        footer = QHBoxLayout()
        hint = QLabel("Preview phát video bước 6; bấm Áp dụng rồi chạy bước 7 để render hiệu ứng.")
        hint.setObjectName("Subtitle")
        cancel = QPushButton("Hủy"); cancel.clicked.connect(self.reject)
        apply_button = QPushButton("Áp dụng"); apply_button.setObjectName("Primary"); apply_button.clicked.connect(self.apply)
        footer.addWidget(hint); footer.addStretch(); footer.addWidget(cancel); footer.addWidget(apply_button)
        root.addLayout(footer)

    def _remember(self) -> None:
        self._sync_layer_order()
        self.history.append(deepcopy(self.state))
        self.history = self.history[-100:]
        self.future.clear()

    def _sync_layer_order(self) -> None:
        if not hasattr(self, "layers"):
            return
        ordered = []
        for index in range(self.layers.count()):
            layer_id = self.layers.item(index).data(Qt.ItemDataRole.UserRole)
            layer = next((item for item in self.state["layers"] if item["id"] == layer_id), None)
            if layer:
                ordered.append(layer)
        if len(ordered) == len(self.state["layers"]):
            self.state["layers"] = ordered

    def _selected(self) -> dict | None:
        item = self.layers.currentItem()
        if not item:
            return None
        layer_id = item.data(Qt.ItemDataRole.UserRole)
        return next((layer for layer in self.state["layers"] if layer["id"] == layer_id), None)

    def _change_global(self, key: str, value: object) -> None:
        if self.loading or self.state.get(key) == value:
            return
        self._remember(); self.state[key] = value

    def _change_layer(self, key: str, value: object) -> None:
        layer = self._selected()
        if self.loading or not layer or layer.get(key) == value:
            return
        self._remember(); layer[key] = value

    def _full_duration_changed(self, value: bool) -> None:
        self._change_layer("full_duration", value)
        self.layer_start.setEnabled(not value)
        self.layer_duration.setEnabled(not value)

    def current_settings(self) -> dict:
        self._sync_layer_order()
        return normalize_studio_settings(self.state)

    def add_layer(self, name: str) -> None:
        kind = ASSET_KINDS.get(name)
        if not kind:
            return
        values = {}
        if kind in {"logo", "overlay"}:
            path, _ = QFileDialog.getOpenFileName(
                self, "Chọn ảnh", "", "Ảnh (*.png *.jpg *.jpeg *.webp *.bmp)"
            )
            if not path:
                return
            values["path"] = path
        else:
            text, accepted = QInputDialog.getText(self, "Nội dung text", "Nhập nội dung:")
            if not accepted or not text.strip():
                return
            values["text"] = text.strip()
        self._remember()
        layer = new_studio_layer(kind, name=name, **values)
        self.state["layers"].append(layer)
        self._reload_layers(layer["id"])

    def remove_layer(self) -> None:
        layer = self._selected()
        if not layer or layer["kind"] in {"video", "voice", "subtitle"}:
            QMessageBox.information(self, "Layer bắt buộc", "Video, Voice và Subtitle không thể xóa; có thể bỏ dấu chọn để tắt.")
            return
        self._remember()
        self.state["layers"] = [item for item in self.state["layers"] if item["id"] != layer["id"]]
        self._reload_layers()

    def choose_asset(self) -> None:
        layer = self._selected()
        if not layer or layer["kind"] not in {"logo", "overlay"}:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn ảnh", str(layer.get("path", "")), "Ảnh (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if path:
            self._change_layer("path", path)
            self.asset_path.setText(path)

    def _layer_checked(self, item: QListWidgetItem) -> None:
        if self.loading:
            return
        layer_id = item.data(Qt.ItemDataRole.UserRole)
        layer = next((entry for entry in self.state["layers"] if entry["id"] == layer_id), None)
        enabled = item.checkState() == Qt.CheckState.Checked
        if layer and layer.get("enabled", True) != enabled:
            self._remember(); layer["enabled"] = enabled

    def _layer_selected(self, current: QListWidgetItem | None) -> None:
        layer = self._selected() if current else None
        self.loading = True
        try:
            kind = str(layer.get("kind", "")) if layer else ""
            self.selected_label.setText(f"Layer: {layer.get('name', 'Chưa chọn') if layer else 'Chưa chọn'}")
            is_image = kind in {"logo", "overlay"}
            is_text = kind in {"intro_text", "scroll_text", "subtitle"}
            has_text_input = kind in {"intro_text", "scroll_text"}
            styled = is_image or is_text
            full_duration = bool(layer.get("full_duration", True)) if layer else True
            self.asset_path.setEnabled(is_image); self.browse_button.setEnabled(is_image)
            self.text.setEnabled(has_text_input); self.position.setEnabled(styled)
            self.full_duration.setEnabled(styled)
            self.layer_start.setEnabled(styled and not full_duration)
            self.layer_duration.setEnabled(styled and not full_duration)
            self.margin.setEnabled(styled); self.width_percent.setEnabled(styled)
            self.layer_height.setEnabled(styled); self.opacity.setEnabled(is_image)
            self.font_size.setEnabled(is_text); self.font_color.setEnabled(is_text)
            self.background_color.setEnabled(styled); self.background_opacity.setEnabled(styled)
            self.asset_path.setText(str(layer.get("path", "")) if layer else "")
            self.text.setText(str(layer.get("text", "")) if layer else "")
            self.position.setCurrentText(str(layer.get("position", "center")) if layer else "center")
            self.full_duration.setChecked(full_duration)
            self.layer_start.setValue(float(layer.get("start", 0)) if layer else 0)
            self.layer_duration.setValue(float(layer.get("duration", 5)) if layer else 5)
            self.margin.setValue(int(layer.get("margin", 24)) if layer else 24)
            self.width_percent.setValue(int(layer.get("width_percent", 90)) if layer else 90)
            self.layer_height.setValue(int(layer.get("height", 64)) if layer else 64)
            self.opacity.setValue(int(layer.get("opacity", 100)) if layer else 100)
            self.font_size.setValue(int(layer.get("font_size", 48)) if layer else 48)
            self.font_color.setText(str(layer.get("font_color", "white")) if layer else "white")
            self.background_color.setText(
                str(layer.get("background_color", "#000000")) if layer else "#000000"
            )
            self.background_opacity.setValue(
                int(layer.get("background_opacity", 60)) if layer else 60
            )
        finally:
            self.loading = False

    def _reload_layers(self, selected_id: str = "") -> None:
        self.loading = True
        self.layers.clear()
        selected_item = None
        for layer in self.state["layers"]:
            item = QListWidgetItem(str(layer.get("name") or layer["kind"]))
            item.setData(Qt.ItemDataRole.UserRole, layer["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked if layer.get("enabled", True) else Qt.CheckState.Unchecked)
            self.layers.addItem(item)
            if layer["id"] == selected_id:
                selected_item = item
        self.loading = False
        self.layers.setCurrentItem(selected_item or (self.layers.item(0) if self.layers.count() else None))

    def _restore(self, settings: dict) -> None:
        self.state = normalize_studio_settings(settings)
        self.loading = True
        self.speed.setValue(self.state["speed"]); self.pitch.setValue(self.state["pitch"])
        self.zoom.setValue(self.state["zoom"]); self.color.setValue(self.state["color"])
        self.flip_h.setChecked(self.state["flip_horizontal"]); self.flip_v.setChecked(self.state["flip_vertical"])
        self.loading = False
        self._reload_layers()

    def undo(self) -> None:
        if self.history:
            self.future.append(deepcopy(self.state)); self._restore(self.history.pop())

    def redo(self) -> None:
        if self.future:
            self.history.append(deepcopy(self.state)); self._restore(self.future.pop())

    def reset_form(self) -> None:
        self._remember(); self._restore(self.original)

    def apply(self) -> None:
        settings = self.current_settings()
        missing = [
            layer.get("name", "ảnh") for layer in settings["layers"]
            if layer.get("enabled", True) and layer.get("kind") in {"logo", "overlay"}
            and not Path(str(layer.get("path", ""))).expanduser().is_file()
        ]
        if missing:
            QMessageBox.warning(self, "Thiếu file layer", f"Không tìm thấy file: {', '.join(missing)}")
            return
        self.preview.player.stop()
        self.settings_applied.emit(settings)
        self.accept()
