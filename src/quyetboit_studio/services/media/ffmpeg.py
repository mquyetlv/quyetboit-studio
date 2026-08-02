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


class FilterGraphBuilder:
    @staticmethod
    def escape(value: str) -> str:
        return value.replace("\\", "/").replace(":", "\\:").replace("'", "\\'").replace("[", "\\[").replace("]", "\\]")

    def build(self, settings: dict[str, Any], *, subtitle: str | Path | None = None, rubberband: bool = False) -> tuple[str, bool]:
        filters: list[str] = []
        visual = False
        if subtitle:
            filters.append(f"subtitles='{self.escape(str(Path(subtitle).resolve()))}'")
            visual = True
        speed = float(settings.get("speed", 1.0))
        if speed != 1:
            filters.append(f"setpts=PTS/{speed:.8g}"); visual = True
        zoom = max(0.5, min(2.0, float(settings.get("zoom", 100)) / 100))
        if zoom != 1:
            filters.append(f"scale=iw*{zoom:.8g}:ih*{zoom:.8g},crop=iw/{zoom:.8g}:ih/{zoom:.8g}"); visual = True
        if settings.get("flip_horizontal"):
            filters.append("hflip"); visual = True
        if settings.get("flip_vertical"):
            filters.append("vflip"); visual = True
        color = float(settings.get("color", 100)) / 100
        if color != 1:
            filters.append(f"eq=saturation={max(0, min(2, color)):.4g}"); visual = True
        return ",".join(filters), visual


class VideoRenderer:
    def __init__(self, runner: FFmpegRunner | None = None) -> None:
        self.runner = runner or FFmpegRunner()

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
        graph, visual = FilterGraphBuilder().build(studio, subtitle=subtitle, rubberband=preflight["rubberband"])
        encoder = preflight["encoder"] or "libx264"
        args = ["-i", str(video.path)]
        speed = float(studio.get("speed", 1.0))
        audio_filtered = speed != 1.0
        if graph:
            script = target.parent / f".{target.stem}.filters.txt"
            script_text = f"[0:v]{graph}[v]"
            if audio_filtered:
                script_text += f";[0:a]{atempo_chain(speed)}[a]"
            script.write_text(script_text, encoding="utf-8")
            args += ["-filter_complex_script", str(script), "-map", "[v]", "-map", "[a]" if audio_filtered else "0:a?"]
        args += ["-c:v", encoder if visual else "copy", "-c:a", "aac" if audio_filtered else "copy", "-movflags", "+faststart", str(partial)]
        try:
            self.runner.run(args, duration_ms=video.duration_ms, progress=progress, cancel=cancel, step=7)
        except PipelineError:
            if encoder == "libx264" or not visual:
                raise
            args[args.index(encoder)] = "libx264"
            self.runner.run(args, duration_ms=video.duration_ms, progress=progress, cancel=cancel, step=7)
        result = self.runner.probe.video_artifact(partial)
        os.replace(partial, target); result.path = target
        return result.refresh()
