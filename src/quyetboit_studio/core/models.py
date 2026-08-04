from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Artifact:
    path: Path
    checksum: str = ""
    size: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    input_hash: str = ""
    config_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def refresh(self) -> Artifact:
        self.size = self.path.stat().st_size
        self.checksum = sha256_file(self.path)
        return self

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Artifact:
        target: type[Artifact] = cls
        if cls is Artifact:
            if "source_start_ms" in value:
                target = AudioChunk
            elif "sample_rate" in value:
                target = AudioArtifact
            elif "width" in value:
                target = VideoArtifact
        allowed = set(target.__dataclass_fields__)
        fields = {key: item for key, item in value.items() if key in allowed}
        fields["path"] = Path(value["path"])
        return target(**fields)


@dataclass(slots=True)
class VideoArtifact(Artifact):
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0


@dataclass(slots=True)
class AudioArtifact(Artifact):
    duration_ms: int = 0
    sample_rate: int = 0
    channels: int = 0


@dataclass(slots=True)
class AudioChunk(AudioArtifact):
    index: int = 0
    source_start_ms: int = 0
    source_end_ms: int = 0


@dataclass(slots=True)
class SrtCue:
    id: int
    start_ms: int
    end_ms: int
    text: str
    translated_text: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def normalized(self) -> SrtCue:
        self.start_ms = max(0, int(self.start_ms))
        self.end_ms = max(self.start_ms + 1, int(self.end_ms))
        self.text = self.text.strip()
        self.translated_text = self.translated_text.strip()
        return self


@dataclass(slots=True)
class StepResult:
    step: int
    artifacts: list[Artifact] = field(default_factory=list)
    data: Any = None
    cache_hit: bool = False
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


@dataclass(slots=True, frozen=True)
class ProgressEvent:
    step: int
    fraction: float
    message: str = ""
    state: str = "running"
    current: int | float | None = None
    total: int | float | None = None
    unit: str = ""
    item_id: str = ""


class PipelineError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        user_message: str,
        *,
        provider: str = "pipeline",
        retryable: bool = False,
        debug_detail: str = "",
        log_detail: str = "",
    ) -> None:
        super().__init__(user_message)
        self.error_code = error_code
        self.provider = provider
        self.retryable = retryable
        self.user_message = user_message
        self.debug_detail = debug_detail
        # Provider-safe diagnostic intended for the user-visible log.
        self.log_detail = log_detail


class CancelledError(PipelineError):
    def __init__(self) -> None:
        super().__init__("cancelled", "Tác vụ đã bị hủy.", retryable=True)


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise CancelledError()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


ProgressCallback = Callable[[ProgressEvent], None]


def sha256_file(path: str | Path, cancel: CancellationToken | None = None) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if cancel:
                cancel.raise_if_cancelled()
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    import json

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
