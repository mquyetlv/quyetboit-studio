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
    schema_version: int = 1
    project_uuid: str = field(default_factory=lambda: str(uuid4()))
    topic: str = ""
    background_video: str = ""
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
        source = Path(self.background_video).expanduser()
        if not source.is_file():
            raise ValueError("Vui lòng chọn video nền hợp lệ.")
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
