from __future__ import annotations

import json
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


class VocabularyLevel(StrEnum):
    EASY = "Dễ"
    MEDIUM = "Trung bình"
    HARD = "Khó"

    @classmethod
    def normalize(cls, value: str) -> str:
        normalized = value.strip().casefold()
        aliases = {item.value.casefold(): item.value for item in cls}
        try:
            return aliases[normalized]
        except KeyError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"level phải là một trong: {choices}.") from exc


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
    outline_color: str = "#07152B"
    shadow_color: str = "#07152B"
    outline_width: int = 2
    shadow_depth: int = 1
    bold: bool = False
    italic: bool = False
    spacing: int = 0
    line_spacing: int = 4
    margin: int = 100


@dataclass(slots=True, frozen=True)
class ThemeColorPalette:
    """Semantic text colors for every surface used by a minigame theme."""

    primary: str
    secondary: str
    accent: str
    outline: str
    shadow: str
    review_primary: str
    review_secondary: str
    review_accent: str
    review_outline: str
    hook_primary: str
    hook_secondary: str
    hook_outline: str


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
    colors: ThemeColorPalette
    supported_formats: tuple[str, ...]
    canvas: CanvasSpec
    start_screen: Path
    background_video: Path
    background_text: Path
    score_screen: Path
    review_start_screen: Path
    review_screen: Path
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
    word_type: str = ""
    example_vietnamese: str = ""
    level: str = VocabularyLevel.EASY.value

    def normalized(self) -> VocabularyItem:
        return VocabularyItem(
            self.vietnamese.strip(),
            self.english.strip(),
            self.ipa.strip(),
            self.example.strip(),
            self.word_type.strip(),
            self.example_vietnamese.strip(),
            VocabularyLevel.normalize(self.level),
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
        try:
            VocabularyLevel.normalize(self.level)
        except ValueError as exc:
            raise ValueError(f"Từ #{index}: {exc}") from exc

    def to_content_dict(self) -> dict[str, str]:
        return {
            "vietnamese": self.vietnamese,
            "english": self.english,
            "ipa": self.ipa,
            "wordType": self.word_type,
            "example": self.example,
            "exampleVietnamese": self.example_vietnamese,
            "level": self.level,
        }


@dataclass(slots=True)
class MinigameContent:
    """Credential-free JSON input used by the Minigame content editor."""

    topic: str
    words: list[VocabularyItem]
    schema_version: int = 1

    _ROOT_KEYS = frozenset({"schemaVersion", "topic", "words"})
    _WORD_KEYS = frozenset(
        {
            "vietnamese", "english", "ipa", "wordType", "workType", "example",
            "exampleVietnamese", "level",
        }
    )
    _REQUIRED_WORD_KEYS = frozenset(
        {"vietnamese", "english", "ipa", "example", "exampleVietnamese", "level"}
    )

    @classmethod
    def blank(cls, count: int = 5) -> MinigameContent:
        count = max(1, min(50, count))
        levels = tuple(VocabularyLevel)
        return cls(
            "",
            [
                VocabularyItem("", "", "", "", "", "", levels[index % 3].value)
                for index in range(count)
            ],
        )

    @classmethod
    def example(cls) -> MinigameContent:
        return cls(
            "Hospital",
            [
                VocabularyItem(
                    "Bác sĩ",
                    "Doctor",
                    "/ˈdɒk.tər/",
                    "The doctor checks the patient.",
                    "Noun",
                    "Bác sĩ khám cho bệnh nhân.",
                    "Dễ",
                ),
                VocabularyItem(
                    "Y tá", "Nurse", "/nɜːrs/", "The nurse helps the doctor.",
                    "Noun", "Y tá hỗ trợ bác sĩ.", "Trung bình",
                ),
                VocabularyItem(
                    "Bệnh nhân", "Patient", "/ˈpeɪ.ʃənt/", "The patient is waiting.",
                    "Noun", "Bệnh nhân đang chờ.", "Khó",
                ),
            ],
        )

    @classmethod
    def from_json(cls, value: str) -> MinigameContent:
        try:
            payload = json.loads(value.lstrip("\ufeff"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"JSON không hợp lệ tại dòng {exc.lineno}, cột {exc.colno}: {exc.msg}."
            ) from exc
        try:
            return cls.from_dict(payload)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc

    @classmethod
    def from_dict(cls, payload: object) -> MinigameContent:
        if not isinstance(payload, dict):
            raise TypeError("JSON gốc phải là một object.")
        unknown = sorted(set(payload) - cls._ROOT_KEYS)
        if unknown:
            raise ValueError(f"JSON có trường gốc không hỗ trợ: {', '.join(unknown)}.")
        missing = sorted(cls._ROOT_KEYS - set(payload))
        if missing:
            raise ValueError(f"JSON thiếu trường gốc: {', '.join(missing)}.")
        if payload["schemaVersion"] != 1:
            raise ValueError("schemaVersion phải bằng 1.")
        if not isinstance(payload["topic"], str):
            raise TypeError("topic phải là chuỗi.")
        rows = payload["words"]
        if not isinstance(rows, list):
            raise TypeError("words phải là một array.")
        if not 1 <= len(rows) <= 50:
            raise ValueError("words phải có từ 1 đến 50 phần tử.")
        words: list[VocabularyItem] = []
        for index, row in enumerate(rows):
            path = f"words[{index}]"
            if not isinstance(row, dict):
                raise TypeError(f"{path} phải là một object.")
            unknown = sorted(set(row) - cls._WORD_KEYS)
            if unknown:
                raise ValueError(f"{path} có trường không hỗ trợ: {', '.join(unknown)}.")
            missing = sorted(cls._REQUIRED_WORD_KEYS - set(row))
            if "wordType" not in row and "workType" not in row:
                missing.append("wordType")
            if missing:
                raise ValueError(f"{path} thiếu trường: {', '.join(sorted(missing))}.")
            values = {
                "vietnamese": row["vietnamese"],
                "english": row["english"],
                "ipa": row["ipa"],
                "word_type": row.get("wordType", row.get("workType")),
                "example": row["example"],
                "example_vietnamese": row["exampleVietnamese"],
                "level": row["level"],
            }
            invalid = [name for name, item in values.items() if not isinstance(item, str)]
            if invalid:
                raise ValueError(f"{path}.{invalid[0]} phải là chuỗi.")
            try:
                words.append(VocabularyItem(**values).normalized())
            except ValueError as exc:
                raise ValueError(f"{path}.{exc}") from exc
        return cls(payload["topic"].strip(), words)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "topic": self.topic,
            "words": [word.to_content_dict() for word in self.words],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"


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
        data["vocabulary"] = []
        levels = tuple(VocabularyLevel)
        for index, item in enumerate(vocabulary):
            if isinstance(item, VocabularyItem):
                data["vocabulary"].append(item)
                continue
            migrated = dict(item)
            migrated.setdefault("level", levels[index % len(levels)].value)
            data["vocabulary"].append(VocabularyItem(**migrated))
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
    REVIEW_START_MS = 2_800
    REVIEW_GAP_MS = 280
    REVIEW_AFTER_MS = 500
    SCORE_MS = 3_000

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
        scenes.append(MinigameScene("score", cursor, cursor + self.SCORE_MS))
        cursor += self.SCORE_MS
        scenes.append(MinigameScene("review_start", cursor, cursor + self.REVIEW_START_MS))
        cursor += self.REVIEW_START_MS
        for index in range(len(items)):
            normal = word_audio[index]
            example = example_audio[index]
            start = cursor
            word_start = start + self.REVIEW_LEAD_MS
            example_start = word_start + durations[normal] + self.REVIEW_GAP_MS
            cursor = example_start + durations[example] + self.REVIEW_AFTER_MS
            scenes.append(MinigameScene("review", start, cursor, index))
            audio_events.extend([(word_start, normal), (example_start, example)])
        return MinigameTimeline(scenes, audio_events, cursor)


def score_ranges(
    items: list[VocabularyItem],
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Build cumulative score bands from Easy, Medium and Hard word counts."""
    if not items:
        raise ValueError("Danh sách từ phải có ít nhất một phần tử.")
    counts = [sum(item.level == level.value for item in items) for level in VocabularyLevel]
    easy, medium, _hard = counts
    return (0, easy), (easy + 1, easy + medium), (easy + medium + 1, len(items))


def format_score_range(score_range: tuple[int, int], total: int) -> str:
    start, end = score_range
    if start > end:
        return "— correct"
    if start == end:
        return f"{start}/{total} correct"
    return f"{start}–{end} correct"
