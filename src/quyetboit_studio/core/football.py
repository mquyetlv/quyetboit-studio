"""Credential-free input contract for Football Highlight.

The renderer is deliberately kept out of the Qt page.  This module is the
single place where user supplied segment JSON is parsed before a future
pipeline downloads media or starts FFmpeg.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

_TIMESTAMP = re.compile(r"^(?:\d{1,2}:)?[0-5]\d:[0-5]\d$")
_SKIP_FRAME = {"", "không xuất hiện", "not found", "none", "n/a"}


@dataclass(frozen=True, slots=True)
class FootballRenderConfig:
    """Non-sensitive visual settings for the football renderer."""

    ken_burns_start_zoom: float = 1.00
    ken_burns_end_zoom: float = 1.15
    top_text: str = ""
    top_font_size: int = 48
    top_margin: int = 24
    top_font_color: str = "#FFFFFF"
    top_background_color: str = "#000000"
    top_background_opacity: int = 60
    subtitle_position: str = "bottom"
    subtitle_font_size: int = 44
    subtitle_margin: int = 56
    subtitle_font_color: str = "#FFFFFF"
    subtitle_background_color: str = "#000000"
    subtitle_background_opacity: int = 60

    def __post_init__(self) -> None:
        if not 1.0 <= self.ken_burns_start_zoom < self.ken_burns_end_zoom <= 1.30:
            raise ValueError("Zoom Ken Burns phải nằm trong 1.00–1.30 và zoom kết thúc phải lớn hơn zoom bắt đầu.")
        if self.subtitle_position not in {"top", "bottom"}:
            raise ValueError("Vị trí phụ đề chỉ có thể là trên hoặc dưới.")
        for name in ("top_font_size", "subtitle_font_size"):
            if not 12 <= getattr(self, name) <= 200:
                raise ValueError("Cỡ chữ phải nằm trong 12–200 px.")
        for name in ("top_margin", "subtitle_margin"):
            if not 0 <= getattr(self, name) <= 1000:
                raise ValueError("Margin phải nằm trong 0–1000 px.")
        for name in ("top_background_opacity", "subtitle_background_opacity"):
            if not 0 <= getattr(self, name) <= 100:
                raise ValueError("Opacity nền phải nằm trong 0–100%.")


@dataclass(frozen=True, slots=True)
class FootballSegment:
    match_minute: str
    situation_type: str
    frames: dict[str, str | list[str]]
    content: str
    tts_text: str

    def timestamps(self) -> list[str]:
        values: list[str] = []
        for value in self.frames.values():
            candidates = value if isinstance(value, list) else [value]
            for candidate in candidates:
                normalized = candidate.strip()
                if normalized.casefold() not in _SKIP_FRAME:
                    values.append(normalized)
        return values


@dataclass(frozen=True, slots=True)
class FootballHighlightInput:
    youtube_url: str
    segments: tuple[FootballSegment, ...]

    @staticmethod
    def validate_youtube_url(value: str) -> str:
        url = value.strip()
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        accepted = {"youtube.com", "youtu.be", "youtube-nocookie.com"}
        if parsed.scheme != "https" or not any(host == item or host.endswith(f".{item}") for item in accepted):
            raise ValueError("URL nguồn phải là liên kết HTTPS của YouTube hoặc youtu.be.")
        return url

    @classmethod
    def from_json(cls, youtube_url: str, value: str) -> FootballHighlightInput:
        source = cls.validate_youtube_url(youtube_url)
        try:
            payload = json.loads(value.lstrip("\ufeff"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON không hợp lệ tại dòng {exc.lineno}, cột {exc.colno}: {exc.msg}.") from exc
        if not isinstance(payload, list) or not payload:
            raise ValueError("JSON Highlight phải là một mảng có ít nhất một phân đoạn.")
        segments: list[FootballSegment] = []
        required = {"match_minute", "situation_type", "frames", "content", "tts_text"}
        for index, row in enumerate(payload, 1):
            label = f"Phân đoạn #{index}"
            if not isinstance(row, dict):
                raise TypeError(f"{label} phải là một object.")
            missing = sorted(required - set(row))
            if missing:
                raise ValueError(f"{label} thiếu trường: {', '.join(missing)}.")
            if not all(isinstance(row[key], str) for key in ("match_minute", "situation_type", "content", "tts_text")):
                raise ValueError(f"{label}: match_minute, situation_type, content và tts_text phải là chuỗi.")
            frames = row["frames"]
            if not isinstance(frames, dict) or not frames:
                raise ValueError(f"{label}.frames phải là object không rỗng.")
            clean_frames: dict[str, str | list[str]] = {}
            for key, frame_value in frames.items():
                if not isinstance(key, str) or not isinstance(frame_value, (str, list)):
                    raise TypeError(f"{label}.frames chỉ nhận giá trị timestamp hoặc mảng timestamp.")
                candidates = frame_value if isinstance(frame_value, list) else [frame_value]
                if not all(isinstance(item, str) for item in candidates):
                    raise ValueError(f"{label}.frames.{key} chỉ nhận chuỗi timestamp.")
                for timestamp in candidates:
                    normalized = timestamp.strip()
                    if normalized.casefold() not in _SKIP_FRAME and not _TIMESTAMP.fullmatch(normalized):
                        raise ValueError(f"{label}.frames.{key} có timestamp không hợp lệ: {normalized!r}.")
                clean_frames[key] = [item.strip() for item in candidates] if isinstance(frame_value, list) else frame_value.strip()
            if not row["content"].strip():
                raise ValueError(f"{label}.content không được để trống.")
            if not row["tts_text"].strip():
                raise ValueError(f"{label}.tts_text không được để trống.")
            segments.append(FootballSegment(row["match_minute"].strip(), row["situation_type"].strip(), clean_frames, row["content"].strip(), row["tts_text"].strip()))
        return cls(source, tuple(segments))

    def to_dict(self) -> dict[str, object]:
        return {"youtube_url": self.youtube_url, "segments": [asdict(item) for item in self.segments]}

    @classmethod
    def example_json(cls) -> str:
        return json.dumps([{
            "match_minute": "12:34", "situation_type": "Bàn thắng",
            "frames": {"setup": "12:28", "finish": ["12:32", "12:34"]},
            "content": "Một tình huống phối hợp đẹp mắt của Manchester United và bàn thắng được ghi.",
            "tts_text": "Một tình huống phối hợp đẹp mắt của Man-chét-tơ Yu-nai-tịt và bàn thắng được ghi.",
        }], ensure_ascii=False, indent=2) + "\n"
