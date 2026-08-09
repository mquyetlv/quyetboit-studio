from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import wave
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ...core.models import (
    Artifact,
    AudioArtifact,
    AudioChunk,
    CancellationToken,
    PipelineError,
    ProgressCallback,
    ProgressEvent,
    SrtCue,
    VideoArtifact,
)
from ...core.studio import normalize_studio_settings


def atempo_chain(factor: float) -> str:
    if factor <= 0:
        raise ValueError("Hệ số tốc độ phải lớn hơn 0.")
    filters: list[float] = []
    while factor > 2.0:
        filters.append(2.0)
        factor /= 2.0
    while factor < 0.5:
        filters.append(0.5)
        factor /= 0.5
    filters.append(factor)
    return ",".join(f"atempo={value:.8g}" for value in filters)


class MediaProbe:
    def __init__(self, ffprobe: str = "ffprobe") -> None:
        self.executable = ffprobe

    def inspect(self, path: str | Path) -> dict[str, Any]:
        command = [
            self.executable, "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            raise PipelineError(
                "media_probe_failed", "Không thể đọc file media bằng ffprobe.",
                provider="ffmpeg", debug_detail=str(exc),
            ) from exc

    @staticmethod
    def duration_ms(info: dict[str, Any]) -> int:
        value = info.get("format", {}).get("duration")
        if value is None:
            durations = [stream.get("duration") for stream in info.get("streams", [])]
            value = next((item for item in durations if item is not None), 0)
        return round(float(value or 0) * 1000)

    def video_artifact(self, path: str | Path) -> VideoArtifact:
        info = self.inspect(path)
        stream = next((item for item in info.get("streams", []) if item.get("codec_type") == "video"), None)
        if not stream or self.duration_ms(info) <= 0:
            raise PipelineError("invalid_video", "File tải về không có video hợp lệ.", provider="ffmpeg")
        numerator, _, denominator = str(stream.get("avg_frame_rate", "0/1")).partition("/")
        fps = float(numerator or 0) / max(1.0, float(denominator or 1))
        return VideoArtifact(
            Path(path), duration_ms=self.duration_ms(info), width=int(stream.get("width", 0)),
            height=int(stream.get("height", 0)), fps=fps,
        ).refresh()

    def audio_artifact(self, path: str | Path) -> AudioArtifact:
        info = self.inspect(path)
        stream = next((item for item in info.get("streams", []) if item.get("codec_type") == "audio"), None)
        if not stream or self.duration_ms(info) <= 0:
            raise PipelineError("invalid_audio", "File audio không hợp lệ.", provider="ffmpeg")
        return AudioArtifact(
            Path(path), duration_ms=self.duration_ms(info),
            sample_rate=int(stream.get("sample_rate", 0)), channels=int(stream.get("channels", 0)),
        ).refresh()


