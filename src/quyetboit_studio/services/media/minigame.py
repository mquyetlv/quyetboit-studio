from __future__ import annotations

import hashlib
import os
import textwrap
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import numpy as np

from ...core.minigame import (
    MinigameAudioEvent,
    MinigameAudioRole,
    MinigameProject,
    MinigameScene,
    MinigameThemeManifest,
    MinigameTimeline,
    answer_placeholder,
    format_score_range,
    score_ranges,
)
from ...core.models import (
    AudioArtifact,
    CancellationToken,
    PipelineError,
    ProgressCallback,
    VideoArtifact,
)
from .ffmpeg import FFmpegRunner, atempo_chain
from .minigame_text import MinigameTextLayerService


@dataclass(slots=True, frozen=True)
class MinigameVisualScene:
    background: Path
    duration_ms: int
    overlays: tuple[Path, ...] = ()
    fade_in_ms: int = 0
    background_is_video: bool = False
    crop_focus: tuple[float, float] = (0.5, 0.5)
    background_start_ms: int = 0


@dataclass(slots=True, frozen=True)
class MinigameAudioCue:
    semantic: str
    relative_path: str
    gain_db: float
    fade_ms: int = 35
    is_background_bed: bool = False


class MinigameAudioCueManifest:
    """Bundled production cues. Missing assets are a recoverable preflight error."""

    PROCESSING_VERSION: ClassVar[int] = 2
    CUES: ClassVar[dict[str, MinigameAudioCue]] = {
        "question_start": MinigameAudioCue("question_start", "sound-effect/check-mark.mp3", -8.0),
        "countdown_bed": MinigameAudioCue("countdown_bed", "sound-effect/clock-tick.mp3", -7.0),
        "answer_reveal": MinigameAudioCue(
            "answer_reveal", "sound-effect/hoan-thanh/Happy Win Game.mp3", -6.0
        ),
        "score_win": MinigameAudioCue(
            "score_win", "sound-effect/hoan-thanh/Cartoon Big Win.mp3", -6.0
        ),
        # This music is deliberately restricted to silent transition scenes. It
        # never sits underneath TTS, so speech intelligibility is unaffected.
        "hook_bed": MinigameAudioCue(
            "hook_bed", "sound-effect/intro/Be Happy (Teaser) (Kiểm tra bản quyền).mp3",
            -18.0, 450, True,
        ),
        "review_start_bed": MinigameAudioCue(
            "review_start_bed", "sound-effect/intro/Be Happy (Teaser) (Kiểm tra bản quyền).mp3",
            -18.0, 450, True,
        ),
        "cta_bed": MinigameAudioCue(
            "cta_bed", "sound-effect/intro/Be Happy (Teaser) (Kiểm tra bản quyền).mp3",
            -18.0, 180, True,
        ),
    }

    def __init__(self, assets_root: Path | None = None) -> None:
        self.assets_root = assets_root or Path(__file__).resolve().parents[2] / "assets" / "audio"

    def source(self, semantic: str) -> Path:
        try:
            cue = self.CUES[semantic]
        except KeyError as exc:
            raise ValueError(f"Cue minigame không hợp lệ: {semantic}") from exc
        source = self.assets_root / cue.relative_path
        if not source.is_file():
            raise PipelineError(
                "minigame_audio_asset_missing",
                f"Thiếu asset âm thanh '{cue.relative_path}'. Hãy cài lại ứng dụng.",
            )
        return source


def _ass_time(milliseconds: int) -> str:
    centiseconds = max(0, milliseconds // 10)
    hours, rest = divmod(centiseconds, 360_000)
    minutes, rest = divmod(rest, 6_000)
    seconds, fraction = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"


def _ass_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\r", " ")
        .replace("\n", r"\N")
    )


def _ass_color(value: str, *, alpha: str = "00") -> str:
    red, green, blue = (
        value.removeprefix("#")[:2],
        value.removeprefix("#")[2:4],
        value.removeprefix("#")[4:6],
    )
    return f"&H{alpha}{blue}{green}{red}"


