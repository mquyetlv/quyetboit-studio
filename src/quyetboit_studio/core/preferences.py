from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..models import ProjectModel
from .studio import normalize_studio_settings


class LastUsedSettingsStore:
    """Persist reusable project configuration without credentials or project data."""

    SCALAR_FIELDS = (
        "srt_provider", "translation_provider", "translation_context", "custom_context",
        "tts_provider", "source_language", "browser", "browser_profile", "gemini_model",
        "translation_batch_size", "translation_concurrency", "capcut_voice",
    )
    DICT_FIELDS = (
        "audio_settings", "tts_settings", "mix_settings", "studio_settings", "export_settings",
    )

    def __init__(self, path: str | Path | None = None) -> None:
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.path = Path(path) if path else local / "QuyetBoITStudio" / "last-used-settings.json"

    def _payload(self, project: ProjectModel) -> dict[str, Any]:
        settings = {name: deepcopy(getattr(project, name)) for name in self.SCALAR_FIELDS}
        settings.update({name: deepcopy(getattr(project, name)) for name in self.DICT_FIELDS})
        settings["studio_settings"] = normalize_studio_settings(settings["studio_settings"])
        return {"schema_version": 1, "settings": settings}

    def save(self, project: ProjectModel) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._payload(project)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".last-used-settings.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != 1 or not isinstance(payload.get("settings"), dict):
                return {}
            return payload["settings"]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def apply(self, project: ProjectModel) -> ProjectModel:
        settings = self.load()
        defaults = ProjectModel()
        for name in self.SCALAR_FIELDS:
            value = settings.get(name)
            expected = type(getattr(defaults, name))
            if type(value) is expected and not isinstance(value, (dict, list)):
                setattr(project, name, value)
        for name in self.DICT_FIELDS:
            value = settings.get(name)
            if isinstance(value, dict):
                merged = deepcopy(getattr(defaults, name))
                merged.update(value)
                if name == "studio_settings":
                    merged = normalize_studio_settings(merged)
                setattr(project, name, merged)
        return project

    def new_project(self) -> ProjectModel:
        return self.apply(ProjectModel())
