from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .models import ProjectModel

SENSITIVE_FRAGMENTS = (
    "token", "cookie", "api_key", "apikey", "secret", "password", "authorization",
    "access_key", "sessionid", "sid_tt", "sid_guard", "uid_tt",
)

SENSITIVE_TEXT = re.compile(
    r"(?i)(authorization\s*[:=]\s*|(?:api[_-]?key|token|sessionid|sid_tt|sid_guard|uid_tt|secret)\s*[=:]\s*)([^\s,;&]+)"
)
EMAIL_TEXT = re.compile(r"(?<![\w.+-])([\w.+-])[^\s@]*(@[^\s]+)")


def redact_text(value: str) -> str:
    value = SENSITIVE_TEXT.sub(lambda match: f"{match.group(1)}<redacted>", value)
    return EMAIL_TEXT.sub(r"\1***\2", value)


def sanitize(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            key: sanitize(value)
            for key, value in data.items()
            if not any(fragment in key.lower() for fragment in SENSITIVE_FRAGMENTS)
        }
    if isinstance(data, list):
        return [sanitize(item) for item in data]
    if isinstance(data, str):
        return redact_text(data)
    return data


class ProjectStore:
    def save(self, project: ProjectModel, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = sanitize(project.to_dict())
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
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
