from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import uuid4


class MinigameFormat(StrEnum):
    YOUTUBE = "16:9"
    VERTICAL = "9:16"
    BOTH = "both"

    @property
    def outputs(self) -> tuple[str, ...]:
        return ("16:9", "9:16") if self is MinigameFormat.BOTH else (self.value,)


class MinigameTtsProvider(StrEnum):
    CAPCUT = "capcut"
    GOOGLE = "google"


@dataclass(slots=True, frozen=True)
class CanvasSpec:
    width: int = 1080
    height: int = 1920

    def scale_bounds(
        self,
        bounds: tuple[int, int, int, int],
        source_size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        """Scale inclusive pixel bounds from a native asset onto this canvas."""
        source_width, source_height = source_size
        if source_width <= 0 or source_height <= 0:
            raise ValueError("Kích thước canvas nguồn phải lớn hơn 0.")
        x1, y1, x2, y2 = bounds
        return (
            round(x1 * self.width / source_width),
            round(y1 * self.height / source_height),
            round(x2 * self.width / source_width),
            round(y2 * self.height / source_height),
        )


@dataclass(slots=True, frozen=True)
class TextSlot:
    name: str
    bounds: tuple[int, int, int, int]
    max_lines: int = 2
    wrap_policy: str = "word"


@dataclass(slots=True, frozen=True)
class TextStyle:
    name: str
    font: str
    font_file: str
    min_size: int
    max_size: int
    color: str = "#F6F8FF"
    bold: bool = False
    italic: bool = False
    spacing: int = 0
    line_spacing: int = 4
    margin: int = 100


@dataclass(slots=True, frozen=True)
class TextPlacement:
    style: str
    slot: str
    y: int | None = None
    layer: int = 0


@dataclass(slots=True, frozen=True)
class AlphaMaskProfile:
    matte_threshold: int
    feather_radius: int
    panel_opacity: float
    detail_threshold: int
    algorithm_version: int = 1


@dataclass(slots=True, frozen=True)
class MinigameThemeManifest:
    theme_id: str
    display_name: str
    palette: tuple[str, ...]
    supported_formats: tuple[str, ...]
    canvas: CanvasSpec
    start_screen: Path
    background_video: Path
    background_text: Path
    mask_profile: AlphaMaskProfile
    slots: dict[str, TextSlot]
    styles: dict[str, TextStyle]
    placements: dict[str, TextPlacement]
    layout_version: int
    checksum: str


@dataclass(slots=True)
class VocabularyItem:
    vietnamese: str
    english: str
    ipa: str
    example: str

    def normalized(self) -> VocabularyItem:
        return VocabularyItem(
            self.vietnamese.strip(), self.english.strip(), self.ipa.strip(), self.example.strip()
        )

    def validate(self, index: int) -> None:
        labels = {
            "vietnamese": "nghĩa tiếng Việt",
            "english": "từ tiếng Anh",
            "ipa": "phiên âm IPA",
            "example": "câu ví dụ",
        }
        for name, label in labels.items():
            if not getattr(self, name).strip():
                raise ValueError(f"Từ #{index}: chưa nhập {label}.")


@dataclass(slots=True)
class MinigameProject:
    schema_version: int = 2
    project_uuid: str = field(default_factory=lambda: str(uuid4()))
    topic: str = ""
    background_video: str = ""
    theme_id: str = "theme-1"
    output_format: str = MinigameFormat.VERTICAL.value
    output_folder: str = "output"
    output_name: str = "english_minigame"
    tts_provider: str = MinigameTtsProvider.GOOGLE.value
    voice: str = "en-US-Neural2-F"
    voice_language: str = "en-US"
    voice_gender: str = ""
    voice_family: str = "Neural2"
    voice_categories: list[str] = field(default_factory=list)
    service_account_path: str = ""
    slow_rate: float = 0.72
    volume: int = 100
    crop_focus: dict[str, list[float]] = field(
        default_factory=lambda: {"16:9": [0.5, 0.5], "9:16": [0.5, 0.5]}
    )
    vocabulary: list[VocabularyItem] = field(default_factory=list)

    def validate(self) -> None:
        if not self.topic.strip():
            raise ValueError("Vui lòng nhập Topic.")
        if self.theme_id not in {f"theme-{index}" for index in range(1, 6)}:
            raise ValueError("Giao diện minigame không hợp lệ.")
        # Theme image is the production background. Keep this field untouched for old projects;
        # it is intentionally not validated or consumed by themed rendering.
        if not self.vocabulary:
            raise ValueError("Vui lòng nhập ít nhất một từ.")
        for index, item in enumerate(self.vocabulary, 1):
            item.validate(index)
        if self.output_format not in {item.value for item in MinigameFormat}:
            raise ValueError("Tỷ lệ video không hợp lệ.")
        if self.tts_provider not in {item.value for item in MinigameTtsProvider}:
            raise ValueError("Nhà cung cấp TTS không hợp lệ.")
        if not self.voice.strip():
            raise ValueError("Vui lòng nhập hoặc chọn voice.")
        if self.tts_provider == MinigameTtsProvider.GOOGLE.value:
            account = Path(self.service_account_path).expanduser()
            if not account.is_file() or account.suffix.lower() != ".json":
                raise ValueError("Vui lòng chọn service-account JSON cho Google TTS.")

    def to_safe_dict(self) -> dict:
        """Serialize project data without copying credential file content or its path."""
        value = asdict(self)
        value["service_account_path"] = ""
        return value

    @classmethod
    def from_dict(cls, value: dict) -> MinigameProject:
        """Restore old minigame payloads without discarding their legacy video path."""
        allowed = set(cls.__dataclass_fields__)
        data = {key: item for key, item in value.items() if key in allowed}
        data.setdefault("schema_version", 1)
        data.setdefault("theme_id", "theme-1")
        vocabulary = data.get("vocabulary", [])
        data["vocabulary"] = [
            item if isinstance(item, VocabularyItem) else VocabularyItem(**item)
            for item in vocabulary
        ]
        data["schema_version"] = 2
        return cls(**data)


@dataclass(slots=True, frozen=True)
class MinigameScene:
    kind: str
    start_ms: int
    end_ms: int
    item_index: int = -1
    phase: str = ""


@dataclass(slots=True)
class MinigameTimeline:
    scenes: list[MinigameScene]
    audio_events: list[tuple[int, Path]]
    duration_ms: int


class MinigameTimelineBuilder:
    HOOK_MS = 2_800
    QUESTION_MS = 3_000
    REVEAL_LEAD_MS = 320
    BETWEEN_VOICE_MS = (320, 480)
    AFTER_WORD_MS = 550
    REVIEW_LEAD_MS = 320
    REVIEW_GAP_MS = 280
    REVIEW_AFTER_MS = 500
    SCORE_MS = 3_000
    CTA_MS = 2_400

    def build(
        self,
        items: list[VocabularyItem],
        word_audio: list[Path],
        slow_audio: list[Path],
        example_audio: list[Path],
        durations: dict[Path, int],
    ) -> MinigameTimeline:
        if not (len(items) == len(word_audio) == len(slow_audio) == len(example_audio)):
            raise ValueError("Số từ và số audio minigame không khớp.")
        scenes = [MinigameScene("hook", 0, self.HOOK_MS)]
        audio_events: list[tuple[int, Path]] = []
        cursor = self.HOOK_MS
        for index in range(len(items)):
            question_start = cursor
            reveal = question_start + self.QUESTION_MS
            scenes.append(MinigameScene("word", question_start, reveal, index, "question"))
            normal = word_audio[index]
            slow = slow_audio[index]
            normal_start = reveal + self.REVEAL_LEAD_MS
            slow_start = normal_start + durations[normal] + self.BETWEEN_VOICE_MS[0]
            final_start = slow_start + durations[slow] + self.BETWEEN_VOICE_MS[1]
            cursor = final_start + durations[normal] + self.AFTER_WORD_MS
            scenes.append(MinigameScene("word", reveal, cursor, index, "answer"))
            scenes.extend(
                [
                    MinigameScene(
                        "voice",
                        normal_start,
                        normal_start + durations[normal],
                        index,
                        "listen",
                    ),
                    MinigameScene(
                        "voice",
                        slow_start,
                        slow_start + durations[slow],
                        index,
                        "slow",
                    ),
                    MinigameScene(
                        "voice",
                        final_start,
                        final_start + durations[normal],
                        index,
                        "repeat",
                    ),
                ]
            )
            audio_events.extend(
                [(normal_start, normal), (slow_start, slow), (final_start, normal)]
            )
        for index in range(len(items)):
            normal = word_audio[index]
            example = example_audio[index]
            start = cursor
            word_start = start + self.REVIEW_LEAD_MS
            example_start = word_start + durations[normal] + self.REVIEW_GAP_MS
            cursor = example_start + durations[example] + self.REVIEW_AFTER_MS
            scenes.append(MinigameScene("review", start, cursor, index))
            audio_events.extend([(word_start, normal), (example_start, example)])
        scenes.append(MinigameScene("score", cursor, cursor + self.SCORE_MS))
        cursor += self.SCORE_MS
        scenes.append(MinigameScene("cta", cursor, cursor + self.CTA_MS))
        cursor += self.CTA_MS
        return MinigameTimeline(scenes, audio_events, cursor)


def score_ranges(total: int) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    if total < 1:
        raise ValueError("Tổng số từ phải lớn hơn 0.")
    beginner_end = max(0, (total * 60 + 99) // 100 - 1)
    return (0, beginner_end), (beginner_end + 1, total - 1), (total, total)