class FFmpegRunner:
    TIME = re.compile(r"out_time_ms=(\d+)")

    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.ffmpeg = ffmpeg
        self.probe = MediaProbe(ffprobe)

    def preflight(self) -> dict[str, Any]:
        if not shutil.which(self.ffmpeg) or not shutil.which(self.probe.executable):
            raise PipelineError(
                "ffmpeg_missing", "Không tìm thấy FFmpeg/ffprobe trong PATH.", provider="ffmpeg"
            )
        version = subprocess.run(
            [self.ffmpeg, "-version"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[0]
        encoders = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=True
        ).stdout
        filters = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, check=True
        ).stdout
        available = [name for name in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264") if name in encoders]
        return {
            "ffmpeg": shutil.which(self.ffmpeg), "ffprobe": shutil.which(self.probe.executable),
            "version": version, "encoders": available, "encoder": available[0] if available else "",
            "rubberband": "rubberband" in filters,
        }

    def run(
        self,
        arguments: Iterable[str],
        *,
        duration_ms: int = 0,
        progress: ProgressCallback | None = None,
        cancel: CancellationToken | None = None,
        step: int = 0,
    ) -> None:
        command = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", *map(str, arguments)]
        if progress:
            command[-1:-1] = ["-progress", "pipe:1", "-nostats"]
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE if progress else subprocess.DEVNULL,
            # FFmpeg can emit enough subtitle/font diagnostics to fill stderr's pipe and deadlock
            # while the caller is reading progress from stdout. Merge both streams and drain them.
            stderr=subprocess.STDOUT if progress else subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        diagnostics: deque[str] = deque(maxlen=120)
        last_reported_bucket = -1
        process_finished = threading.Event()

        def cancellation_watcher() -> None:
            while not process_finished.wait(0.1):
                if cancel and cancel.is_cancelled and process.poll() is None:
                    process.terminate()
                    return

        if cancel:
            threading.Thread(
                target=cancellation_watcher, name="ffmpeg-cancel", daemon=True
            ).start()
        try:
            if progress and process.stdout:
                for line in process.stdout:
                    if cancel and cancel.is_cancelled:
                        process.terminate()
                        break
                    match = self.TIME.match(line.strip())
                    if match and duration_ms:
                        # FFmpeg may expose microseconds despite the historical field name.
                        current_ms = int(match.group(1)) / 1000
                        fraction = min(1.0, current_ms / duration_ms)
                        bucket = int(fraction * 100) // 5
                        message = ""
                        if bucket != last_reported_bucket:
                            last_reported_bucket = bucket
                            message = f"FFmpeg — {round(fraction * 100)}%"
                        progress(ProgressEvent(step, fraction, message))
                    else:
                        diagnostics.append(line.rstrip())
            stdout, stderr = process.communicate()
            if cancel:
                cancel.raise_if_cancelled()
            if process.returncode:
                detail = "\n".join(diagnostics)[-4000:] or (stderr or stdout or "")[-4000:]
                raise PipelineError(
                    "ffmpeg_failed", "FFmpeg không thể xử lý media.", provider="ffmpeg",
                    retryable=False, debug_detail=detail,
                )
            if progress:
                progress(ProgressEvent(step, 1.0, "FFmpeg — 100%"))
        finally:
            process_finished.set()
            if process.poll() is None:
                process.kill()


class VideoConcatenator:
    """Normalize and concatenate downloaded videos into one validated source artifact."""

    def __init__(self, runner: FFmpegRunner | None = None) -> None:
        self.runner = runner or FFmpegRunner()

    def concatenate(
        self, videos: Iterable[VideoArtifact], output: str | Path, *,
        progress: ProgressCallback | None = None, cancel: CancellationToken | None = None,
    ) -> VideoArtifact:
        sources = list(videos)
        if not sources:
            raise PipelineError("concat_empty", "Không có video để gộp.", provider="ffmpeg")
        if len(sources) == 1:
            return sources[0]
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.stem}.partial{target.suffix}")
        script = target.parent / f".{target.stem}.concat-filters.txt"
        width = max(2, int(sources[0].width or 1920)) // 2 * 2
        height = max(2, int(sources[0].height or 1080)) // 2 * 2
        fps = max(1.0, float(sources[0].fps or 30))
        filters: list[str] = []
        labels: list[str] = []
        for index, video in enumerate(sources):
            filters.append(
                f"[{index}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
                f"fps={fps:.8g},format=yuv420p,setpts=PTS-STARTPTS[v{index}]"
            )
            info = self.runner.probe.inspect(video.path)
            has_audio = any(
                stream.get("codec_type") == "audio" for stream in info.get("streams", [])
            )
            if has_audio:
                filters.append(
                    f"[{index}:a:0]aresample=48000,"
                    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                    f"asetpts=PTS-STARTPTS[a{index}]"
                )
            else:
                filters.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=duration={video.duration_ms / 1000:.6f}"
                    f",asetpts=PTS-STARTPTS[a{index}]"
                )
            labels.append(f"[v{index}][a{index}]")
        filters.append(
            f"{''.join(labels)}concat=n={len(sources)}:v=1:a=1[vout][aout]"
        )
        script.write_text(";".join(filters), encoding="utf-8")
        preflight = self.runner.preflight()
        encoder = preflight.get("encoder") or "libx264"
        args: list[str] = []
        for video in sources:
            args += ["-i", str(video.path)]
        args += [
            "-filter_complex_script", str(script), "-map", "[vout]", "-map", "[aout]",
            "-c:v", encoder, "-c:a", "aac", "-movflags", "+faststart", str(partial),
        ]
        duration_ms = sum(video.duration_ms for video in sources)
        try:
            try:
                self.runner.run(
                    args, duration_ms=duration_ms, progress=progress, cancel=cancel, step=0,
                )
            except PipelineError:
                if encoder == "libx264":
                    raise
                args[args.index(encoder)] = "libx264"
                self.runner.run(
                    args, duration_ms=duration_ms, progress=progress, cancel=cancel, step=0,
                )
            result = self.runner.probe.video_artifact(partial)
            os.replace(partial, target)
            result.path = target
            result.metadata["source_count"] = len(sources)
            return result.refresh()
        finally:
            script.unlink(missing_ok=True)


