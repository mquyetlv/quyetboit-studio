from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog

from quyetboit_studio.core.preferences import LastUsedSettingsStore
from quyetboit_studio.main_window import MainWindow
from quyetboit_studio.models import ProjectModel
from quyetboit_studio.services.capcut import Voice
from quyetboit_studio.studio import StudioDialog
from quyetboit_studio.vietsub import VoiceCatalogDialog


def test_shell_sidebar_and_pages(qtbot, tmp_path):
    window = MainWindow(LastUsedSettingsStore(tmp_path / "settings.json"))
    qtbot.addWidget(window)
    assert window.minimumWidth() == 1100
    assert window.pages.count() == 2
    window.toggle_sidebar()
    assert window.sidebar.width() == 76
    assert window.vietsub_nav.toolTip() == "VietSub"
    qtbot.mouseClick(window.audio_nav, Qt.MouseButton.LeftButton)
    assert window.pages.currentIndex() == 1


def test_vietsub_has_eight_steps(qtbot, tmp_path):
    window = MainWindow(LastUsedSettingsStore(tmp_path / "settings.json"))
    qtbot.addWidget(window)
    assert window.vietsub.stack.count() == 8
    assert len(window.vietsub.step_rows) == 8
    assert window.vietsub.run_all_btn.text() == "▶ Chạy bước 1–6"


def test_custom_context_validation_state(qtbot, tmp_path):
    settings_store = LastUsedSettingsStore(tmp_path / "settings.json")
    window = MainWindow(settings_store)
    qtbot.addWidget(window)
    page = window.vietsub.pages[3]
    page.context.setCurrentText("Tự cung cấp")
    assert page.custom.isVisibleTo(page)
    page.custom.setPlainText("Dịch tự nhiên, vui vẻ")
    assert window.project.custom_context == "Dịch tự nhiên, vui vẻ"
    assert settings_store.new_project().custom_context == "Dịch tự nhiên, vui vẻ"
    window.dirty = False


def test_studio_apply_and_cancel(qtbot, monkeypatch, tmp_path):
    project = ProjectModel()
    dialog = StudioDialog(project.studio_settings)
    qtbot.addWidget(dialog)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"image fixture")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(logo), ""))
    captured = []
    dialog.settings_applied.connect(captured.append)
    dialog.add_layer("Logo thương hiệu")
    dialog.speed.setValue(1.25)
    dialog.apply()
    assert captured[0]["speed"] == 1.25
    assert any(
        layer["kind"] == "logo" and layer["path"] == str(logo)
        for layer in captured[0]["layers"]
    )


def test_studio_reset_and_undo(qtbot, monkeypatch, tmp_path):
    dialog = StudioDialog(ProjectModel().studio_settings)
    qtbot.addWidget(dialog)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"image fixture")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(logo), ""))
    dialog.add_layer("Logo thương hiệu")
    assert dialog.layers.count() == 4
    dialog.undo()
    assert dialog.layers.count() == 3


class FakeVoiceCatalogService:
    def list_voices(self, refresh=False):
        return [
            Voice(
                "Nữ Việt", "vi-female", lang="vi", gender="female",
                category="vietnamese", categories=("vietnamese", "female_voice"),
            ),
            Voice(
                "English Male", "en-male", lang="en", gender="male",
                category="english", categories=("english", "male_voice"),
            ),
        ]


def test_voice_catalog_dialog_filters_and_selects_cached_voice(qtbot):
    dialog = VoiceCatalogDialog(
        FakeVoiceCatalogService(),  # type: ignore[arg-type]
        "en-male", {"speed": 1, "pitch": 0, "volume": 100, "concurrency": 25},
    )
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.list.count() == 2)
    qtbot.waitUntil(lambda: not dialog._tasks)
    assert dialog.selected_voice().speaker == "en-male"
    assert dialog.concurrency.minimum() == 1
    assert dialog.concurrency.maximum() == 50
    assert dialog.concurrency.value() == 25

    dialog.category.setCurrentIndex(dialog.category.findData("female"))
    assert dialog.list.count() == 1
    assert dialog.selected_voice().speaker == "vi-female"
