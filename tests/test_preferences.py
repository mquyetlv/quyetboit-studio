import json

from quyetboit_studio.core.preferences import LastUsedSettingsStore
from quyetboit_studio.core.studio import new_studio_layer
from quyetboit_studio.models import ProjectModel


def test_last_used_settings_roundtrip_excludes_project_and_credentials(tmp_path):
    path = tmp_path / "last-used.json"
    store = LastUsedSettingsStore(path)
    project = ProjectModel()
    project.source_url = "https://www.douyin.com/video/secret-project"
    project.workspace_path = "C:/private/workspace"
    project.translation_batch_size = 80
    project.mix_settings["original_volume"] = 12
    project.studio_settings["layers"].append(
        new_studio_layer("intro_text", text="Text dùng lại", margin=32, width_percent=88)
    )
    project.studio_settings["layers"].append(
        new_studio_layer(
            "scroll_text", text="Watermark", font_size=52, opacity=45,
            speed=1.6, bold=False,
        )
    )
    project.__dict__["_gemini_api_key"] = "secret-api-key"
    project.__dict__["_douyin_cookies_file"] = "C:/private/cookies.txt"

    store.save(project)
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    restored = store.new_project()

    assert payload["schema_version"] == 1
    assert "secret-project" not in raw
    assert "private/workspace" not in raw
    assert "secret-api-key" not in raw
    assert "cookies.txt" not in raw
    assert restored.source_url == ""
    assert restored.workspace_path == ""
    assert restored.project_uuid != project.project_uuid
    assert restored.translation_batch_size == 80
    assert restored.mix_settings["original_volume"] == 12
    fixed_text = restored.studio_settings["layers"][-2]
    moving_text = restored.studio_settings["layers"][-1]
    assert fixed_text["text"] == "Text dùng lại"
    assert "width_percent" not in fixed_text
    assert moving_text["text"] == "Watermark"
    assert moving_text["font_size"] == 52
    assert moving_text["opacity"] == 45
    assert moving_text["speed"] == 1.6
    assert moving_text["bold"] is False


def test_invalid_last_used_settings_fall_back_to_defaults(tmp_path):
    path = tmp_path / "last-used.json"
    path.write_text("{broken", encoding="utf-8")

    project = LastUsedSettingsStore(path).new_project()

    assert project.translation_batch_size == 50
    assert project.studio_settings["zoom"] == 100
