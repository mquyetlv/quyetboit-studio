from __future__ import annotations

import json
import unicodedata
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
class DigitAtlasSpec:
    """Grid and optical-layout contract for a theme's 0-9 sprite atlas."""

    columns: int = 5
    rows: int = 2
    glyph_count: int = 10
    alpha_threshold: int = 16
    spacing_ratio: float = 0.06
    padding_px: int = 4
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
    start_base: Path
    background_video: Path
    background_text: Path
    score_screen: Path
    review_start_screen: Path
    review_start_base: Path
    review_screen: Path
    cta_screen: Path
    digit_atlas: Path
    digit_atlas_spec: DigitAtlasSpec
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
            "vietnamese",
            "english",
            "ipa",
            "wordType",
            "workType",
            "example",
            "exampleVietnamese",
            "level",
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
                    "Y tá",
                    "Nurse",
                    "/nɜːrs/",
                    "The nurse helps the doctor.",
                    "Noun",
                    "Y tá hỗ trợ bác sĩ.",
                    "Trung bình",
                ),
                VocabularyItem(
                    "Bệnh nhân",
                    "Patient",
                    "/ˈpeɪ.ʃənt/",
                    "The patient is waiting.",
                    "Noun",
                    "Bệnh nhân đang chờ.",
                    "Khó",
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


class MinigameAudioRole(StrEnum):
    VOICE = "voice"
    EFFECT = "effect"


@dataclass(slots=True, frozen=True)
class MinigameAudioEvent:
    """A timeline event with an explicit semantic cue and mix role.

    Iteration intentionally preserves the former ``(start_ms, path)`` shape for
    integrations that only need a placed PCM file.
    """

    start_ms: int
    path: Path
    semantic: str = ""
    role: MinigameAudioRole = MinigameAudioRole.VOICE

    def __iter__(self):
        yield self.start_ms
        yield self.path


@dataclass(slots=True, frozen=True)
class MinigameTimingProfile:
    """Single source of truth for mini-game pacing (all values are milliseconds)."""

    hook_ms: int = 3_200
    question_intro_ms: int = 700
    countdown_ms: int = 3_750
    post_reveal_sfx_gap_ms: int = 450
    between_listen_slow_ms: int = 700
    between_slow_repeat_ms: int = 900
    after_repeat_ms: int = 1_200
    score_ms: int = 6_000
    review_start_ms: int = 3_200
    review_lead_ms: int = 650
    review_word_to_example_ms: int = 900
    review_after_example_min_ms: int = 1_600
    review_after_example_max_ms: int = 3_500
    cta_ms: int = 6_500
    reading_chars_per_second: float = 15.0
    version: int = 5

    def __post_init__(self) -> None:
        numeric = (
            self.hook_ms,
            self.question_intro_ms,
            self.countdown_ms,
            self.post_reveal_sfx_gap_ms,
            self.between_listen_slow_ms,
            self.between_slow_repeat_ms,
            self.after_repeat_ms,
            self.score_ms,
            self.review_start_ms,
            self.review_lead_ms,
            self.review_word_to_example_ms,
            self.review_after_example_min_ms,
            self.review_after_example_max_ms,
            self.cta_ms,
        )
        if any(value <= 0 for value in numeric) or self.reading_chars_per_second <= 0:
            raise ValueError("Các giá trị timing minigame phải lớn hơn 0.")
        if self.review_after_example_min_ms > self.review_after_example_max_ms:
            raise ValueError("Review hold tối thiểu không được lớn hơn tối đa.")

    def review_hold_ms(self, item: VocabularyItem, example_audio_ms: int) -> int:
        text = f"{item.example} {item.example_vietnamese}".strip()
        reading_ms = round(len(text) / self.reading_chars_per_second * 1_000)
        return max(
            self.review_after_example_min_ms,
            min(self.review_after_example_max_ms, reading_ms - example_audio_ms),
        )


@dataclass(slots=True)
class MinigameTimeline:
    scenes: list[MinigameScene]
    audio_events: list[MinigameAudioEvent]
    duration_ms: int


class MinigameTimelineBuilder:
    """Build visual/audio boundaries from :class:`MinigameTimingProfile`."""

    # Compatibility aliases for consumers of the original public constants.
    HOOK_MS = MinigameTimingProfile().hook_ms
    QUESTION_MS = MinigameTimingProfile().question_intro_ms + MinigameTimingProfile().countdown_ms
    SCORE_MS = MinigameTimingProfile().score_ms
    REVIEW_START_MS = MinigameTimingProfile().review_start_ms
    CTA_MS = MinigameTimingProfile().cta_ms

    def __init__(self, timing: MinigameTimingProfile | None = None) -> None:
        self.timing = timing or MinigameTimingProfile()

    def build(
        self,
        items: list[VocabularyItem],
        word_audio: list[Path],
        slow_audio: list[Path],
        example_audio: list[Path],
        durations: dict[Path, int],
        *,
        answer_reveal_duration_ms: int = 0,
    ) -> MinigameTimeline:
        if not (len(items) == len(word_audio) == len(slow_audio) == len(example_audio)):
            raise ValueError("Số từ và số audio minigame không khớp.")
        timing = self.timing
        scenes = [MinigameScene("hook", 0, timing.hook_ms)]
        audio_events: list[MinigameAudioEvent] = []
        cursor = timing.hook_ms
        for index in range(len(items)):
            question_start = cursor
            countdown_start = question_start + timing.question_intro_ms
            reveal = countdown_start + timing.countdown_ms
            scenes.append(MinigameScene("word", question_start, reveal, index, "question"))
            scenes.append(
                MinigameScene("boundary", question_start, question_start, index, "question_start")
            )
            scenes.append(
                MinigameScene("boundary", countdown_start, countdown_start, index, "countdown_bed")
            )
            scenes.append(MinigameScene("boundary", reveal, reveal, index, "answer_reveal"))
            normal = word_audio[index]
            slow = slow_audio[index]
            # The effect duration is inserted by the pipeline; this named gap is
            # retained here so there are no magic voice offsets in the pipeline.
            normal_start = reveal + answer_reveal_duration_ms + timing.post_reveal_sfx_gap_ms
            slow_start = normal_start + durations[normal] + timing.between_listen_slow_ms
            final_start = slow_start + durations[slow] + timing.between_slow_repeat_ms
            cursor = final_start + durations[normal] + timing.after_repeat_ms
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
                [
                    MinigameAudioEvent(normal_start, normal, "voice_listen"),
                    MinigameAudioEvent(slow_start, slow, "voice_slow"),
                    MinigameAudioEvent(final_start, normal, "voice_repeat"),
                ]
            )
        scenes.append(MinigameScene("score", cursor, cursor + timing.score_ms))
        scenes.append(MinigameScene("boundary", cursor + 150, cursor + 150, -1, "score_win"))
        cursor += timing.score_ms
        scenes.append(MinigameScene("review_start", cursor, cursor + timing.review_start_ms))
        cursor += timing.review_start_ms
        for index in range(len(items)):
            normal = word_audio[index]
            example = example_audio[index]
            start = cursor
            word_start = start + timing.review_lead_ms
            example_start = word_start + durations[normal] + timing.review_word_to_example_ms
            cursor = (
                example_start
                + durations[example]
                + timing.review_hold_ms(items[index], durations[example])
            )
            scenes.append(MinigameScene("review", start, cursor, index))
            audio_events.extend(
                [
                    MinigameAudioEvent(word_start, normal, "review_word"),
                    MinigameAudioEvent(example_start, example, "review_example"),
                ]
            )
        scenes.append(MinigameScene("cta", cursor, cursor + timing.cta_ms))
        cursor += timing.cta_ms
        return MinigameTimeline(scenes, audio_events, cursor)


def answer_placeholder(english: str) -> str:
    """Hide each Unicode grapheme while preserving word-boundary separators."""
    clusters: list[str] = []
    join_next = False
    regional_count = 0
    for char in unicodedata.normalize("NFC", english):
        codepoint = ord(char)
        is_modifier = (
            unicodedata.combining(char) != 0
            or 0xFE00 <= codepoint <= 0xFE0F
            or 0x1F3FB <= codepoint <= 0x1F3FF
        )
        is_regional = 0x1F1E6 <= codepoint <= 0x1F1FF
        if char == "\u200d":
            if clusters:
                clusters[-1] += char
            join_next = True
        elif clusters and (is_modifier or join_next):
            clusters[-1] += char
            join_next = False
        elif is_regional and regional_count % 2 == 1 and clusters:
            clusters[-1] += char
            regional_count += 1
        else:
            clusters.append(char)
            regional_count = regional_count + 1 if is_regional else 0
    separators = {"-", "'", "’"}
    return "".join(cluster if cluster.isspace() or cluster in separators else "_" for cluster in clusters)


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
