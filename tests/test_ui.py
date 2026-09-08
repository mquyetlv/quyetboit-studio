from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog

from quyetboit_studio.core.preferences import LastUsedSettingsStore
from quyetboit_studio.main_window import MainWindow
from quyetboit_studio.models import ProjectModel, SourceMode
from quyetboit_studio.services.capcut import Voice
from quyetboit_studio.studio import StudioDialog
from quyetboit_studio.vietsub import VoiceCatalogDialog


def test_shell_sidebar_and_pages(qtbot, tmp_path):
    window = MainWindow(LastUsedSettingsStore(tmp_path / "settings.json"))
    qtbot.addWidget(window)
    assert window.minimumWidth() == 1100
    assert window.pages.count() == 6
    window.toggle_sidebar()
    assert window.sidebar.width() == 76
    assert window.vietsub_nav.toolTip() == "VietSub"
    qtbot.mouseClick(window.audio_nav, Qt.MouseButton.LeftButton)
    assert window.pages.currentIndex() == 2


def test_highlight_football_menu_and_json_validation(qtbot, tmp_path):
    window = MainWindow(LastUsedSettingsStore(tmp_path / "settings.json"))
    qtbot.addWidget(window)
    qtbot.mouseClick(window.highlight_nav, Qt.MouseButton.LeftButton)
    assert window.pages.currentIndex() == 0
    assert window.highlight.start_button.text() == "▶  Tạo video"
    assert window.highlight.stop_button.isHidden()
    window.highlight.youtube_url.setText("https://youtu.be/example")
    assert window.highlight.validate() is not None
    assert window.minigame.shutdown()
    window.set_dirty(False)


def test_highlight_football_keeps_non_sensitive_voice_configuration(qtbot, tmp_path):
    window = MainWindow(LastUsedSettingsStore(tmp_path / "settings.json"))
    qtbot.addWidget(window)
    page = window.highlight
    page.voice_provider = "google"
    page.voice_id = "vi-VN-Neural2-A"
    page.voice_name = "Vietnamese Neural2 A"
    page.voice_language = "vi-VN"
    page.voice_volume = 85
    page.voice_rate_input.setValue(1.25)
    page.ken_burns_start.setValue(1.00)
    page.ken_burns_end.setValue(1.15)
    page.top_text.setText("Highlight mới")
    page.subtitle_background_opacity.setValue(75)
    settings = page.to_safe_dict()
    assert settings["tts_provider"] == "google"
    assert settings["voice"] == "vi-VN-Neural2-A"
    assert settings["voice_rate"] == 1.25
    assert "service_account_path" not in settings
    assert settings["ken_burns_start_zoom"] == 1.00
    assert settings["ken_burns_end_zoom"] == 1.15
    assert settings["top_text"] == "Highlight mới"
    assert settings["subtitle_background_opacity"] == 75
    page.set_settings({"voice_rate": 0.80, "ken_burns_start_zoom": 1.02, "ken_burns_end_zoom": 1.20})
    assert page.voice_rate_input.value() == 0.80
    assert page.ken_burns_start.value() == 1.02
    assert page.ken_burns_end.value() == 1.20
    assert window.minigame.shutdown()
    window.set_dirty(False)


def test_english_menu_has_three_placeholder_pages(qtbot, tmp_path):
    window = MainWindow(LastUsedSettingsStore(tmp_path / "settings.json"))
    qtbot.addWidget(window)

    assert window.english_submenu.isHidden()
    qtbot.mouseClick(window.english_nav, Qt.MouseButton.LeftButton)
    assert not window.english_submenu.isHidden()
    assert window.highlight_nav.isChecked()
    assert not window.english_nav.isChecked()
    assert [button.toolTip() for button in window.english_subnav_buttons] == [
        "Mini game", "Học từ mới", "Hội thoại",
    ]

    for expected_index, button in enumerate(window.english_subnav_buttons, start=2):
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        assert window.pages.currentIndex() == expected_index
        assert button.isChecked()

    window.toggle_sidebar()
    assert window.english_submenu.isHidden()
    window.toggle_sidebar()
    assert not window.english_submenu.isHidden()


def test_vietsub_has_eight_steps(qtbot, tmp_path):
    window = MainWindow(LastUsedSettingsStore(tmp_path / "settings.json"))
    qtbot.addWidget(window)
    assert window.vietsub.stack.count() == 8
    assert len(window.vietsub.step_rows) == 8
    assert window.vietsub.run_all_btn.text() == "▶ Chạy tất cả các bước"


def test_source_step_has_three_download_modes_and_restores_multi_links(qtbot, tmp_path):
    project = ProjectModel(
        source_mode=SourceMode.MULTI.value,
        source_urls=[
            "https://www.douyin.com/video/1",
            "https://www.douyin.com/video/2",
        ],
    )
    window = MainWindow(LastUsedSettingsStore(tmp_path / "settings.json"))
    qtbot.addWidget(window)
    window.project = project
    window.vietsub.set_project(project)
    source = window.vietsub.pages[0]

    assert source.tabs.count() == 3
    assert [source.tabs.tabText(index) for index in range(3)] == [
        "Tải từng tập", "Tải Theo Series", "Tải nhiều Videos",
    ]
    assert source.tabs.currentIndex() == 2
    assert source.multi_urls.toPlainText().splitlines() == project.source_urls
    assert source.analyze() is True
    window.dirty = False


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
