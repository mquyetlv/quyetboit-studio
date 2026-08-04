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
    "Text cố định": "intro_text",
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
        self.setWindowTitle("Quyết Bồ Layer Studio")
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
        title = QLabel("Layer Studio")
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
        self._layer_rows: dict[str, tuple[QWidget, QWidget]] = {}
        self.asset_path = QLineEdit(); self.asset_path.setReadOnly(True)
        self.browse_button = QPushButton("Chọn ảnh…"); self.browse_button.clicked.connect(self.choose_asset)
        path_widget = QWidget(); path_row = QHBoxLayout(path_widget); path_row.setContentsMargins(0, 0, 0, 0)
        path_row.addWidget(self.asset_path, 1); path_row.addWidget(self.browse_button)
        self.layer_enabled = QCheckBox("Hiển thị layer")
        self.text = QLineEdit()
        self.position = QComboBox()
        self.margin = QSpinBox(); self.margin.setRange(0, 1000); self.margin.setSuffix(" px")
        self.margin_x = QSpinBox(); self.margin_x.setRange(0, 1000); self.margin_x.setSuffix(" px")
        self.margin_y = QSpinBox(); self.margin_y.setRange(0, 1000); self.margin_y.setSuffix(" px")
        self.layer_height = QSpinBox(); self.layer_height.setRange(1, 2160); self.layer_height.setSuffix(" px")
        self.opacity = QSpinBox(); self.opacity.setRange(0, 100); self.opacity.setSuffix("%")
        self.font_size = QSpinBox(); self.font_size.setRange(12, 200); self.font_size.setSuffix(" px")
        self.font_color = QLineEdit()
        self.background_color = QLineEdit()
        self.background_opacity = QSpinBox(); self.background_opacity.setRange(0, 100); self.background_opacity.setSuffix("%")
        self.scroll_speed = QDoubleSpinBox(); self.scroll_speed.setRange(.1, 10); self.scroll_speed.setSingleStep(.1); self.scroll_speed.setSuffix("×")
        self.bold = QCheckBox("Chữ đậm")

        def add_layer_row(key: str, label_text: str, widget: QWidget) -> None:
            label = QLabel(label_text)
            layer_form.addRow(label, widget)
            self._layer_rows[key] = (label, widget)

        add_layer_row("enabled", "Hiển thị", self.layer_enabled)
        add_layer_row("path", "Ảnh logo", path_widget)
        add_layer_row("text", "Nội dung", self.text)
        add_layer_row("position", "Vị trí", self.position)
        add_layer_row("margin", "Cách cạnh Top/Bottom", self.margin)
        add_layer_row("margin_x", "Margin trục X", self.margin_x)
        add_layer_row("margin_y", "Margin trục Y", self.margin_y)
        add_layer_row("height", "Chiều cao logo", self.layer_height)
        add_layer_row("opacity", "Opacity", self.opacity)
        add_layer_row("font_size", "Cỡ chữ", self.font_size)
        add_layer_row("font_color", "Màu chữ", self.font_color)
        add_layer_row("background_color", "Màu nền", self.background_color)
        add_layer_row("background_opacity", "Opacity nền", self.background_opacity)
        add_layer_row("speed", "Tốc độ", self.scroll_speed)
        add_layer_row("bold", "Kiểu chữ", self.bold)
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
        self.layer_enabled.toggled.connect(self._layer_enabled_changed)
        self.text.editingFinished.connect(lambda: self._change_layer("text", self.text.text()))
        self.position.currentTextChanged.connect(lambda value: self._change_layer("position", value))
        self.margin.valueChanged.connect(lambda value: self._change_layer("margin", value))
        self.margin_x.valueChanged.connect(lambda value: self._change_layer("margin_x", value))
        self.margin_y.valueChanged.connect(lambda value: self._change_layer("margin_y", value))
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
        self.scroll_speed.valueChanged.connect(lambda value: self._change_layer("speed", value))
        self.bold.toggled.connect(lambda value: self._change_layer("bold", value))

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

    def _layer_enabled_changed(self, value: bool) -> None:
        layer = self._selected()
        if self.loading or not layer or layer.get("kind") in {"video", "voice"}:
            return
        self._change_layer("enabled", value)
        item = self.layers.currentItem()
        if item:
            self.loading = True
            item.setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
            self.loading = False
        self._layer_selected(item)

    def current_settings(self) -> dict:
        self._sync_layer_order()
        return normalize_studio_settings(self.state)

    def add_layer(self, name: str) -> None:
        kind = ASSET_KINDS.get(name)
        if not kind:
            return
        values = {}
        if kind == "logo":
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
        if not layer or layer["kind"] != "logo":
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
            if item is self.layers.currentItem():
                self._layer_selected(item)

    def _layer_selected(self, current: QListWidgetItem | None) -> None:
        layer = self._selected() if current else None
        self.loading = True
        try:
            kind = str(layer.get("kind", "")) if layer else ""
            self.selected_label.setText(f"Layer: {layer.get('name', 'Chưa chọn') if layer else 'Chưa chọn'}")
            visible_by_kind = {
                "subtitle": {"enabled", "position", "margin", "font_size", "font_color", "background_color", "background_opacity"},
                "logo": {"path", "position", "margin_x", "margin_y", "height", "opacity"},
                "intro_text": {"enabled", "text", "position", "margin", "font_size", "font_color", "background_color", "background_opacity"},
                "scroll_text": {"enabled", "text", "font_size", "font_color", "opacity", "speed", "bold"},
            }
            visible = visible_by_kind.get(kind, set())
            enabled = bool(layer.get("enabled", True)) if layer else False
            for key, (label, widget) in self._layer_rows.items():
                show = key in visible
                label.setVisible(show); widget.setVisible(show)
                widget.setEnabled(show and (key == "enabled" or enabled))
            self.asset_path.setEnabled(kind == "logo" and enabled)
            self.browse_button.setEnabled(kind == "logo" and enabled)
            positions = (
                ["top-left", "top-right", "bottom-left", "bottom-right"]
                if kind == "logo" else ["top", "bottom"]
            )
            self.position.clear(); self.position.addItems(positions)
            self.layer_enabled.setChecked(enabled)
            self.asset_path.setText(str(layer.get("path", "")) if layer else "")
            self.text.setText(str(layer.get("text", "")) if layer else "")
            self.position.setCurrentText(str(layer.get("position", positions[0])) if layer else positions[0])
            self.margin.setValue(int(layer.get("margin", 24)) if layer else 24)
            self.margin_x.setValue(int(layer.get("margin_x", 24)) if layer else 24)
            self.margin_y.setValue(int(layer.get("margin_y", 24)) if layer else 24)
            self.layer_height.setValue(int(layer.get("height", 80)) if layer else 80)
            self.opacity.setValue(int(layer.get("opacity", 60)) if layer else 60)
            self.font_size.setValue(int(layer.get("font_size", 48)) if layer else 48)
            self.font_color.setText(str(layer.get("font_color", "#FFFFFF")) if layer else "#FFFFFF")
            self.background_color.setText(
                str(layer.get("background_color", "#000000")) if layer else "#000000"
            )
            self.background_opacity.setValue(
                int(layer.get("background_opacity", 60)) if layer else 60
            )
            self.scroll_speed.setValue(float(layer.get("speed", 1.0)) if layer else 1.0)
            self.bold.setChecked(bool(layer.get("bold", True)) if layer else True)
        finally:
            self.loading = False

    def _reload_layers(self, selected_id: str = "") -> None:
        self.loading = True
        self.layers.clear()
        selected_item = None
        for layer in self.state["layers"]:
            item = QListWidgetItem(str(layer.get("name") or layer["kind"]))
            item.setData(Qt.ItemDataRole.UserRole, layer["id"])
            flags = item.flags() | Qt.ItemFlag.ItemIsDragEnabled
            if layer.get("kind") not in {"video", "voice"}:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                item.setCheckState(Qt.CheckState.Checked if layer.get("enabled", True) else Qt.CheckState.Unchecked)
            item.setFlags(flags)
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
            if layer.get("enabled", True) and layer.get("kind") == "logo"
            and not Path(str(layer.get("path", ""))).expanduser().is_file()
        ]
        if missing:
            QMessageBox.warning(self, "Thiếu file layer", f"Không tìm thấy file: {', '.join(missing)}")
            return
        self.preview.player.stop()
        self.settings_applied.emit(settings)
        self.accept()
