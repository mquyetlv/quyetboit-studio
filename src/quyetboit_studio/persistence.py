from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ProjectModel

SENSITIVE_FRAGMENTS = ("token", "cookie", "api_key", "apikey", "secret", "password")


def sanitize(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            key: sanitize(value)
            for key, value in data.items()
            if not any(fragment in key.lower() for fragment in SENSITIVE_FRAGMENTS)
        }
    if isinstance(data, list):
        return [sanitize(item) for item in data]
    return data


class ProjectStore:
    def save(self, project: ProjectModel, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = sanitize(project.to_dict())
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def load(self, path: str | Path) -> ProjectModel:
        source = Path(path)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Không thể mở project: {exc}") from exc
        return ProjectModel.from_dict(payload)

    def autosave(self, project: ProjectModel, path: str | Path) -> Path:
        return self.save(project, path)

