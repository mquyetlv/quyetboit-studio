import json

import pytest

from quyetboit_studio.models import ProjectModel
from quyetboit_studio.persistence import ProjectStore, sanitize


def test_round_trip_project(tmp_path):
    project = ProjectModel(name="Demo", source_url="https://www.douyin.com/video/1")
    project.studio_settings["layers"].append("Logo")
    path = tmp_path / "demo.qbstudio.json"
    ProjectStore().save(project, path)
    loaded = ProjectStore().load(path)
    assert loaded.name == "Demo"
    assert loaded.source_url == project.source_url
    assert loaded.studio_settings["layers"][-1] == "Logo"


def test_sensitive_data_removed_recursively():
    payload = {"model": "gemini", "api_key": "secret", "nested": {"cookie": "x", "safe": 1}}
    assert sanitize(payload) == {"model": "gemini", "nested": {"safe": 1}}


def test_saved_project_has_no_credentials(tmp_path):
    project = ProjectModel()
    project.metadata = {"token": "secret", "safe": "value", "nested": {"password": "hidden"}}
    path = ProjectStore().save(project, tmp_path / "safe.json")
    text = path.read_text(encoding="utf-8").lower()
    assert "secret" not in text
    assert "password" not in text
    assert json.loads(text)["metadata"]["safe"] == "value"


def test_invalid_json_has_friendly_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="Không thể mở project"):
        ProjectStore().load(path)

