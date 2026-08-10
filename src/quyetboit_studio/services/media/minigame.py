from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np

from ...core.minigame import MinigameProject, MinigameScene, MinigameTimeline, score_ranges
from ...core.models import (
    AudioArtifact,
    CancellationToken,
    PipelineError,
    ProgressCallback,
    VideoArtifact,
)
from .ffmpeg import FFmpegRunner, atempo_chain


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


class MinigameAudioService:
    def __init__(self, runner: FFmpegRunner | None = None) -> None:
        self.runner = runner or FFmpegRunner()

    def normalize(
        self, source: Path, output: Path, *, cancel: CancellationToken | None = None
    ) -> AudioArtifact:
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        self.runner.run(
            ["-i", str(source), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(partial)],
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
        if output.is_file():
            return self.runner.probe.audio_artifact(output).refresh()
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        self.runner.run(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1046:duration=0.18:sample_rate=48000",
                "-filter:a",
                "afade=t=out:st=0.08:d=0.10,volume=0.35",
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

    @staticmethod
    def assemble(
        events: list[tuple[int, Path]], output: Path, duration_ms: int
    ) -> AudioArtifact:
        rate = 48_000
        timeline = np.zeros(max(1, round(duration_ms * rate / 1000)), dtype=np.float32)
        for start_ms, path in events:
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
                    np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32)
                    / 32768
                )
            offset = round(start_ms * rate / 1000)
            available = min(len(samples), len(timeline) - offset)
            if available > 0:
                timeline[offset : offset + available] += samples[:available]
        peak = float(np.max(np.abs(timeline), initial=0))
        if peak > 0.96:
            timeline *= 0.96 / peak
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        with wave.open(str(partial), "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(rate)
            destination.writeframes((timeline * 32767).astype("<i2").tobytes())
        os.replace(partial, output)
        return AudioArtifact(output, duration_ms=duration_ms, sample_rate=rate, channels=1).refresh()


class MinigameSubtitleBuilder:
    def build(
        self, project: MinigameProject, timeline: MinigameTimeline, output: Path, aspect: str
    ) -> Path:
        width, height = (1920, 1080) if aspect == "16:9" else (1080, 1920)
        title_size = 72 if aspect == "16:9" else 86
        body_size = 52 if aspect == "16:9" else 64
        small_size = 34 if aspect == "16:9" else 43
        margin_v = 100 if aspect == "16:9" else 210
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Title,Arial,{title_size},&H00FFFFFF,&H000000FF,&H00181733,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,8,70,70,{margin_v},1
Style: Body,Arial,{body_size},&H00FFFFFF,&H000000FF,&H00181733,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,5,90,90,0,1
Style: Accent,Arial,{title_size},&H004FE8FF,&H000000FF,&H00181733,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,5,80,80,0,1
Style: Small,Arial,{small_size},&H00E8E7FF,&H000000FF,&H00181733,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events: list[str] = []
        total = len(project.vocabulary)

        def add(scene: MinigameScene, style: str, text: str, *, start: int | None = None, end: int | None = None) -> None:
            begin = scene.start_ms if start is None else start
            finish = scene.end_ms if end is None else end
            effect = r"{\fad(180,160)\t(0,220,\fscx105\fscy105)}"
            events.append(
                f"Dialogue: 0,{_ass_time(begin)},{_ass_time(finish)},{style},,0,0,0,,"
                f"{effect}{_ass_text(text)}"
            )

        for scene in timeline.scenes:
            if scene.kind == "hook":
                add(scene, "Title", f"CAN YOU GET {total}/{total}?")
                add(scene, "Small", f"Topic: {project.topic}")
            elif scene.kind == "word" and scene.phase == "question":
                item = project.vocabulary[scene.item_index]
                add(scene, "Title", f"QUESTION {scene.item_index + 1}/{total}")
                add(scene, "Body", item.vietnamese)
                for offset, number in enumerate((3, 2, 1)):
                    add(
                        scene,
                        "Small",
                        str(number),
                        start=scene.start_ms + offset * 1_000,
                        end=min(scene.end_ms, scene.start_ms + (offset + 1) * 1_000),
                    )
            elif scene.kind == "word":
                item = project.vocabulary[scene.item_index]
                add(scene, "Accent", item.english)
                add(scene, "Small", f"{item.ipa}  •  NORMAL  •  SLOW  •  NATURAL")
            elif scene.kind == "review":
                item = project.vocabulary[scene.item_index]
                add(scene, "Title", f"REVIEW {scene.item_index + 1}/{total}")
                add(scene, "Accent", f"{item.english}\n{item.ipa}")
                add(scene, "Small", f"{item.vietnamese}\n{item.example}")
            elif scene.kind == "score":
                beginner, good, perfect = score_ranges(total)
                add(scene, "Title", "HOW DID YOU DO?")
                lines = [f"{beginner[0]}–{beginner[1]}  BEGINNER  🌱"]
                if good[0] <= good[1]:
                    lines.append(f"{good[0]}–{good[1]}  GOOD JOB  🔥")
                lines.append(f"{perfect[0]}/{total}  PERFECT  🏆")
                add(
                    scene,
                    "Body",
                    "\n".join(lines),
                )
            elif scene.kind == "cta":
                add(scene, "Accent", "COMMENT YOUR SCORE!")
                add(scene, "Small", f"Did you get {total}/{total}?  •  Challenge a friend!")
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_suffix(output.suffix + ".partial")
        partial.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
        os.replace(partial, output)
        return output


class MinigameVideoRenderer:
    def __init__(self, runner: FFmpegRunner | None = None) -> None:
        self.runner = runner or FFmpegRunner()

    @staticmethod
    def _escape_filter_path(path: Path) -> str:
        return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

    def render(
        self,
        background: VideoArtifact,
        audio: AudioArtifact,
        subtitles: Path,
        output: Path,
        aspect: str,
        focus: list[float],
        *,
        progress: ProgressCallback | None = None,
        cancel: CancellationToken | None = None,
    ) -> VideoArtifact:
        width, height = (1920, 1080) if aspect == "16:9" else (1080, 1920)
        focus_x = max(0.0, min(1.0, float(focus[0] if focus else 0.5)))
        focus_y = max(0.0, min(1.0, float(focus[1] if len(focus) > 1 else 0.5)))
        panel_x = round(width * 0.07)
        panel_y = round(height * (0.13 if aspect == "16:9" else 0.16))
        panel_w = width - panel_x * 2
        panel_h = round(height * (0.72 if aspect == "16:9" else 0.68))
        filter_path = self._escape_filter_path(subtitles)
        graph = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:(in_w-out_w)*{focus_x:.4f}:(in_h-out_h)*{focus_y:.4f},"
            "eq=brightness=-0.10:saturation=0.88,"
            f"drawbox=x={panel_x}:y={panel_y}:w={panel_w}:h={panel_h}:"
            "color=0x17152f@0.88:t=fill,"
            f"drawbox=x={panel_x}:y={panel_y}:w={panel_w}:h={panel_h}:"
            "color=0x8b82ff@0.65:t=4,"
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