@dataclass(slots=True, frozen=True)
class SplitConfig:
    target_seconds: int = 240
    min_seconds: int = 180
    max_seconds: int = 300
    search_seconds: int = 30
    overlap_ms: int = 250
    silence_db: int = -35
    silence_duration: float = 0.35


class AudioProcessor:
    SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)")

    def __init__(self, runner: FFmpegRunner | None = None) -> None:
        self.runner = runner or FFmpegRunner()

    def extract(
        self, video: Artifact, output: str | Path, *, progress: ProgressCallback | None = None,
        cancel: CancellationToken | None = None,
    ) -> AudioArtifact:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.stem}.partial{target.suffix}")
        self.runner.run(
            ["-i", str(video.path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "96k", str(partial)],
            duration_ms=int(video.metadata.get("duration_ms", 0)), progress=progress, cancel=cancel, step=1,
        )
        artifact = self.runner.probe.audio_artifact(partial)
        os.replace(partial, target)
        artifact.path = target
        return artifact.refresh()

    def silence_points(self, audio: str | Path, config: SplitConfig | None = None) -> list[int]:
        config = config or SplitConfig()
        command = [
            self.runner.ffmpeg, "-hide_banner", "-nostdin", "-i", str(audio), "-af",
            f"silencedetect=noise={config.silence_db}dB:d={config.silence_duration}", "-f", "null", "-",
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        return [round(float(value) * 1000) for value in self.SILENCE_END.findall(result.stderr)]

    @staticmethod
    def boundaries(duration_ms: int, silences: Iterable[int], config: SplitConfig | None = None) -> list[tuple[int, int]]:
        config = config or SplitConfig()
        if duration_ms <= config.max_seconds * 1000:
            return [(0, duration_ms)]
        points = sorted(value for value in silences if 0 < value < duration_ms)
        output: list[tuple[int, int]] = []
        start = 0
        target = config.target_seconds * 1000
        while duration_ms - start > config.max_seconds * 1000:
            ideal = start + target
            candidates = [
                point for point in points
                if start + config.min_seconds * 1000 <= point <= start + config.max_seconds * 1000
                and abs(point - ideal) <= config.search_seconds * 1000
            ]
            end = min(candidates, key=lambda point: abs(point - ideal)) if candidates else ideal
            output.append((start, end))
            start = end
        output.append((start, duration_ms))
        return output

    def split_on_silence(
        self, audio: AudioArtifact, directory: str | Path, config: SplitConfig | None = None,
        *, progress: ProgressCallback | None = None, cancel: CancellationToken | None = None,
    ) -> list[AudioChunk]:
        config = config or SplitConfig()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        chunks: list[AudioChunk] = []
        points = self.silence_points(audio.path, config)
        bounds = self.boundaries(audio.duration_ms, points, config)
        for index, (source_start, source_end) in enumerate(bounds):
            if cancel:
                cancel.raise_if_cancelled()
            start = max(0, source_start - (config.overlap_ms if index else 0))
            end = min(audio.duration_ms, source_end + (config.overlap_ms if index < len(bounds) - 1 else 0))
            target = directory / f"audio_{index + 1:03d}.m4a"
            partial = target.with_name(f".{target.stem}.partial{target.suffix}")
            self.runner.run([
                "-ss", f"{start / 1000:.3f}", "-to", f"{end / 1000:.3f}", "-i", str(audio.path),
                "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "96k", str(partial),
            ], cancel=cancel)
            probed = self.runner.probe.audio_artifact(partial)
            os.replace(partial, target)
            chunk = AudioChunk(
                path=target, duration_ms=probed.duration_ms, sample_rate=probed.sample_rate,
                channels=probed.channels, index=index, source_start_ms=start, source_end_ms=end,
            ).refresh()
            chunks.append(chunk)
            if progress:
                progress(ProgressEvent(1, source_end / max(1, audio.duration_ms), "Chia audio", item_id=str(index)))
        return chunks


class TimelineAudioAssembler:
    def assemble(
        self, cues: list[SrtCue], clips: list[AudioArtifact], output: str | Path,
        *, video_duration_ms: int,
    ) -> AudioArtifact:
        if len(cues) != len(clips):
            raise ValueError("Số cue và clip voice phải bằng nhau.")
        rate = 48_000
        timeline = np.zeros(math.ceil(video_duration_ms * rate / 1000), dtype=np.float32)
        for cue, clip in zip(cues, clips, strict=True):
            with wave.open(str(clip.path), "rb") as source:
                if source.getframerate() != rate or source.getnchannels() != 1 or source.getsampwidth() != 2:
                    raise PipelineError("invalid_voice_pcm", "Clip voice phải là PCM mono 48 kHz.", provider="ffmpeg")
                samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
            offset = round(cue.start_ms * rate / 1000)
            available = min(len(samples), len(timeline) - offset)
            if available > 0:
                timeline[offset:offset + available] += samples[:available]
        peak = float(np.max(np.abs(timeline), initial=0))
        if peak > 0.98:
            timeline *= 0.98 / peak
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.stem}.partial{target.suffix}")
        with wave.open(str(partial), "wb") as destination:
            destination.setnchannels(1); destination.setsampwidth(2); destination.setframerate(rate)
            destination.writeframes((timeline * 32767).astype("<i2").tobytes())
        os.replace(partial, target)
        return AudioArtifact(target, duration_ms=video_duration_ms, sample_rate=rate, channels=1).refresh()


@dataclass(slots=True)
class RenderGraph:
    script: str
    image_inputs: list[Path]
    video_output: str | None
    audio_output: str | None
    visual: bool


class FilterGraphBuilder:
    @staticmethod
    def escape_path(value: str) -> str:
        return (
            value.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            .replace("[", "\\[").replace("]", "\\]")
        )

    @staticmethod
    def escape_text(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("\r", " ").replace("\n", " ")
        for character in ("'", ":", "%", ",", ";", "[", "]"):
            escaped = escaped.replace(character, f"\\{character}")
        return escaped

    @staticmethod
    def _font_path(bold: bool = False) -> Path | None:
        filename = "arialbd.ttf" if bold else "arial.ttf"
        dejavu = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        candidates = (
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename,
            Path("/usr/share/fonts/truetype/dejavu") / dejavu,
        )
        return next((path.resolve() for path in candidates if path.exists()), None)

    @staticmethod
    def _font_family(font: Path | None) -> str:
        if font and font.stem.lower().startswith("dejavu"):
            return "DejaVu Sans"
        return "Arial"

    @staticmethod
    def _enable(layer: dict[str, Any]) -> str:
        start = max(0.0, float(layer.get("start", 0)))
        end = start + max(0.1, float(layer.get("duration", 5)))
        return f"between(t,{start:.6g},{end:.6g})"

    @staticmethod
    def _enable_option(layer: dict[str, Any]) -> str:
        if layer.get("full_duration", True):
            return ""
        return f":enable='{FilterGraphBuilder._enable(layer)}'"

    @staticmethod
    def _ffmpeg_color(value: str) -> str:
        value = value.strip() or "black"
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return f"0x{value[1:]}"
        return re.sub(r"[^#0-9a-zA-Z@._-]", "", value) or "black"

    @staticmethod
    def _ass_color(value: str) -> str:
        named = {
            "white": "FFFFFF", "black": "000000", "red": "FF0000",
            "green": "00FF00", "blue": "0000FF", "yellow": "FFFF00",
        }
        raw = value.strip().lstrip("#")
        rgb = raw if re.fullmatch(r"[0-9a-fA-F]{6}", raw) else named.get(raw.lower(), "FFFFFF")
        red, green, blue = rgb[0:2], rgb[2:4], rgb[4:6]
        return f"&H00{blue}{green}{red}"

    @staticmethod
    def _overlay_position(layer: dict[str, Any]) -> tuple[str, str]:
        position = str(layer.get("position", "top-right"))
        margin_x = max(0, int(layer.get("margin_x", layer.get("margin", 24))))
        margin_y = max(0, int(layer.get("margin_y", layer.get("margin", 24))))
        x = str(margin_x) if position.endswith("left") else f"W-w-{margin_x}"
        y = str(margin_y) if position.startswith("top") else f"H-h-{margin_y}"
        return x, y

    @staticmethod
    def _edge_box_y(layer: dict[str, Any], height: int) -> str:
        position = str(layer.get("position", "top"))
        margin = max(0, int(layer.get("margin", 24)))
        return str(margin) if position == "top" else f"ih-{height}-{margin}"

    def build(
        self, settings: dict[str, Any], *, subtitle: str | Path | None = None,
        rubberband: bool = False, frame_width: int = 1920, frame_height: int = 1080,
    ) -> RenderGraph:
        settings = normalize_studio_settings(settings)
        layers = [layer for layer in settings["layers"] if layer.get("enabled", True)]
        video_filters: list[str] = []
        timeline_speed = float(settings.get("speed", 1.0))
        zoom = max(0.5, min(2.0, float(settings.get("zoom", 100)) / 100))
        if zoom != 1:
            video_filters.append(
                f"scale=iw*{zoom:.8g}:ih*{zoom:.8g},crop=iw/{zoom:.8g}:ih/{zoom:.8g}"
            )
        if settings.get("flip_horizontal"):
            video_filters.append("hflip")
        if settings.get("flip_vertical"):
            video_filters.append("vflip")
        color = float(settings.get("color", 100)) / 100
        if color != 1:
            video_filters.append(f"eq=saturation={max(0, min(2, color)):.4g}")
        # Composite subtitles after spatial transforms so configured pixel size and edge margin
        # are not enlarged or cropped by Studio zoom. Keep them before setpts for cue timing.
        subtitle_layer = next((layer for layer in layers if layer.get("kind") == "subtitle"), None)
        if subtitle and subtitle_layer:
            font_size = int(subtitle_layer.get("font_size", 42))
            height = max(1, round(font_size * 1.5))
            margin = max(0, int(subtitle_layer.get("margin", 24)))
            box_y = self._edge_box_y(subtitle_layer, height)
            background_opacity = int(subtitle_layer.get("background_opacity", 60))
            if background_opacity > 0:
                background = self._ffmpeg_color(
                    str(subtitle_layer.get("background_color", "#000000"))
                )
                video_filters.append(
                    f"drawbox=x=0:y={box_y}:w=iw:h={height}:"
                    f"color={background}@{background_opacity / 100:.4g}:t=fill"
                )
            vertical_margin = margin + max(0, round((height - font_size) / 2))
            primary = self._ass_color(str(subtitle_layer.get("font_color", "white")))
            alignment = 8 if subtitle_layer.get("position") == "top" else 2
            # SRT is converted by libass with PlayResY=288. Convert requested pixels back
            # to that coordinate system so Subtitle matches drawtext-based fixed text.
            ass_scale = 288 / max(1, frame_height)
            ass_font_size = font_size * ass_scale
            ass_vertical_margin = vertical_margin * ass_scale
            font_name = self._font_family(self._font_path(False))
            video_filters.append(
                f"subtitles='{self.escape_path(str(Path(subtitle).resolve()))}':force_style='"
                f"FontName={font_name},FontSize={ass_font_size:.6g},PrimaryColour={primary},"
                f"BorderStyle=1,Outline=0,Shadow=0,Alignment={alignment},MarginL=0,MarginR=0,"
                f"MarginV={ass_vertical_margin:.6g}'"
            )
        for layer in layers:
            kind = layer.get("kind")
            if kind not in {"intro_text", "scroll_text"} or not str(layer.get("text", "")).strip():
                continue
            text = str(layer["text"]).strip()
            requested_size = int(layer.get("font_size", 48))
            # Keep even long text inside the frame; drawtext's fix_bounds provides the final guard.
            estimated_glyphs = max(1.0, len(text) * 0.62)
            font_size = max(1, min(requested_size, frame_height, int(frame_width / estimated_glyphs)))
            if kind == "scroll_text":
                scroll_speed = max(0.1, min(10.0, float(layer.get("speed", 1.0))))
                x_range = "max(1\\,w-text_w)"
                y_range = "max(1\\,h-text_h)"
                x = f"abs(mod(t*{120 * scroll_speed:.6g}\\,2*{x_range})-{x_range})"
                y = f"abs(mod(t*{83 * scroll_speed:.6g}\\,2*{y_range})-{y_range})"
                opacity = max(0, min(100, int(layer.get("opacity", 60)))) / 100
                color = self._ffmpeg_color(str(layer.get("font_color", "#FFFFFF")))
                font = self._font_path(bool(layer.get("bold", True)))
                shadow = "shadowx=2:shadowy=2:shadowcolor=black@0.65:"
                font_color = f"{color}@{opacity:.4g}"
            else:
                height = max(1, round(font_size * 1.5))
                box_y = self._edge_box_y(layer, height)
                x, y = "(w-text_w)/2", f"{box_y}+({height}-text_h)/2"
                background_opacity = int(layer.get("background_opacity", 60))
                if background_opacity > 0:
                    background = self._ffmpeg_color(str(layer.get("background_color", "#000000")))
                    video_filters.append(
                        f"drawbox=x=0:y={box_y}:w=iw:h={height}:"
                        f"color={background}@{background_opacity / 100:.4g}:t=fill"
                    )
                font = self._font_path(False)
                shadow = ""
                font_color = self._ffmpeg_color(str(layer.get("font_color", "#FFFFFF")))
            font_option = f"fontfile='{self.escape_path(str(font))}':" if font else ""
            video_filters.append(
                "drawtext="
                f"{font_option}text='{self.escape_text(text)}':"
                f"fontsize={font_size}:fontcolor={font_color}:{shadow}fix_bounds=1:"
                f"x={x}:y={y}"
            )
        if timeline_speed != 1:
            video_filters.append(f"setpts=PTS/{timeline_speed:.8g}")

        script_parts: list[str] = []
        image_inputs: list[Path] = []
        visual = bool(video_filters)
        image_layers = [layer for layer in layers if layer.get("kind") == "logo"]
        valid_images: list[tuple[dict[str, Any], Path]] = []
        for layer in image_layers:
            path = Path(str(layer.get("path", ""))).expanduser()
            if not path.is_file():
                raise PipelineError(
                    "studio_asset_missing",
                    f"Không tìm thấy file của layer {layer.get('name', 'ảnh')}: {path}",
                    provider="ffmpeg",
                )
            valid_images.append((layer, path.resolve()))
        if video_filters or valid_images:
            script_parts.append(f"[0:v]{','.join(video_filters) if video_filters else 'null'}[v0]")
            current = "v0"
            for index, (layer, path) in enumerate(valid_images, 1):
                image_inputs.append(path)
                opacity = max(0, min(100, int(layer.get("opacity", 100)))) / 100
                height = max(1, int(layer.get("height", 80)))
                raw, asset, output = f"img{index}", f"asset{index}", f"v{index}"
                x, y = self._overlay_position(layer)
                script_parts.append(
                    f"[{index}:v]format=rgba,colorchannelmixer=aa={opacity:.4g}[{raw}]"
                )
                script_parts.append(
                    f"[{raw}]scale=-1:{height}[{asset}]"
                )
                script_parts.append(
                    f"[{current}][{asset}]overlay=x={x}:y={y}[{output}]"
                )
                current = output
                visual = True
            video_output = current
        else:
            video_output = None

        pitch = int(settings.get("pitch", 0))
        audio_filters: list[str] = []
        if pitch:
            ratio = 2 ** (pitch / 12)
            if rubberband:
                audio_filters.append(f"rubberband=pitch={ratio:.8g}")
            else:
                audio_filters.extend([
                    f"asetrate=48000*{ratio:.8g}", "aresample=48000", atempo_chain(1 / ratio),
                ])
        if timeline_speed != 1:
            audio_filters.append(atempo_chain(timeline_speed))
        if audio_filters:
            script_parts.append(f"[0:a]{','.join(audio_filters)}[aout]")
        return RenderGraph(
            ";".join(script_parts), image_inputs, video_output,
            "aout" if audio_filters else None, visual,
        )


class VideoRenderer:
    def __init__(self, runner: FFmpegRunner | None = None) -> None:
        self.runner = runner or FFmpegRunner()

    @staticmethod
    def requires_render(
        studio: dict[str, Any], *, subtitle: str | Path | None = None,
    ) -> bool:
        graph = FilterGraphBuilder().build(studio, subtitle=subtitle, rubberband=False)
        return bool(graph.script)

    def mix_audio(
        self, video: VideoArtifact, voice: AudioArtifact, output: str | Path, settings: dict[str, Any],
        *, cancel: CancellationToken | None = None, progress: ProgressCallback | None = None,
    ) -> VideoArtifact:
        target = Path(output); target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.stem}.partial{target.suffix}")
        keep = bool(settings.get("keep_original", True))
        if keep:
            original = float(settings.get("original_volume", 25)) / 100
            voice_gain = float(settings.get("voice_volume", 100)) / 100
            args = ["-i", str(video.path), "-i", str(voice.path), "-filter_complex",
                    f"[0:a]volume={original}[a0];[1:a]volume={voice_gain}[a1];[a0][a1]amix=inputs=2:duration=first,alimiter=limit=0.98[a]",
                    "-map", "0:v:0", "-map", "[a]"]
        else:
            args = ["-i", str(video.path), "-i", str(voice.path), "-map", "0:v:0", "-map", "1:a:0"]
        args += ["-c:v", "copy", "-c:a", "aac", "-shortest", str(partial)]
        self.runner.run(args, duration_ms=video.duration_ms, progress=progress, cancel=cancel, step=5)
        result = self.runner.probe.video_artifact(partial)
        os.replace(partial, target); result.path = target
        return result.refresh()

    def render(
        self, video: VideoArtifact, output: str | Path, studio: dict[str, Any], export: dict[str, Any],
        *, subtitle: str | Path | None = None, cancel: CancellationToken | None = None,
        progress: ProgressCallback | None = None,
    ) -> VideoArtifact:
        target = Path(output); target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.stem}.partial{target.suffix}")
        preflight = self.runner.preflight()
        graph = FilterGraphBuilder().build(
            studio, subtitle=subtitle, rubberband=preflight["rubberband"],
            frame_width=video.width or 1920,
            frame_height=video.height or 1080,
        )
        encoder = preflight["encoder"] or "libx264"
        args = ["-i", str(video.path)]
        for image_path in graph.image_inputs:
            args += ["-loop", "1", "-i", str(image_path)]
        script: Path | None = None
        if graph.script:
            script = target.parent / f".{target.stem}.filters.txt"
            script.write_text(graph.script, encoding="utf-8")
            args += ["-filter_complex_script", str(script)]
        args += ["-map", f"[{graph.video_output}]" if graph.video_output else "0:v:0"]
        args += ["-map", f"[{graph.audio_output}]" if graph.audio_output else "0:a?"]
        args += [
            "-c:v", encoder if graph.visual else "copy",
            "-c:a", "aac" if graph.audio_output else "copy",
            "-movflags", "+faststart",
        ]
        if graph.image_inputs:
            args.append("-shortest")
        args.append(str(partial))
        try:
            self.runner.run(args, duration_ms=video.duration_ms, progress=progress, cancel=cancel, step=7)
        except PipelineError:
            if encoder == "libx264" or not graph.visual:
                raise
            args[args.index(encoder)] = "libx264"
            self.runner.run(args, duration_ms=video.duration_ms, progress=progress, cancel=cancel, step=7)
        finally:
            if script:
                script.unlink(missing_ok=True)
        result = self.runner.probe.video_artifact(partial)
        os.replace(partial, target); result.path = target
        return result.refresh()
