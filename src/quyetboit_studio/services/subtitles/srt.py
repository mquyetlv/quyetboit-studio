from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher
from pathlib import Path

from ...core.models import SrtCue

TIMESTAMP = re.compile(
    r"(?P<h>\d{1,3}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{3})"
)
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def parse_timestamp(value: str) -> int:
    match = TIMESTAMP.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Timestamp SRT không hợp lệ: {value!r}")
    parts = {key: int(item) for key, item in match.groupdict().items()}
    if parts["m"] > 59 or parts["s"] > 59:
        raise ValueError(f"Timestamp SRT không hợp lệ: {value!r}")
    return ((parts["h"] * 60 + parts["m"]) * 60 + parts["s"]) * 1000 + parts["ms"]


def format_timestamp(milliseconds: int) -> str:
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def parse_srt(content: str | bytes) -> list[SrtCue]:
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        return []
    cues: list[SrtCue] = []
    for position, block in enumerate(re.split(r"\n{2,}", content), start=1):
        lines = block.splitlines()
        if len(lines) < 2:
            continue
        try:
            cue_id = int(lines[0].strip())
            timing_index = 1
        except ValueError:
            cue_id = position
            timing_index = 0
        if timing_index >= len(lines) or "-->" not in lines[timing_index]:
            raise ValueError(f"Cue {cue_id} thiếu dòng timestamp.")
        start, end = (part.strip().split(" ", 1)[0] for part in lines[timing_index].split("-->", 1))
        text = "\n".join(lines[timing_index + 1 :]).strip()
        cue = SrtCue(cue_id, parse_timestamp(start), parse_timestamp(end), text).normalized()
        if text:
            cues.append(cue)
    return cues


def serialize_srt(cues: Iterable[SrtCue], *, translated: bool = False, bom: bool = False) -> str:
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        cue.normalized()
        text = cue.translated_text if translated else cue.text
        blocks.append(
            f"{index}\n{format_timestamp(cue.start_ms)} --> {format_timestamp(cue.end_ms)}\n{text.strip()}"
        )
    result = "\n\n".join(blocks) + ("\n" if blocks else "")
    return ("\ufeff" + result) if bom else result


def write_srt(path: str | Path, cues: Iterable[SrtCue], *, translated: bool = False) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".partial")
    temporary.write_text(serialize_srt(cues, translated=translated, bom=True), encoding="utf-8")
    temporary.replace(target)
    return target


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def _duplicate(left: SrtCue, right: SrtCue) -> bool:
    overlap = max(0, min(left.end_ms, right.end_ms) - max(left.start_ms, right.start_ms))
    shortest = max(1, min(left.duration_ms, right.duration_ms))
    if overlap / shortest < 0.25:
        return False
    a, b = normalize_text(left.text), normalize_text(right.text)
    return bool(a and b and SequenceMatcher(None, a, b).ratio() >= 0.86)


def merge_chunk_cues(chunks: Iterable[tuple[int, Iterable[SrtCue]]]) -> list[SrtCue]:
    merged: list[SrtCue] = []
    for offset_ms, cues in chunks:
        for cue in cues:
            candidate = SrtCue(
                0,
                cue.start_ms + offset_ms,
                cue.end_ms + offset_ms,
                cue.text,
                cue.translated_text,
                list(cue.warnings),
            ).normalized()
            if not any(_duplicate(existing, candidate) for existing in merged[-8:]):
                merged.append(candidate)
    merged.sort(key=lambda cue: (cue.start_ms, cue.end_ms))
    for index, cue in enumerate(merged, start=1):
        cue.id = index
    return merged


def batch_cues(cues: Sequence[SrtCue], size: int = 50, context: int = 2) -> list[dict]:
    if not 10 <= size <= 200:
        raise ValueError("Batch size phải trong khoảng 10–200.")
    batches = []
    for start in range(0, len(cues), size):
        end = min(len(cues), start + size)
        batches.append({
            "items": list(cues[start:end]),
            "context_before": list(cues[max(0, start - context):start]),
            "context_after": list(cues[end:min(len(cues), end + context)]),
        })
    return batches


def find_untranslated(cues: Iterable[SrtCue], *, han_ratio: float = 0.25) -> list[SrtCue]:
    invalid: list[SrtCue] = []
    seen: dict[str, int] = {}
    for cue in cues:
        translated = cue.translated_text.strip()
        normalized = normalize_text(translated)
        source = normalize_text(cue.text)
        han_count = len(HAN.findall(translated))
        visible_count = sum(not char.isspace() for char in translated)
        repeated = normalized and seen.get(normalized, 0) >= 2
        if not translated or normalized == source or han_count / max(1, visible_count) > han_ratio or repeated:
            invalid.append(cue)
        if normalized:
            seen[normalized] = seen.get(normalized, 0) + 1
    return invalid
