from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from urllib.parse import urlparse


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
    schema_version: int = 1
    name: str = "Dự án chưa đặt tên"
    source_url: str = ""
    source: str = ""
    srt_provider: str = SrtProvider.CAPCUT.value
    translation_provider: str = TranslationProvider.GEMINI_API.value
    translation_context: str = TranslationContext.GEN_Z.value
    custom_context: str = ""
    tts_provider: str = TtsProvider.GOOGLE.value
    audio_settings: dict = field(default_factory=lambda: {
        "format": "wav", "sample_rate": 44100, "channels": "Mono", "chunk_seconds": 60
    })
    tts_settings: dict = field(default_factory=lambda: {
        "voice": "vi-VN-Neural2-A", "speed": 1.0, "pitch": 0, "volume": 100
    })
    mix_settings: dict = field(default_factory=lambda: {
        "keep_original": True, "original_volume": 25, "voice_volume": 100
    })
    studio_settings: dict = field(default_factory=lambda: {
        "speed": 1.0, "pitch": 0, "zoom": 100, "color": 100,
        "flip_horizontal": False, "flip_vertical": False,
        "layers": ["Video", "Voice", "Subtitle"], "layer_start": 0, "layer_duration": 5
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
        version = data.get("schema_version")
        if version != 1:
            raise ValueError(f"Không hỗ trợ schema_version={version!r}.")
        allowed = set(cls.__dataclass_fields__)
        clean = {key: value for key, value in data.items() if key in allowed}
        project = cls(**clean)
        if len(project.step_statuses) != 8:
            raise ValueError("Project phải có trạng thái cho đúng 8 bước.")
        return project


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
