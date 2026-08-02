from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path

from .models import Artifact, sha256_file


class ArtifactCache:
    """Checksum-verified, atomic artifact manifest with downstream invalidation."""

    def __init__(self, workspace: str | Path, manifest_name: str = "manifest.json") -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.workspace / manifest_name
        self._lock = threading.RLock()
        self._manifest = self._load()

    def _load(self) -> dict:
        if not self.manifest_path.exists():
            return {"schema_version": 1, "artifacts": {}}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "artifacts": {}}
        return data if isinstance(data.get("artifacts"), dict) else {"schema_version": 1, "artifacts": {}}

    @staticmethod
    def key(step: int | str, input_hash: str, config_hash: str, item_id: str = "") -> str:
        return f"{int(step):02d}:{input_hash}:{config_hash}:{item_id}"

    def get(
        self, step: int, input_hash: str, config_hash: str, item_id: str = ""
    ) -> Artifact | None:
        key = self.key(step, input_hash, config_hash, item_id)
        with self._lock:
            record = self._manifest["artifacts"].get(key)
        if not record:
            return None
        artifact = Artifact.from_dict(record)
        try:
            resolved = artifact.path.resolve()
            resolved.relative_to(self.workspace)
            if not resolved.is_file() or resolved.stat().st_size != artifact.size:
                return None
            if sha256_file(resolved) != artifact.checksum:
                return None
        except (OSError, ValueError):
            return None
        artifact.path = resolved
        return artifact

    def put(
        self, step: int, artifact: Artifact, *, item_id: str = ""
    ) -> Artifact:
        resolved = artifact.path.resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("Artifact phải nằm trong workspace.") from exc
        artifact.path = resolved
        artifact.refresh()
        key = self.key(step, artifact.input_hash, artifact.config_hash, item_id)
        with self._lock:
            self._manifest["artifacts"][key] = artifact.to_dict()
            self._write_atomic()
        return artifact

    def invalidate(self, step: int, *, downstream: bool = True) -> list[Path]:
        removed: list[Path] = []
        with self._lock:
            keys = [
                key for key in self._manifest["artifacts"]
                if int(key.split(":", 1)[0]) >= step
            ] if downstream else [
                key for key in self._manifest["artifacts"] if int(key.split(":", 1)[0]) == step
            ]
            for key in keys:
                record = self._manifest["artifacts"].pop(key)
                removed.append(Path(record["path"]))
            self._write_atomic()
        return removed

    def clear_files(self, records: Iterable[Path] | None = None) -> None:
        targets = list(records) if records is not None else self.invalidate(0)
        for path in targets:
            try:
                resolved = path.resolve()
                resolved.relative_to(self.workspace)
                if resolved.is_file():
                    resolved.unlink()
            except (OSError, ValueError):
                continue

    def _write_atomic(self) -> None:
        fd, temporary = tempfile.mkstemp(
            prefix=".manifest.", suffix=".tmp", dir=self.manifest_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(self._manifest, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.manifest_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
