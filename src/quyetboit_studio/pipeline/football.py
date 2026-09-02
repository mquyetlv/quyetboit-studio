from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image

from ..core.football import FootballHighlightInput, FootballRenderConfig, FootballSegment
from ..core.models import (
    CancellationToken,
    PipelineError,
    ProgressCallback,
    ProgressEvent,
    VideoArtifact,
    stable_hash,
)
from ..services.media.ffmpeg import FFmpegRunner
from ..services.youtube import YouTubeDownloader


class FootballSpeechService(Protocol):
    def synthesize(self, text: str, output: Path, *, cancel: CancellationToken | None = None) -> Path: ...


@dataclass(frozen=True, slots=True)
class FootballResult:
    output: Path
    duration_ms: int
    segments: int


class FootballHighlightPipeline:
    """Background-safe media pipeline.  UI only owns this object through a worker."""

    FADE_MS = 450
    def __init__(self, content: FootballHighlightInput, *, output_folder: Path, workspace: Path,
                 speech_factory: Callable[[], FootballSpeechService], runner: FFmpegRunner | None = None,
                 downloader: YouTubeDownloader | None = None, burn_subtitles: bool = True,
                 cancel: CancellationToken | None = None, tts_suffix: str = ".wav",
                 voice_cache_key: dict[str, object] | None = None,
                 render_config: FootballRenderConfig | None = None) -> None:
        self.content, self.output_folder, self.workspace = content, output_folder, workspace
        self.speech_factory, self.runner = speech_factory, runner or FFmpegRunner()
        self.downloader, self.burn_subtitles, self.cancel = downloader or YouTubeDownloader(self.runner), burn_subtitles, cancel or CancellationToken()
        self.tts_suffix = tts_suffix if tts_suffix.startswith(".") else f".{tts_suffix}"
        self.voice_cache_key = voice_cache_key or {}
        self.render_config = render_config or FootballRenderConfig()

    def cancel_run(self) -> None:
        self.cancel.cancel()

    def run(self, progress: ProgressCallback | None = None) -> FootballResult:
        self.cancel.raise_if_cancelled()
        self.workspace.mkdir(parents=True, exist_ok=True); self.output_folder.mkdir(parents=True, exist_ok=True)
        self._emit(progress, .02, "Đang tải video YouTube")
        title, source = self.downloader.download(self.content.youtube_url, self.workspace / "source", cancel=self.cancel)
        speech = self.speech_factory(); rendered: list[Path] = []
        total = len(self.content.segments)
        for index, segment in enumerate(self.content.segments):
            self.cancel.raise_if_cancelled()
            base = .12 + .76 * index / total
            self._emit(progress, base, f"Đang tạo voice {index + 1}/{total}")
            audio_key = stable_hash({"text": segment.tts_text, "voice": self.voice_cache_key})[:20]
            audio = self.workspace / "audio" / f"segment-{index + 1:03d}-{audio_key}.wav"
            if not audio.is_file():
                raw = audio.with_name(f"{audio.stem}.source{self.tts_suffix}")
                self._synthesize_with_retry(speech, segment.tts_text, raw)
                self._normalize_audio(raw, audio)
            audio_artifact = self.runner.probe.audio_artifact(audio)
            frames = self._extract_frames(source, segment, index)
            ass = (
                self._write_ass(segment.tts_text, index, audio_artifact.duration_ms)
                if self.burn_subtitles
                else None
            )
            target = self.workspace / "segments" / f"segment-{index + 1:03d}.mp4"
            self._render_segment(frames, audio, ass, target, progress, base, .76 / total)
            rendered.append(target)
        self._emit(progress, .90, "Đang ghép các phân đoạn")
        output = self._available_output(title)
        self._concat(rendered, output, progress)
        artifact = self.runner.probe.video_artifact(output)
        self._emit(progress, 1, "Đã tạo video Highlight bóng đá")
        return FootballResult(output, artifact.duration_ms, total)

    def _extract_frames(self, source: VideoArtifact, segment: FootballSegment, index: int) -> list[Path]:
        directory = self.workspace / "frames" / f"segment-{index + 1:03d}"; directory.mkdir(parents=True, exist_ok=True)
        frames: list[Path] = []
        for position, timestamp in enumerate(segment.timestamps()):
            target = directory / f"frame-{position:03d}.jpg"
            if not target.is_file():
                try:
                    self.runner.run(["-ss", timestamp, "-i", str(source.path), "-frames:v", "1", "-q:v", "2", str(target)], cancel=self.cancel)
                except PipelineError:
                    target.unlink(missing_ok=True)
            if target.is_file(): frames.append(target)
        if not frames:
            fallback = directory / "fallback.jpg"
            Image.new("RGB", (1920, 1080), "#0f172a").save(fallback, quality=92)
            frames.append(fallback)
        return frames

    def _normalize_audio(self, source: Path, target: Path) -> None:
        partial = target.with_name(f".{target.stem}.partial.wav")
        try:
            self.runner.run(["-i", str(source), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(partial)], cancel=self.cancel)
            self.runner.probe.audio_artifact(partial)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)

    def _synthesize_with_retry(self, speech: FootballSpeechService, text: str, output: Path) -> None:
        """Retry only provider-declared temporary TTS failures; cancellation is immediate."""
        for attempt in range(3):
            self.cancel.raise_if_cancelled()
            try:
                speech.synthesize(text, output, cancel=self.cancel)
                return
            except PipelineError as exc:
                if not exc.retryable or attempt == 2:
                    raise
                # Do not use uninterruptible sleep: Stop remains responsive during backoff.
                if self.cancel.wait(0.5 * (2**attempt)):
                    self.cancel.raise_if_cancelled()
        raise AssertionError("TTS retry loop must return or raise.")

    def _render_segment(self, frames: list[Path], audio: Path, ass: Path | None, target: Path,
                        progress: ProgressCallback | None, base: float, span: float) -> None:
        duration = self.runner.probe.audio_artifact(audio).duration_ms
        per_frame = max(0.7, duration / 1000 / len(frames)); fade = min(self.FADE_MS / 1000, per_frame / 3)
        partial = target.with_name(f".{target.stem}.partial.mp4"); target.parent.mkdir(parents=True, exist_ok=True)
        args: list[str] = []
        # Use a fixed input cadence.  zoompan with a single output frame for
        # each input frame then has an exact, stable 30 fps timeline.
        for frame in frames: args += ["-loop", "1", "-framerate", "30", "-t", f"{per_frame:.3f}", "-i", str(frame)]
        args += ["-i", str(audio)]
        chains: list[str] = []
        for i in range(len(frames)):
            chains.append(self._ken_burns_filter(i, per_frame))
        label = "[v0]"
        for i in range(1, len(frames)):
            offset = i * per_frame - i * fade
            chains.append(f"{label}[v{i}]xfade=transition=fade:duration={fade:.3f}:offset={offset:.3f}[x{i}]")
            label = f"[x{i}]"
        if ass:
            escaped = str(ass.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            chains.append(f"{label}subtitles='{escaped}'[outv]"); label = "[outv]"
        # libass/JPEG inputs can otherwise leave the final MP4 tagged yuvj444p,
        # which Windows Media Player does not decode on many installations.
        chains.append(f"{label}scale=in_range=full:out_range=tv,format=yuv420p[compat]")
        label = "[compat]"
        args += ["-filter_complex", ";".join(chains), "-map", label, "-map", f"{len(frames)}:a", "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.1", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(partial)]
        try:
            self.runner.run(args, duration_ms=duration, progress=lambda event: self._emit(progress, base + span * event.fraction, f"Render segment — {event.message}"), cancel=self.cancel)
            self.runner.probe.video_artifact(partial); os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)

    def _ken_burns_filter(self, index: int, duration_seconds: float) -> str:
        """Build a frame-accurate 30 fps Ken Burns move with alternating pan."""
        frames = max(1, round(duration_seconds * 30))
        start, end = self.render_config.ken_burns_start_zoom, self.render_config.ken_burns_end_zoom
        # `on` is zoompan's output-frame number.  d=1 and the 30 fps looping
        # input avoid the old d=<segment frame count> burst/reset behaviour.
        # The 2x working canvas makes crop-coordinate rounding sub-pixel after
        # final Lanczos downsampling, removing the visible one-pixel stepping.
        focus = ((0.25, 0.35), (0.75, 0.35), (0.70, 0.65), (0.30, 0.65))[index % 4]
        denominator = max(1, frames - 1)
        return (
            f"[{index}:v]scale=3840:2160:force_original_aspect_ratio=increase,"
            "crop=3840:2160,setsar=1,"
            f"zoompan=z='{start:.8f}+{end - start:.8f}*on/{denominator}':"
            f"x='(iw-iw/zoom)*{focus[0]:.3f}':"
            f"y='(ih-ih/zoom)*{focus[1]:.3f}':"
            f"d=1:s=3840x2160:fps=30,scale=1920:1080:flags=lanczos,trim=duration={duration_seconds:.3f},"
            f"setpts=PTS-STARTPTS[v{index}]"
        )

    def _concat(self, segments: list[Path], output: Path, progress: ProgressCallback | None) -> None:
        partial = output.with_name(f".{output.stem}.partial.mp4")
        durations = [self.runner.probe.video_artifact(path).duration_ms / 1000 for path in segments]
        fade = min(self.FADE_MS / 1000, *(duration / 3 for duration in durations))
        args: list[str] = []
        for segment in segments:
            args += ["-i", str(segment)]
        chains: list[str] = []
        for index in range(len(segments)):
            chains.append(f"[{index}:v]fps=30,format=yuv420p,setpts=PTS-STARTPTS[v{index}]")
            chains.append(f"[{index}:a]aresample=48000,asetpts=PTS-STARTPTS[a{index}]")
        video, audio, cursor = "[v0]", "[a0]", durations[0]
        for index in range(1, len(segments)):
            offset = max(0, cursor - fade)
            chains.append(f"{video}[v{index}]xfade=transition=fade:duration={fade:.3f}:offset={offset:.3f}[vx{index}]")
            chains.append(f"{audio}[a{index}]acrossfade=d={fade:.3f}:c1=tri:c2=tri[ax{index}]")
            video, audio, cursor = f"[vx{index}]", f"[ax{index}]", cursor + durations[index] - fade
        args += ["-filter_complex", ";".join(chains), "-map", video, "-map", audio, "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.1", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(partial)]
        try:
            self.runner.run(args, duration_ms=round(cursor * 1000), progress=lambda event: self._emit(progress, .90 + .10 * event.fraction, event.message), cancel=self.cancel)
            self.runner.probe.video_artifact(partial); os.replace(partial, output)
        finally: partial.unlink(missing_ok=True)

    def _write_ass(self, text: str, index: int, duration_ms: int) -> Path:
        path = self.workspace / "subtitles" / f"segment-{index + 1:03d}.ass"; path.parent.mkdir(parents=True, exist_ok=True)
        cues = self._subtitle_cues(text, duration_ms)
        dialogues = "".join(
            "Dialogue: 0,"
            f"{self._ass_time(start_ms)},{self._ass_time(end_ms)},Default,,0,0,0,,"
            f"{self._escape_ass(cue)}\n"
            for start_ms, end_ms, cue in cues
        )
        path.write_text(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
            "WrapStyle: 0\nScaledBorderAndShadow: yes\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
            "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
            "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: Default,Arial,44,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,"
            "-1,0,0,0,100,100,0,0,1,3,1,2,90,90,56,1\n[Events]\n"
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
            + dialogues,
            encoding="utf-8-sig",
        )
        return path

    @staticmethod
    def _ass_time(value_ms: int) -> str:
        hours, remainder = divmod(max(0, value_ms), 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, milliseconds = divmod(remainder, 1_000)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds // 10:02d}"

    @staticmethod
    def _escape_ass(value: str) -> str:
        return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")

    @classmethod
    def _subtitle_cues(cls, text: str, duration_ms: int, limit: int = 42) -> list[tuple[int, int, str]]:
        """Split narration into SRT-like single-line cues, timed by text weight."""
        sentences = [item.strip() for item in re.split(r"(?<=[.!?…])\s+", text.replace("\n", " ")) if item.strip()]
        chunks: list[str] = []
        for sentence in sentences or [text.strip()]:
            # Prefer natural clause boundaries before falling back to word boundaries.
            clauses = [item.strip(" ,") for item in re.split(r"(?<=[,;:])\s+", sentence) if item.strip(" ,")]
            for clause in clauses:
                words = clause.split(); current = ""
                for word in words:
                    candidate = f"{current} {word}".strip()
                    if current and len(candidate) > limit:
                        chunks.append(current); current = word
                    else:
                        current = candidate
                if current:
                    chunks.append(current)
        if not chunks:
            return [(0, max(1, duration_ms), "")]
        total_weight = sum(max(1, len(chunk)) for chunk in chunks)
        cursor, output = 0, []
        for index, chunk in enumerate(chunks):
            remaining = max(1, duration_ms - cursor)
            allocated = remaining if index == len(chunks) - 1 else max(
                500, round(duration_ms * len(chunk) / total_weight)
            )
            end = min(duration_ms, cursor + allocated)
            if end <= cursor:
                end = cursor + 1
            output.append((cursor, end, chunk))
            cursor = end
        return output

    def _available_output(self, title: str) -> Path:
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .") or "football_highlight"; target = self.output_folder / f"{safe}.mp4"; n = 2
        while target.exists(): target = self.output_folder / f"{safe}_{n}.mp4"; n += 1
        return target

    @staticmethod
    def _emit(callback: ProgressCallback | None, fraction: float, message: str) -> None:
        if callback: callback(ProgressEvent(0, max(0, min(1, fraction)), message))
