from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from ..core.football import FootballHighlightInput, FootballRenderConfig, FootballSegment
from ..core.models import (
    CancellationToken,
    PipelineError,
    ProgressCallback,
    ProgressEvent,
    sha256_file,
    stable_hash,
)
from ..services.media.ffmpeg import FFmpegRunner
from ..services.media.football import FootballMediaService
from ..services.media.football_cache import cache_record, cache_valid
from ..services.subtitles.football import paired_clauses
from ..services.youtube import YouTubeDownload, YouTubeDownloader


class FootballSpeechService(Protocol):
    def synthesize(
        self, text: str, output: Path, *, cancel: CancellationToken | None = None
    ) -> Path: ...


@dataclass(frozen=True, slots=True)
class FootballResult:
    output: Path
    duration_ms: int
    segments: int


class FootballHighlightPipeline:
    """Background-safe media pipeline.  UI only owns this object through a worker."""

    FADE_MS = 450
    OUTPUT_FPS = 30

    def __init__(
        self,
        content: FootballHighlightInput,
        *,
        output_folder: Path,
        workspace: Path,
        speech_factory: Callable[[], FootballSpeechService],
        runner: FFmpegRunner | None = None,
        downloader: YouTubeDownloader | None = None,
        burn_subtitles: bool = True,
        cancel: CancellationToken | None = None,
        tts_suffix: str = ".wav",
        voice_cache_key: dict[str, object] | None = None,
        render_config: FootballRenderConfig | None = None,
    ) -> None:
        self.content, self.output_folder, self.workspace = content, output_folder, workspace
        self.speech_factory, self.runner = speech_factory, runner or FFmpegRunner()
        self.downloader, self.burn_subtitles, self.cancel = (
            downloader or YouTubeDownloader(self.runner),
            burn_subtitles,
            cancel or CancellationToken(),
        )
        self.tts_suffix = tts_suffix if tts_suffix.startswith(".") else f".{tts_suffix}"
        self.voice_cache_key = voice_cache_key or {}
        self.render_config = render_config or FootballRenderConfig()
        self.media = FootballMediaService(
            self.workspace, self.runner, self.render_config, self.cancel
        )

    def cancel_run(self) -> None:
        self.cancel.cancel()

    def run(self, progress: ProgressCallback | None = None) -> FootballResult:
        self.cancel.raise_if_cancelled()
        if self.render_config.music_path:
            self.runner.probe.audio_artifact(Path(self.render_config.music_path))
        callback = progress
        last = 0.0

        def report(event: ProgressEvent) -> None:
            nonlocal last
            last = max(last, event.fraction)
            if callback:
                callback(ProgressEvent(event.step, last, event.message))

        progress = report
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.output_folder.mkdir(parents=True, exist_ok=True)
        self._emit(progress, 0.02, "Đang tải video YouTube")
        options = {"cancel": self.cancel}
        if isinstance(self.downloader, YouTubeDownloader):
            options["progress"] = lambda event: self._emit(
                progress, 0.02 + 0.09 * event.fraction, event.message
            )
        downloaded = self.downloader.download(
            self.content.youtube_url, self.workspace / "source", **options
        )
        # Compatibility with narrow test/provider doubles retained during the public API transition.
        if isinstance(downloaded, tuple):
            title, source = downloaded
            downloaded = YouTubeDownload(title, source)
        title, source = downloaded.title, downloaded.source
        output_directory = self._available_output_directory(title)
        self._copy_thumbnail(downloaded.thumbnail, output_directory)
        speech = self.speech_factory()
        rendered: list[Path] = []
        total = len(self.content.segments)
        for index, segment in enumerate(self.content.segments):
            self.cancel.raise_if_cancelled()
            base = 0.12 + 0.76 * index / total
            self._emit(progress, base, f"Đang tạo voice {index + 1}/{total}")
            frames = self.media._extract_frames(source, segment, index)
            audio, cues = self._segment_speech(speech, segment, index)
            ass = self.media._write_ass(
                segment.content,
                index,
                self.runner.probe.audio_artifact(audio).duration_ms,
                timing_text=segment.tts_text,
                cues=cues if self.burn_subtitles else [],
                event=f"{segment.match_minute} • {segment.situation_type}"
                if self.render_config.show_event
                else "",
            )
            visual_config = asdict(self.render_config)
            visual_config.pop("music_path")
            key = stable_hash(
                {
                    "frames": [sha256_file(path, self.cancel) for path in frames],
                    "audio": sha256_file(audio, self.cancel),
                    "ass": sha256_file(ass, self.cancel),
                    "config": visual_config,
                    "weights": segment.frame_weights(),
                    "source": source.checksum,
                    "timestamps": segment.timestamps(),
                    "version": 4,
                }
            )
            target = self.workspace / "segments" / f"segment-{index + 1:03d}-{key[:20]}.mp4"
            if not cache_valid(target, key, self.cancel):
                self.media._render_segment(
                    frames,
                    audio,
                    ass,
                    target,
                    progress,
                    base,
                    0.76 / total,
                    weights=segment.frame_weights()
                    if self.render_config.emphasize_finish
                    else None,
                    source=source,
                    timestamps=segment.timestamps(),
                )
                cache_record(target, key, self.cancel)
            rendered.append(target)
        self._emit(progress, 0.90, "Đang ghép các phân đoạn")
        output = self._available_output(output_directory)
        if self.render_config.music_path:
            intermediate = self.workspace / "joined.mp4"
            self.media._concat(rendered, intermediate, progress)
            self.media._mix_music(intermediate, Path(self.render_config.music_path), output)
        else:
            self.media._concat(rendered, output, progress)
        artifact = self.runner.probe.video_artifact(output)
        self.media.write_cover(output, output_directory)
        self._emit(progress, 1, "Đã tạo video Highlight bóng đá")
        return FootballResult(output, artifact.duration_ms, total)

    def _segment_speech(
        self, speech: FootballSpeechService, segment: FootballSegment, index: int
    ) -> tuple[Path, list[tuple[int, int, str]]]:
        clips, cues, cursor = [], [], 0
        for display, spoken in paired_clauses(segment.content, segment.tts_text):
            self.cancel.raise_if_cancelled()
            key = stable_hash({"text": spoken, "voice": self.voice_cache_key, "normalizer": 3})
            audio = self.workspace / "audio" / f"{key[:24]}.wav"
            audio.parent.mkdir(parents=True, exist_ok=True)
            if not cache_valid(audio, key, self.cancel):
                raw = audio.with_name(f".{audio.stem}.source.partial{self.tts_suffix}")
                try:
                    self._synthesize_with_retry(speech, spoken, raw)
                    self.media._normalize_audio(
                        raw, audio, volume=float(self.voice_cache_key.get("volume", 100)) / 100
                    )
                    cache_record(audio, key, self.cancel)
                finally:
                    raw.unlink(missing_ok=True)
                    raw.with_suffix(raw.suffix + ".partial").unlink(missing_ok=True)
                    raw.with_name(f".{raw.stem}.partial{raw.suffix}").unlink(missing_ok=True)
            duration = self.runner.probe.audio_artifact(audio).duration_ms
            cues.append((cursor, cursor + duration, display))
            cursor += duration
            clips.append(audio)
        if len(clips) == 1:
            return clips[0], cues
        key = stable_hash([sha256_file(path, self.cancel) for path in clips])
        audio = self.workspace / "audio" / f"joined-{key[:24]}.wav"
        if not cache_valid(audio, key, self.cancel):
            self.media._join_voice(clips, audio)
            cache_record(audio, key, self.cancel)
        return audio, cues

    def _synthesize_with_retry(
        self, speech: FootballSpeechService, text: str, output: Path
    ) -> None:
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

    def _available_output_directory(self, title: str) -> Path:
        safe = (
            re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:100] or "football_highlight"
        )
        if safe.split(".")[0].upper() in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }:
            safe = "_" + safe
        target = self.output_folder / safe
        n = 2
        while target.exists() and (not target.is_dir() or any(target.iterdir())):
            target = self.output_folder / f"{safe}_{n}"
            n += 1
        target.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def _copy_thumbnail(thumbnail: Path | None, directory: Path) -> None:
        if not thumbnail or not thumbnail.is_file():
            return
        target = directory / f"thumbnail{thumbnail.suffix.lower()}"
        if target.exists():
            return
        # A direct byte copy preserves yt-dlp's selected highest quality artwork.
        target.write_bytes(thumbnail.read_bytes())

    def _available_output(self, directory: Path) -> Path:
        target = directory / "highlight.mp4"
        n = 2
        while target.exists():
            target = directory / f"highlight_{n}.mp4"
            n += 1
        return target

    @staticmethod
    def _emit(callback: ProgressCallback | None, fraction: float, message: str) -> None:
        if callback:
            callback(ProgressEvent(0, max(0, min(1, fraction)), message))
