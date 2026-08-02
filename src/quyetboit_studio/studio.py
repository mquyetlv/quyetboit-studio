from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .widgets import Card


class PreviewCanvas(QFrame):
    asset_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumSize(420, 300)
        self.setStyleSheet("background:#101828;border-radius:12px;border:2px dashed #475467;")
        layout = QVBoxLayout(self)
        label = QLabel("PREVIEW VIDEO\n9:16  •  Safe area\n\nKéo logo/overlay vào đây")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color:#d0d5dd;font-size:16px;")
        layout.addWidget(label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasText() or event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        text = event.mimeData().text() or "Asset"
        self.asset_dropped.emit(text.splitlines()[0])
        event.acceptProposedAction()


class StudioDialog(QDialog):
    settings_applied = Signal(dict)

    def __init__(self, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quyết Bồ Video Studio")
        self.resize(1300, 820)
        self.setMinimumSize(1000, 680)
        self.original = deepcopy(settings)
        self.history: list[dict] = []
        self.future: list[dict] = []
        self._build_ui(settings)

    def _build_ui(self, settings: dict) -> None:
        root = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        title = QLabel("Video Studio")
        title.setObjectName("Title")
        for text, slot in (("Hoàn tác", self.undo), ("Làm lại", self.redo), ("Đặt lại", self.reset_form)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        toolbar.insertWidget(0, title)
        toolbar.insertStretch(1)
        root.addLayout(toolbar)

        split = QSplitter(Qt.Orientation.Horizontal)
        assets = Card("Tài nguyên", "Kéo logo hoặc overlay vào preview")
        self.assets = QListWidget()
        self.assets.addItems(["Logo thương hiệu", "Overlay ánh sáng", "Text đầu video", "Chữ chạy quanh video"])
        self.assets.setDragEnabled(True)
        assets.layout.addWidget(self.assets)
        assets.setMinimumWidth(190)
        split.addWidget(assets)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        self.preview = PreviewCanvas()
        self.preview.asset_dropped.connect(self.add_layer)
        center_layout.addWidget(self.preview, 3)
        timeline = Card("Timeline", "Các layer có thể chọn và sắp xếp bằng kéo-thả")
        self.layers = QListWidget()
        self.layers.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.layers.addItems(settings.get("layers", ["Video", "Voice", "Subtitle"]))
        self.layers.currentTextChanged.connect(self._layer_selected)
        timeline.layout.addWidget(self.layers)
        center_layout.addWidget(timeline, 2)
        split.addWidget(center)

        props = Card("Thuộc tính", "Điều chỉnh layer đang chọn")
        self.selected_label = QLabel("Layer: Video")
        props.layout.addWidget(self.selected_label)
        self.speed = QDoubleSpinBox(); self.speed.setRange(.25, 4); self.speed.setSingleStep(.05); self.speed.setSuffix("×")
        self.speed.setValue(float(settings.get("speed", 1)))
        self.pitch = QSpinBox(); self.pitch.setRange(-12, 12); self.pitch.setSuffix(" semitone"); self.pitch.setValue(int(settings.get("pitch", 0)))
        self.zoom = QSlider(Qt.Orientation.Horizontal); self.zoom.setRange(50, 200); self.zoom.setValue(int(settings.get("zoom", 100)))
        self.color = QSlider(Qt.Orientation.Horizontal); self.color.setRange(0, 200); self.color.setValue(int(settings.get("color", 100)))
        self.layer_start = QSpinBox(); self.layer_start.setRange(0, 36000); self.layer_start.setSuffix(" giây"); self.layer_start.setValue(int(settings.get("layer_start", 0)))
        self.layer_duration = QSpinBox(); self.layer_duration.setRange(1, 36000); self.layer_duration.setSuffix(" giây"); self.layer_duration.setValue(int(settings.get("layer_duration", 5)))
        self.flip_h = QCheckBox("Lật ngang"); self.flip_h.setChecked(bool(settings.get("flip_horizontal")))
        self.flip_v = QCheckBox("Lật dọc"); self.flip_v.setChecked(bool(settings.get("flip_vertical")))
        for label, widget in (("Tốc độ", self.speed), ("Pitch", self.pitch), ("Zoom", self.zoom), ("Màu sắc", self.color), ("Bắt đầu layer", self.layer_start), ("Thời lượng layer", self.layer_duration)):
            props.layout.addWidget(QLabel(label)); props.layout.addWidget(widget)
        props.layout.addWidget(self.flip_h); props.layout.addWidget(self.flip_v); props.layout.addStretch()
        split.addWidget(props)
        split.setSizes([210, 780, 250])
        root.addWidget(split)

        footer = QHBoxLayout()
        hint = QLabel("Cấu hình này sẽ được áp dụng bằng FFmpeg ở bước render")
        hint.setObjectName("Subtitle")
        cancel = QPushButton("Hủy"); cancel.clicked.connect(self.reject)
        apply_btn = QPushButton("Áp dụng"); apply_btn.setObjectName("Primary"); apply_btn.clicked.connect(self.apply)
        footer.addWidget(hint); footer.addStretch(); footer.addWidget(cancel); footer.addWidget(apply_btn)
        root.addLayout(footer)

    def current_settings(self) -> dict:
        return {"speed": self.speed.value(), "pitch": self.pitch.value(), "zoom": self.zoom.value(),
                "color": self.color.value(), "flip_horizontal": self.flip_h.isChecked(),
                "flip_vertical": self.flip_v.isChecked(),
                "layer_start": self.layer_start.value(), "layer_duration": self.layer_duration.value(),
                "layers": [self.layers.item(i).text() for i in range(self.layers.count())]}

    def add_layer(self, name: str) -> None:
        self.history.append(self.current_settings())
        self.future.clear()
        self.layers.addItem(name or "Asset mới")

    def _layer_selected(self, name: str) -> None:
        self.selected_label.setText(f"Layer: {name or 'Chưa chọn'}")

    def _restore(self, settings: dict) -> None:
        self.speed.setValue(settings.get("speed", 1)); self.pitch.setValue(settings.get("pitch", 0))
        self.zoom.setValue(settings.get("zoom", 100)); self.color.setValue(settings.get("color", 100))
        self.layer_start.setValue(settings.get("layer_start", 0)); self.layer_duration.setValue(settings.get("layer_duration", 5))
        self.flip_h.setChecked(settings.get("flip_horizontal", False)); self.flip_v.setChecked(settings.get("flip_vertical", False))
        self.layers.clear(); self.layers.addItems(settings.get("layers", []))

    def undo(self) -> None:
        if self.history:
            self.future.append(self.current_settings()); self._restore(self.history.pop())

    def redo(self) -> None:
        if self.future:
            self.history.append(self.current_settings()); self._restore(self.future.pop())

    def reset_form(self) -> None:
        self.history.append(self.current_settings()); self._restore(self.original)

    def apply(self) -> None:
        if self.layers.count() == 0:
            QMessageBox.warning(self, "Thiếu layer", "Studio cần ít nhất một layer.")
            return
        self.settings_applied.emit(self.current_settings())
        self.accept()