class MinigameAudioService:
    # Removing provider padding can expose a non-zero waveform boundary.  A
    # short ramp prevents that discontinuity from becoming an audible click
    # when the clip is placed directly on the assembled timeline.
    SPEECH_EDGE_FADE_SECONDS = 0.012

    def __init__(self, runner: FFmpegRunner | None = None) -> None:
        self.runner = runner or FFmpegRunner()

    def normalize(
        self, source: Path, output: Path, *, cancel: CancellationToken | None = None
    ) -> AudioArtifact:
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        self.runner.run(
            [
                "-i",
                str(source),
                "-vn",
                "-filter:a",
                (
                    "silenceremove=start_periods=1:start_duration=0.04:"
                    "start_threshold=-45dB:stop_periods=-1:stop_duration=0.10:"
                    "stop_threshold=-45dB,"
                    f"afade=t=in:st=0:d={self.SPEECH_EDGE_FADE_SECONDS},"
                    # Reverse makes this a duration-independent fade-out: it
                    # also works when providers return clips of different
                    # lengths, without needing to probe before filtering.
                    f"areverse,afade=t=in:st=0:d={self.SPEECH_EDGE_FADE_SECONDS},"
                    "areverse,apad=pad_dur=0.08"
                ),
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(partial),
            ],
            cancel=cancel,
        )
        artifact = self.runner.probe.audio_artifact(partial)
        os.replace(partial, output)
        artifact.path = output
        return artifact.refresh()

    def normalize_cue(
        self,
        manifest: MinigameAudioCueManifest,
        semantic: str,
        cache_dir: Path,
        *,
        duration_ms: int | None = None,
        cancel: CancellationToken | None = None,
    ) -> AudioArtifact:
        """Decode a packaged cue once, keyed by bytes/config rather than install path."""
        source = manifest.source(semantic)
        cue = manifest.CUES[semantic]
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        if cue.is_background_bed and (duration_ms is None or duration_ms <= cue.fade_ms * 2):
            raise ValueError("Nhạc nền minigame cần duration lớn hơn hai lần fade.")
        key = hashlib.sha256(
            f"{checksum}:{manifest.PROCESSING_VERSION}:{cue.gain_db}:{cue.fade_ms}:{duration_ms}".encode()
        ).hexdigest()[:20]
        output = cache_dir / f"{semantic}-{key}.wav"
        if output.is_file():
            try:
                return self.runner.probe.audio_artifact(output).refresh()
            except PipelineError:
                output.unlink(missing_ok=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        fade_seconds = cue.fade_ms / 1000
        trim = ""
        fade_out = ""
        if cue.is_background_bed:
            assert duration_ms is not None
            duration_seconds = duration_ms / 1000
            trim = f"atrim=duration={duration_seconds:.3f},"
            fade_out = (
                f",afade=t=out:st={duration_seconds - fade_seconds:.3f}:d={fade_seconds:.3f}"
            )
        self.runner.run(
            [
                "-i",
                str(source),
                "-vn",
                "-filter:a",
                (
                    f"aformat=channel_layouts=mono,aresample=48000,{trim}volume={cue.gain_db}dB,"
                    f"afade=t=in:st=0:d={fade_seconds}{fade_out}"
                ),
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(partial),
            ],
            cancel=cancel,
        )
        artifact = self.runner.probe.audio_artifact(partial)
        os.replace(partial, output)
        artifact.path = output
        return artifact.refresh()

    def slow(
        self,
        source: AudioArtifact,
        output: Path,
        rate: float,
        *,
        cancel: CancellationToken | None = None,
    ) -> AudioArtifact:
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        self.runner.run(
            [
                "-i",
                str(source.path),
                "-filter:a",
                atempo_chain(max(0.5, min(1.0, rate))),
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(partial),
            ],
            cancel=cancel,
        )
        artifact = self.runner.probe.audio_artifact(partial)
        os.replace(partial, output)
        artifact.path = output
        return artifact.refresh()

    def ding(self, output: Path, *, cancel: CancellationToken | None = None) -> AudioArtifact:
        return self.cue(output, "reveal", cancel=cancel)

    def cue(
        self,
        output: Path,
        kind: str,
        *,
        cancel: CancellationToken | None = None,
    ) -> AudioArtifact:
        if output.is_file():
            return self.runner.probe.audio_artifact(output).refresh()
        filters = {
            "tick": "sine=frequency=720:duration=0.09:sample_rate=48000",
            "reveal": "sine=frequency=1046:duration=0.18:sample_rate=48000",
            "open": "sine=frequency=880:duration=0.28:sample_rate=48000",
            "finish": "sine=frequency=1318:duration=0.34:sample_rate=48000",
        }
        if kind not in filters:
            raise ValueError(f"Cue âm thanh minigame không hợp lệ: {kind}")
        volume = "0.18" if kind == "tick" else "0.30"
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        self.runner.run(
            [
                "-f",
                "lavfi",
                "-i",
                filters[kind],
                "-filter:a",
                f"afade=t=out:st=0.05:d=0.12,volume={volume}",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(partial),
            ],
            cancel=cancel,
        )
        artifact = self.runner.probe.audio_artifact(partial)
        os.replace(partial, output)
        artifact.path = output
        return artifact.refresh()

    def master(
        self,
        source: AudioArtifact,
        output: Path,
        *,
        cancel: CancellationToken | None = None,
    ) -> AudioArtifact:
        """Apply a consistent social-video loudness target after assembling the timeline."""
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        self.runner.run(
            [
                "-i",
                str(source.path),
                "-filter:a",
                # AAC encoding may add a few tenths of a dB true peak, so keep
                # margin below the published -1.5 dBTP delivery ceiling.
                "loudnorm=I=-16:TP=-2.0:LRA=7",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(partial),
            ],
            cancel=cancel,
        )
        artifact = self.runner.probe.audio_artifact(partial)
        os.replace(partial, output)
        artifact.path = output
        return artifact.refresh()

    @staticmethod
    def assemble(events: list[MinigameAudioEvent], output: Path, duration_ms: int) -> AudioArtifact:
        rate = 48_000
        sample_count = max(1, round(duration_ms * rate / 1000))
        voice_bus = np.zeros(sample_count, dtype=np.float32)
        effect_bus = np.zeros(sample_count, dtype=np.float32)
        for event in events:
            start_ms, path = event
            with wave.open(str(path), "rb") as source:
                if (
                    source.getframerate() != rate
                    or source.getnchannels() != 1
                    or source.getsampwidth() != 2
                ):
                    raise PipelineError(
                        "minigame_audio_format",
                        "Audio minigame phải là PCM mono 48 kHz.",
                        provider="ffmpeg",
                    )
                samples = (
                    np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(
                        np.float32
                    )
                    / 32768
                )
            offset = round(start_ms * rate / 1000)
            available = min(len(samples), sample_count - offset)
            if available > 0:
                target = effect_bus if event.role is MinigameAudioRole.EFFECT else voice_bus
                target[offset : offset + available] += samples[:available]
        # Keep speech at its staged level: constrain only the effect bus first,
        # then apply a transparent soft limiter instead of peak-scaling everything.
        effect_bus = np.tanh(effect_bus)
        timeline = np.tanh(voice_bus + effect_bus * 0.78) * 0.96
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        with wave.open(str(partial), "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(rate)
            destination.writeframes((timeline * 32767).astype("<i2").tobytes())
        os.replace(partial, output)
        return AudioArtifact(
            output, duration_ms=duration_ms, sample_rate=rate, channels=1
        ).refresh()


class MinigameSubtitleBuilder:
    def __init__(self) -> None:
        self.text_layers = MinigameTextLayerService()

    def build(
        self,
        project: MinigameProject,
        timeline: MinigameTimeline,
        output: Path,
        aspect: str,
        manifest: MinigameThemeManifest | None = None,
    ) -> Path:
        if manifest is not None:
            return self._build_theme(project, timeline, output, manifest)
        width, height = (1920, 1080) if aspect == "16:9" else (1080, 1920)
        horizontal = aspect == "16:9"
        sizes = {
            "eyebrow": 30 if horizontal else 40,
            "hero": 78 if horizontal else 112,
            "title": 52 if horizontal else 70,
            "body": 40 if horizontal else 58,
            "ipa": 36 if horizontal else 52,
            "small": 28 if horizontal else 38,
            "countdown": 112 if horizontal else 168,
        }
        positions = (
            {
                "header": 150,
                "label": 330,
                "hero": 475,
                "ipa": 590,
                "translation": 700,
                "phase": 855,
                "countdown": 760,
                "example_label": 690,
                "example": 800,
                "footer": 910,
            }
            if horizontal
            else {
                "header": 285,
                "label": 550,
                "hero": 745,
                "ipa": 900,
                "translation": 1050,
                "phase": 1300,
                "countdown": 1240,
                "example_label": 1090,
                "example": 1260,
                "footer": 1510,
            }
        )
        margin = 190 if horizontal else 120
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Eyebrow,Segoe UI Semibold,{sizes["eyebrow"]},&H00D9C4B8,&H000000FF,&H00201813,&H80000000,-1,0,0,0,100,100,2,0,1,2,0,5,{margin},{margin},0,1
Style: Hero,Segoe UI Semibold,{sizes["hero"]},&H00C5F074,&H000000FF,&H00201813,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,5,{margin},{margin},0,1
Style: Title,Segoe UI Semibold,{sizes["title"]},&H00FFFFFF,&H000000FF,&H00201813,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,5,{margin},{margin},0,1
Style: Body,Segoe UI,{sizes["body"]},&H00FFFFFF,&H000000FF,&H00201813,&H80000000,0,0,0,0,100,100,0,0,1,2,1,5,{margin},{margin},0,1
Style: IPA,Segoe UI,{sizes["ipa"]},&H00D9C4B8,&H000000FF,&H00201813,&H80000000,0,-1,0,0,100,100,1,0,1,2,0,5,{margin},{margin},0,1
Style: Small,Segoe UI,{sizes["small"]},&H00D9C4B8,&H000000FF,&H00201813,&H80000000,0,0,0,0,100,100,0,0,1,2,0,5,{margin},{margin},0,1
Style: Pill,Segoe UI Semibold,{sizes["small"]},&H00FFFFFF,&H000000FF,&H003D2B16,&H803D2B16,-1,0,0,0,100,100,1,0,3,2,0,5,{margin},{margin},0,1
Style: Countdown,Bahnschrift,{sizes["countdown"]},&H0057C8FF,&H000000FF,&H00201813,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,5,{margin},{margin},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events: list[str] = []
        total = len(project.vocabulary)

        def add(
            scene: MinigameScene,
            style: str,
            text: str,
            *,
            y: int,
            start: int | None = None,
            end: int | None = None,
            animation: str = "soft",
            layer: int = 0,
        ) -> None:
            begin = scene.start_ms if start is None else start
            finish = scene.end_ms if end is None else end
            animation_tag = {
                "soft": r"\fad(140,120)",
                "pop": r"\fad(100,100)\fscx94\fscy94\t(0,220,\fscx100\fscy100)",
                "none": "",
            }[animation]
            effect = rf"{{\an5\pos({width // 2},{y}){animation_tag}}}"
            events.append(
                f"Dialogue: {layer},{_ass_time(begin)},{_ass_time(finish)},{style},,0,0,0,,"
                f"{effect}{_ass_text(text)}"
            )

        for scene in timeline.scenes:
            if scene.kind == "hook":
                add(scene, "Eyebrow", "ENGLISH QUICK CHALLENGE", y=positions["header"])
                add(
                    scene,
                    "Hero",
                    f"CAN YOU GET\n{total} OUT OF {total}?",
                    y=positions["hero"],
                    animation="pop",
                )
                add(scene, "Pill", f"TOPIC  •  {project.topic}", y=positions["translation"])
                add(scene, "Small", "Listen  •  Guess  •  Repeat", y=positions["phase"])
            elif scene.kind == "word" and scene.phase == "question":
                item = project.vocabulary[scene.item_index]
                add(
                    scene,
                    "Eyebrow",
                    f"QUESTION  {scene.item_index + 1} / {total}",
                    y=positions["header"],
                )
                add(scene, "Small", "WHAT IS THIS IN ENGLISH?", y=positions["label"])
                add(scene, "Title", item.vietnamese, y=positions["hero"], animation="pop")
                for offset, number in enumerate((3, 2, 1)):
                    add(
                        scene,
                        "Countdown",
                        str(number),
                        y=positions["countdown"],
                        start=scene.start_ms + offset * 1_000,
                        end=min(scene.end_ms, scene.start_ms + (offset + 1) * 1_000),
                        animation="pop",
                    )
            elif scene.kind == "word" and scene.phase == "answer":
                item = project.vocabulary[scene.item_index]
                add(
                    scene,
                    "Eyebrow",
                    f"ANSWER  {scene.item_index + 1} / {total}",
                    y=positions["header"],
                )
                add(scene, "Hero", item.english, y=positions["hero"], animation="pop")
                add(scene, "IPA", item.ipa, y=positions["ipa"])
                add(scene, "Pill", item.vietnamese, y=positions["translation"])
                if item.word_type:
                    add(scene, "Small", f"WORD TYPE  •  {item.word_type}", y=positions["phase"])
                add(scene, "Small", "Listen first, then repeat aloud", y=positions["footer"])
            elif scene.kind == "voice":
                labels = {
                    "listen": "1  •  LISTEN",
                    "slow": "2  •  SLOW",
                    "repeat": "3  •  YOUR TURN — REPEAT",
                }
                add(
                    scene,
                    "Pill",
                    labels[scene.phase],
                    y=positions["phase"],
                    animation="pop",
                    layer=2,
                )
            elif scene.kind == "review":
                item = project.vocabulary[scene.item_index]
                max_chars = 58 if horizontal else 34
                example = item.example
                if item.example_vietnamese:
                    example += f"\n{item.example_vietnamese}"
                wrapped_example = "\n".join(textwrap.wrap(example, width=max_chars))
                add(
                    scene,
                    "Eyebrow",
                    f"QUICK REVIEW  {scene.item_index + 1} / {total}",
                    y=positions["header"],
                )
                add(scene, "Hero", item.english, y=positions["hero"], animation="pop")
                add(scene, "IPA", item.ipa, y=positions["ipa"])
                add(scene, "Pill", item.vietnamese, y=positions["translation"])
                if item.word_type:
                    add(scene, "Small", item.word_type.upper(), y=positions["phase"])
                add(scene, "Eyebrow", "EXAMPLE", y=positions["example_label"])
                add(scene, "Body", wrapped_example, y=positions["example"])
            elif scene.kind == "score":
                beginner, good, perfect = score_ranges(project.vocabulary)
                add(scene, "Eyebrow", "CHALLENGE COMPLETE", y=positions["header"])
                add(scene, "Title", "HOW DID YOU DO?", y=positions["label"])
                lines = [format_score_range(beginner, total)]
                lines.append(format_score_range(good, total))
                lines.append(format_score_range(perfect, total))
                add(
                    scene,
                    "Body",
                    "\n".join(lines),
                    y=positions["hero"],
                    animation="pop",
                )
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_suffix(output.suffix + ".partial")
        partial.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
        os.replace(partial, output)
        return output

    def _build_theme(
        self,
        project: MinigameProject,
        timeline: MinigameTimeline,
        output: Path,
        manifest: MinigameThemeManifest,
    ) -> Path:
        style_lines = []
        for style in manifest.styles.values():
            style_lines.append(
                f"Style: {style.name},{style.font},{style.max_size},{_ass_color(style.color)},"
                f"&H000000FF,{_ass_color(style.outline_color)},"
                f"{_ass_color(style.shadow_color, alpha='80')},"
                f"{-1 if style.bold else 0},{-1 if style.italic else 0},0,0,100,100,"
                f"{style.spacing},0,1,{style.outline_width},{style.shadow_depth},5,"
                f"{style.margin},{style.margin},0,1"
            )
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {manifest.canvas.width}
PlayResY: {manifest.canvas.height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
{chr(10).join(style_lines)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events: list[str] = []
        total = len(project.vocabulary)

        def add(
            scene: MinigameScene,
            placement_name: str,
            text: str,
        ) -> None:
            placement = manifest.placements[placement_name]
            slot = manifest.slots[placement.slot]
            style = manifest.styles[placement.style]
            x1, y1, x2, y2 = slot.bounds
            center_y = (y1 + y2) // 2 if placement.y is None else placement.y
            size = self.text_layers.fit_size(
                text,
                slot.bounds,
                style.max_size,
                style.min_size,
                title=style.font == "Bebas Neue",
                max_lines=slot.max_lines,
                font_file=style.font_file,
                tracking=style.spacing,
                line_spacing=style.line_spacing,
            )
            wrapped = self.text_layers.wrap_text(
                text, slot.bounds, size, style.font_file, style.spacing
            )
            motion = ""
            if placement_name == "word_header":
                center_x = (x1 + x2) // 2
                motion = rf"\move({center_x},{center_y - 10},{center_x},{center_y},0,200)"
            elif placement_name == "english_word":
                # Answer reveal: fly upward while fading/scaling from zero. This
                # avoids a static-looking result without revealing the word early.
                center_x = (x1 + x2) // 2
                motion = (
                    rf"\move({center_x},{center_y + 28},{center_x},{center_y},0,240)"
                    r"\alpha&HFF&\fscx0\fscy0"
                    r"\t(0,240,\alpha&H00&\fscx100\fscy100)"
                )
            elif scene.kind == "review":
                # Keep the card background stable; only the new card content
                # glides in. Fading whole frames was the source of the visible
                # black flash between review words.
                center_x = (x1 + x2) // 2
                motion = rf"\move({center_x},{center_y + 20},{center_x},{center_y},0,200)"
            events.append(
                f"Dialogue: {placement.layer},{_ass_time(scene.start_ms)},{_ass_time(scene.end_ms)},"
                f"{style.name},,0,0,0,,{{\\an5\\pos({(x1 + x2) // 2},{center_y})\\fs{size}{motion}\\fad(120,100)}}"
                f"{_ass_text(wrapped)}"
            )

        def add_countdown_bar(scene: MinigameScene) -> None:
            """Draw the themed 30fps remaining-time bar over its artwork divider."""
            slot = manifest.slots["countdown_bar"]
            x, y, x2, y2 = slot.bounds
            width, height = x2 - x, y2 - y
            radius = min(height // 2, 12)

            def pill(width_px: int, height_px: int) -> str:
                edge = min(radius, width_px // 2)
                return (
                    f"m {edge} 0 l {width_px - edge} 0 {width_px} {edge} "
                    f"{width_px} {height_px - edge} {width_px - edge} {height_px} "
                    f"{edge} {height_px} 0 {height_px - edge} 0 {edge}"
                )

            # A translucent capsule and a restrained outline integrate the bar
            # with every palette while retaining a legible empty-time track.
            track = (
                rf"{{\an7\pos({x},{y})\p1\c{_ass_color(manifest.colors.outline, alpha='78')}"
                rf"\3c{_ass_color(manifest.colors.accent, alpha='70')}\bord2\blur1}}"
                f"{pill(width, height)}"
            )
            inner_height = max(8, height - 14)
            inner_y = y + (height - inner_height) // 2
            inner = (
                rf"{{\an7\pos({x + 7},{inner_y})\p1\c{_ass_color(manifest.colors.primary, alpha='8A')}}}"
                f"{pill(max(1, width - 14), inner_height)}"
            )
            events.extend(
                (
                    f"Dialogue: 1,{_ass_time(scene.start_ms)},{_ass_time(scene.end_ms)},Body,,0,0,0,,{track}",
                    f"Dialogue: 2,{_ass_time(scene.start_ms)},{_ass_time(scene.end_ms)},Body,,0,0,0,,{inner}",
                )
            )
            frame_ms = 1_000 / 30
            frame_count = max(1, round((scene.end_ms - scene.start_ms) / frame_ms))
            for frame in range(frame_count):
                begin = round(scene.start_ms + frame * frame_ms)
                end = min(scene.end_ms, round(scene.start_ms + (frame + 1) * frame_ms))
                fill = max(0, round((width - 14) * (1 - frame / frame_count)))
                if end <= begin or fill <= 0:
                    continue
                fill_shape = pill(fill, inner_height)
                glow = (
                    rf"{{\an7\pos({x + 7},{inner_y})\p1\c{_ass_color(manifest.colors.accent, alpha='A8')}"
                    rf"\bord5\blur6}}{fill_shape}"
                )
                shape = (
                    rf"{{\an7\pos({x + 7},{inner_y})\p1\c{_ass_color(manifest.colors.accent)}}}"
                    f"{fill_shape}"
                )
                events.extend(
                    (
                        f"Dialogue: 3,{_ass_time(begin)},{_ass_time(end)},Body,,0,0,0,,{glow}",
                        f"Dialogue: 4,{_ass_time(begin)},{_ass_time(end)},Body,,0,0,0,,{shape}",
                    )
                )

        for scene in timeline.scenes:
            if scene.kind == "hook":
                continue
            elif scene.kind == "word":
                item = project.vocabulary[scene.item_index]
                label = "QUESTION" if scene.phase == "question" else "ANSWER"
                add(scene, "word_header", f"{label} {scene.item_index + 1} / {total}")
                add(scene, "meaning_label", "VIETNAMESE MEANING")
                add(scene, "meaning_value", item.vietnamese)
                if scene.phase == "question":
                    add(scene, "question_english_label", "ENGLISH WORD")
                    add(
                        scene,
                        "question_english_placeholder",
                        answer_placeholder(item.english),
                    )
                    countdown_start = next(
                        (
                            boundary.start_ms
                            for boundary in timeline.scenes
                            if boundary.kind == "boundary"
                            and boundary.item_index == scene.item_index
                            and boundary.phase == "countdown_bed"
                        ),
                        scene.start_ms,
                    )
                    add_countdown_bar(MinigameScene("countdown", countdown_start, scene.end_ms))
                else:
                    add(scene, "english_label", "ENGLISH WORD")
                    add(scene, "english_word", item.english)
                    add(scene, "ipa", item.ipa)
                    if item.word_type:
                        add(scene, "answer_word_type", f"WORD TYPE • {item.word_type.upper()}")
            elif scene.kind == "voice":
                labels = {"listen": "1 • LISTEN", "slow": "2 • SLOW", "repeat": "3 • YOUR TURN"}
                helpers = {
                    "listen": "Listen first, then repeat aloud",
                    "slow": "Hear every sound clearly",
                    "repeat": "Say it out loud now",
                }
                add(scene, "voice_header", labels[scene.phase])
                add(scene, "voice_helper", helpers[scene.phase])
            elif scene.kind == "review":
                item = project.vocabulary[scene.item_index]
                add(scene, "review_meaning", item.vietnamese)
                add(scene, "review_word", item.english)
                add(scene, "review_ipa", item.ipa)
                if item.word_type:
                    add(scene, "review_word_type", item.word_type.upper())
                if item.example_vietnamese:
                    add(scene, "review_example_en", item.example)
                    add(scene, "review_example_vi", item.example_vietnamese)
                else:
                    add(scene, "review_example", item.example)
            elif scene.kind == "score":
                beginner, good, perfect = score_ranges(project.vocabulary)
                add(scene, "score_beginner", format_score_range(beginner, total))
                add(scene, "score_good", format_score_range(good, total))
                add(scene, "score_perfect", format_score_range(perfect, total))
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_suffix(output.suffix + ".partial")
        partial.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
        os.replace(partial, output)
        return output


class MinigameVideoRenderer:
    def __init__(self, runner: FFmpegRunner | None = None) -> None:
        self.runner = runner or FFmpegRunner()
        self.font_dir = Path(__file__).resolve().parents[2] / "assets" / "fonts"

    @staticmethod
    def _escape_filter_path(path: Path) -> str:
        return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

    def render(
        self,
        background: VideoArtifact | None,
        audio: AudioArtifact,
        subtitles: Path,
        output: Path,
        aspect: str,
        focus: list[float],
        *,
        progress: ProgressCallback | None = None,
        cancel: CancellationToken | None = None,
        theme_background: Path | None = None,
        theme_overlay: Path | None = None,
        start_background: Path | None = None,
        hook_text_overlay: Path | None = None,
        hook_duration_ms: int = 0,
        visual_scenes: list[MinigameVisualScene] | None = None,
    ) -> VideoArtifact:
        width, height = (1920, 1080) if aspect == "16:9" else (1080, 1920)
        if theme_background is not None:
            return self._render_theme(
                audio,
                subtitles,
                output,
                theme_background,
                theme_overlay,
                progress=progress,
                cancel=cancel,
                start_background=start_background,
                hook_text_overlay=hook_text_overlay,
                hook_duration_ms=hook_duration_ms,
                visual_scenes=visual_scenes,
            )
        focus_x = max(0.0, min(1.0, float(focus[0] if focus else 0.5)))
        if background is None:
            raise PipelineError("minigame_background_missing", "Thiếu video nền legacy.")
        focus_y = max(0.0, min(1.0, float(focus[1] if len(focus) > 1 else 0.5)))
        panel_x = round(width * (0.10 if aspect == "16:9" else 0.055))
        panel_y = round(height * 0.075)
        panel_w = width - panel_x * 2
        panel_h = round(height * (0.85 if aspect == "16:9" else 0.84))
        accent_h = max(8, round(height * 0.006))
        filter_path = self._escape_filter_path(subtitles)
        graph = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:(in_w-out_w)*{focus_x:.4f}:(in_h-out_h)*{focus_y:.4f},"
            "eq=brightness=-0.08:saturation=0.82,"
            f"drawbox=x=0:y=0:w={width}:h={height}:color=0x07101f@0.18:t=fill,"
            f"drawbox=x={panel_x}:y={panel_y}:w={panel_w}:h={panel_h}:"
            "color=0x0d1930@0.82:t=fill,"
            f"drawbox=x={panel_x}:y={panel_y}:w={panel_w}:h={panel_h}:"
            "color=0x74f0c5@0.72:t=3,"
            f"drawbox=x={panel_x}:y={panel_y}:w={panel_w}:h={accent_h}:"
            "color=0x74f0c5@0.95:t=fill,"
            f"subtitles='{filter_path}'[v]"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        preflight = self.runner.preflight()
        encoder = preflight["encoder"] or "libx264"
        args = [
            "-stream_loop",
            "-1",
            "-i",
            str(background.path),
            "-i",
            str(audio.path),
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-map",
            "1:a:0",
            "-t",
            f"{audio.duration_ms / 1000:.3f}",
            "-r",
            "30",
            "-c:v",
            encoder,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(partial),
        ]
        try:
            try:
                self.runner.run(
                    args,
                    duration_ms=audio.duration_ms,
                    progress=progress,
                    cancel=cancel,
                    step=0,
                )
            except PipelineError:
                if encoder == "libx264":
                    raise
                args[args.index(encoder)] = "libx264"
                self.runner.run(
                    args,
                    duration_ms=audio.duration_ms,
                    progress=progress,
                    cancel=cancel,
                    step=0,
                )
            info = self.runner.probe.inspect(partial)
            stream_types = {stream.get("codec_type") for stream in info.get("streams", [])}
            result = self.runner.probe.video_artifact(partial)
            if "audio" not in stream_types or result.width != width or result.height != height:
                raise PipelineError(
                    "minigame_output_invalid",
                    "Video minigame xuất ra không đúng resolution hoặc thiếu audio.",
                    provider="ffmpeg",
                )
            os.replace(partial, output)
            result.path = output
            return result.refresh()
        finally:
            partial.unlink(missing_ok=True)

    def _render_theme(
        self,
        audio: AudioArtifact,
        subtitles: Path,
        output: Path,
        background: Path,
        overlay: Path | None,
        *,
        progress: ProgressCallback | None,
        cancel: CancellationToken | None,
        start_background: Path | None,
        hook_text_overlay: Path | None,
        hook_duration_ms: int,
        visual_scenes: list[MinigameVisualScene] | None = None,
    ) -> VideoArtifact:
        if not background.is_file() or (
            visual_scenes is None and (overlay is None or not overlay.is_file())
        ):
            raise PipelineError(
                "theme_asset_missing", "Theme chưa có đủ background hoặc alpha mask."
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        filter_path = self._escape_filter_path(subtitles)
        font_dir = self._escape_filter_path(self.font_dir)
        scenes = visual_scenes or self._legacy_theme_scenes(
            audio.duration_ms,
            background,
            overlay,
            start_background,
            hook_text_overlay,
            hook_duration_ms,
        )
        if not scenes or sum(scene.duration_ms for scene in scenes) != audio.duration_ms:
            raise PipelineError(
                "theme_scene_invalid", "Timeline visual scene không khớp timeline audio."
            )
        encoder = self.runner.preflight()["encoder"] or "libx264"
        scene_paths: list[Path] = []
        try:
            for index, scene in enumerate(scenes):
                scene_path = output.with_name(f".{output.stem}.scene-{index}.mp4")
                scene_partial = output.with_name(f".{output.stem}.scene-{index}.partial.mp4")
                scene_paths.append(scene_path)
                if scene.background_is_video:
                    source = self.runner.probe.video_artifact(scene.background)
                    source_duration = max(1, source.duration_ms)
                    start_seconds = (scene.background_start_ms % source_duration) / 1_000
                    inputs = [
                        "-stream_loop",
                        "-1",
                        "-ss",
                        f"{start_seconds:.3f}",
                        "-i",
                        str(scene.background),
                    ]
                    focus_x = max(0.0, min(1.0, scene.crop_focus[0]))
                    focus_y = max(0.0, min(1.0, scene.crop_focus[1]))
                    background_filter = (
                        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920:(in_w-out_w)*{focus_x:.4f}:(in_h-out_h)*{focus_y:.4f},"
                        "setsar=1,format=rgba"
                    )
                else:
                    inputs = ["-loop", "1", "-framerate", "30", "-i", str(scene.background)]
                    background_filter = "[0:v]scale=1080:1920,format=rgba"
                fade = min(max(0, scene.fade_in_ms), scene.duration_ms) / 1000
                fade_filter = f",fade=t=in:st=0:d={fade:.3f}" if fade else ""
                graph_parts = [f"{background_filter}{fade_filter}[v0]"]
                current = "v0"
                for overlay_index, overlay_path in enumerate(scene.overlays, 1):
                    inputs.extend(["-loop", "1", "-framerate", "30", "-i", str(overlay_path)])
                    next_name = f"v{overlay_index}"
                    graph_parts.append(
                        f"[{overlay_index}:v]format=rgba[o{overlay_index}];"
                        f"[{current}][o{overlay_index}]overlay=0:0[{next_name}]"
                    )
                    current = next_name
                graph_parts.append(f"[{current}]format=yuv420p[v]")
                scene_args = [
                    *inputs,
                    "-filter_complex",
                    ";".join(graph_parts),
                    "-map",
                    "[v]",
                    "-t",
                    f"{scene.duration_ms / 1000:.3f}",
                    "-r",
                    "30",
                    "-an",
                    "-c:v",
                    encoder,
                    str(scene_partial),
                ]
                try:
                    self.runner.run(
                        scene_args, duration_ms=scene.duration_ms, cancel=cancel, step=0
                    )
                except PipelineError:
                    if encoder == "libx264":
                        raise
                    encoder = "libx264"
                    scene_args[scene_args.index("-c:v") + 1] = encoder
                    self.runner.run(
                        scene_args, duration_ms=scene.duration_ms, cancel=cancel, step=0
                    )
                artifact = self.runner.probe.video_artifact(scene_partial)
                if (artifact.width, artifact.height) != (1080, 1920) or abs(
                    artifact.duration_ms - scene.duration_ms
                ) > 80:
                    raise PipelineError(
                        "theme_scene_invalid",
                        "Visual scene không đúng resolution hoặc duration.",
                    )
                os.replace(scene_partial, scene_path)
            inputs = [value for path in scene_paths for value in ("-i", str(path))]
            concat_inputs = "".join(f"[{index}:v]" for index in range(len(scene_paths)))
            graph = (
                f"{concat_inputs}concat=n={len(scene_paths)}:v=1:a=0,"
                f"subtitles=filename='{filter_path}':fontsdir='{font_dir}',format=yuv420p[v]"
            )
            audio_index = len(scene_paths)
            args = [
                *inputs,
                "-i",
                str(audio.path),
                "-filter_complex",
                graph,
                "-map",
                "[v]",
                "-map",
                f"{audio_index}:a:0",
                "-t",
                f"{audio.duration_ms / 1000:.3f}",
                "-r",
                "30",
                "-c:v",
                encoder,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(partial),
            ]
            try:
                self.runner.run(
                    args, duration_ms=audio.duration_ms, progress=progress, cancel=cancel, step=0
                )
            except PipelineError:
                if encoder == "libx264":
                    raise
                args[args.index("-c:v") + 1] = "libx264"
                self.runner.run(
                    args, duration_ms=audio.duration_ms, progress=progress, cancel=cancel, step=0
                )
            info = self.runner.probe.inspect(partial)
            stream_types = {stream.get("codec_type") for stream in info.get("streams", [])}
            result = self.runner.probe.video_artifact(partial)
            if "audio" not in stream_types or (result.width, result.height) != (1080, 1920):
                raise PipelineError(
                    "minigame_output_invalid",
                    "Video theme không đúng khung 1080×1920 hoặc thiếu audio.",
                    provider="ffmpeg",
                )
            os.replace(partial, output)
            result.path = output
            return result.refresh()
        finally:
            partial.unlink(missing_ok=True)
            for index, scene_path in enumerate(scene_paths):
                scene_path.unlink(missing_ok=True)
                output.with_name(f".{output.stem}.scene-{index}.partial.mp4").unlink(
                    missing_ok=True
                )

    @staticmethod
    def _legacy_theme_scenes(
        duration_ms: int,
        background: Path,
        overlay: Path,
        start_background: Path | None,
        hook_text_overlay: Path | None,
        hook_duration_ms: int,
    ) -> list[MinigameVisualScene]:
        hook_duration = max(0, min(hook_duration_ms, duration_ms))
        scenes: list[MinigameVisualScene] = []
        if start_background is not None and start_background.is_file() and hook_duration:
            overlays = (
                (hook_text_overlay,)
                if hook_text_overlay is not None and hook_text_overlay.is_file()
                else ()
            )
            scenes.append(MinigameVisualScene(start_background, hook_duration, overlays))
        remaining = duration_ms - hook_duration
        if remaining:
            scenes.append(MinigameVisualScene(background, remaining, (overlay,)))
        return scenes
