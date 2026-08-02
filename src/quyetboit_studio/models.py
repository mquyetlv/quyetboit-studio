from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from urllib.parse import urlparse
from uuid import uuid4


class VideoSource(StrEnum):
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    BILIBILI = "bilibili"


class SrtProvider(StrEnum):
    CAPCUT = "CapCut API"
    CAPCUT_CN = "CapCut CN API"


class TranslationProvider(StrEnum):
    GEMINI_API = "Google Gemini API"
    GEMINI_BROWSER = "Gemini Browser"
    AI_STUDIO_BROWSER = "Google AI Studio Browser"
    CHATGPT_BROWSER = "ChatGPT Browser"


class TranslationContext(StrEnum):
    GEN_Z = "Giọng điệu Gen Z"
    FUNNY = "Hài hước"
    TRENDING = "Bắt trend"
    CUSTOM = "Tự cung cấp"


class TtsProvider(StrEnum):
    GOOGLE = "Google Text-to-Speech"
    CAPCUT = "CapCut Text-to-Speech"


class StepStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineState(StrEnum):
    PREPARING = "preparing"
    RUNNING = "running"
    WAITING_REMOTE = "waiting_remote"
    RETRYING = "retrying"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


STEP_TITLES = (
    "Nhập video",
    "Tách & chia âm thanh",
    "Chuyển audio thành SRT",
    "Dịch SRT sang tiếng Việt",
    "Tạo voice tiếng Việt",
    "Ghép voice vào video",
    "Chỉnh sửa trong Studio",
    "Xuất video",
)


@dataclass
class ProjectModel:
    schema_version: int = 2
    project_uuid: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Dự án chưa đặt tên"
    source_url: str = ""
    source: str = ""
    srt_provider: str = SrtProvider.CAPCUT.value
    translation_provider: str = TranslationProvider.GEMINI_API.value
    translation_context: str = TranslationContext.GEN_Z.value
    custom_context: str = ""
    tts_provider: str = TtsProvider.CAPCUT.value
    source_language: str = "zh-CN"
    browser: str = "chrome"
    browser_profile: str = "Default"
    gemini_model: str = "gemini-3.6-flash"
    translation_batch_size: int = 50
    translation_concurrency: int = 3
    capcut_voice: str = ""
    workspace_path: str = ""
    artifact_manifest: str = "manifest.json"
    config_hash: str = ""
    audio_settings: dict = field(default_factory=lambda: {
        "format": "m4a", "sample_rate": 16000, "channels": "Mono", "chunk_seconds": 240,
        "chunk_min_seconds": 180, "chunk_max_seconds": 300, "overlap_ms": 250
    })
    tts_settings: dict = field(default_factory=lambda: {
        "voice": "", "speed": 1.0, "pitch": 0, "volume": 100, "concurrency": 25
    })
    mix_settings: dict = field(default_factory=lambda: {
        "keep_original": True, "original_volume": 25, "voice_volume": 100
    })
    studio_settings: dict = field(default_factory=lambda: {
        "speed": 1.0, "pitch": 0, "zoom": 100, "color": 100,
        "flip_horizontal": False, "flip_vertical": False,
        "layers": [
            {"id": "video", "kind": "video", "name": "Video", "enabled": True},
            {"id": "voice", "kind": "voice", "name": "Voice", "enabled": True},
            {"id": "subtitle", "kind": "subtitle", "name": "Subtitle", "enabled": True},
        ],
    })
    export_settings: dict = field(default_factory=lambda: {
        "folder": "output", "filename": "video_vietsub", "format": "mp4",
        "resolution": "1080p", "fps": 30, "quality": "Cao"
    })
    metadata: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    step_statuses: list[str] = field(default_factory=lambda: [
        StepStatus.READY.value, *([StepStatus.PENDING.value] * 7)
    ])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ProjectModel:
        if not isinstance(data, dict):
            raise TypeError("Dữ liệu project phải là một JSON object.")
        version = data.get("schema_version", 1)
        if version not in {1, 2}:
            raise ValueError(f"Không hỗ trợ schema_version={version!r}.")
        if version == 1:
            data = cls._migrate_v1(data)
        allowed = set(cls.__dataclass_fields__)
        clean = {key: value for key, value in data.items() if key in allowed}
        project = cls(**clean)
        if len(project.step_statuses) != 8:
            raise ValueError("Project phải có trạng thái cho đúng 8 bước.")
        return project

    @classmethod
    def _migrate_v1(cls, data: dict) -> dict:
        migrated = dict(data)
        migrated["schema_version"] = 2
        defaults = cls()
        for name in (
            "project_uuid", "source_language", "browser", "browser_profile", "gemini_model",
            "translation_batch_size", "translation_concurrency", "capcut_voice", "workspace_path",
            "artifact_manifest", "config_hash",
        ):
            migrated.setdefault(name, getattr(defaults, name))
        return migrated


def detect_video_source(url: str) -> VideoSource:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Vui lòng nhập một URL http/https hợp lệ.")
    host = parsed.hostname.lower().rstrip(".")
    domains = {
        "douyin.com": VideoSource.DOUYIN,
        "xiaohongshu.com": VideoSource.XIAOHONGSHU,
        "bilibili.com": VideoSource.BILIBILI,
    }
    for domain, source in domains.items():
        if host == domain or host.endswith(f".{domain}"):
            return source
    raise ValueError("Chỉ hỗ trợ video từ Douyin, Xiaohongshu hoặc Bilibili.")
