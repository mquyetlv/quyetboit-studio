from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.minigame import (
    MinigameFormat,
    MinigameProject,
    MinigameTimelineBuilder,
    MinigameTtsProvider,
)
from ..core.models import (
    CancellationToken,
    PipelineError,
    ProgressCallback,
    ProgressEvent,
    stable_hash,
)
from ..services.media.ffmpeg import FFmpegRunner
from ..services.media.minigame import (
    MinigameAudioService,
    MinigameSubtitleBuilder,
    MinigameVideoRenderer,
)
from ..services.minigame_tts import (
    CapCutEnglishSpeechService,
    GoogleCloudSpeechService,
    MinigameSpeechService,
)


@dataclass(slots=True)
class MinigameResult:
    outputs: list[Path]
    duration_ms: int
    word_count: int


class MinigamePipeline:
    def __init__(
        self,
        project: MinigameProject,
        *,
        workspace: str | Path | None = None,
        speech_factory: Callable[[MinigameProject], MinigameSpeechService] | None = None,
        runner: FFmpegRunner | None = None,
    ) -> None:
        self.project = project
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.workspace = Path(
            workspace or base / "QuyetBoITStudio" / "workspaces" / project.project_uuid / "minigame"
        ).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.cancel = CancellationToken()
        self.runner = runner or FFmpegRunner()
        self.audio = MinigameAudioService(self.runner)
        self.renderer = MinigameVideoRenderer(self.runner)
        self.subtitles = MinigameSubtitleBuilder()
        self.timeline_builder = MinigameTimelineBuilder()
        self.speech_factory = speech_factory or self._default_speech

    def cancel_run(self) -> None:
        self.cancel.cancel()

    @staticmethod
    def _default_speech(project: MinigameProject) -> MinigameSpeechService:
        if project.tts_provider == MinigameTtsProvider.GOOGLE.value:
            return GoogleCloudSpeechService(
                project.service_account_path,
                project.voice,
                language_code=project.voice_language or "en-US",
                volume=project.volume,
            )
        return CapCutEnglishSpeechService(project.voice, volume=project.volume)

    def run(self, progress: ProgressCallback | None = None) -> MinigameResult:
        try:
            self.project.validate()
            self.cancel.raise_if_cancelled()
            self.runner.preflight()
            background = self.runner.probe.video_artifact(self.project.background_video)
            speech = self.speech_factory(self.project)
            word_audio: list[Path] = []
            slow_audio: list[Path] = []
            example_audio: list[Path] = []
            durations: dict[Path, int] = {}
            total_clips = len(self.project.vocabulary) * 2
            completed_clips = 0
            for index, item in enumerate(self.project.vocabulary):
                self.cancel.raise_if_cancelled()
                normal = self._speech_clip(speech, item.english, f"word-{index}")
                completed_clips += 1
                self._emit(progress, 0.52 * completed_clips / total_clips, "Đang tạo voice từ vựng")
                example = self._speech_clip(speech, item.example, f"example-{index}")
                completed_clips += 1
                self._emit(progress, 0.52 * completed_clips / total_clips, "Đang tạo voice câu ví dụ")
                slow = self._slow_clip(normal, index)
                word_audio.append(normal.path)
                slow_audio.append(slow.path)
                example_audio.append(example.path)
                durations[normal.path] = normal.duration_ms
                durations[slow.path] = slow.duration_ms
                durations[example.path] = example.duration_ms
            timeline = self.timeline_builder.build(
                self.project.vocabulary, word_audio, slow_audio, example_audio, durations
            )
            cue_dir = self.workspace / "audio" / "cues-v2"
            tick = self.audio.cue(cue_dir / "tick.wav", "tick", cancel=self.cancel)
            reveal = self.audio.cue(cue_dir / "reveal.wav", "reveal", cancel=self.cancel)
            opening = self.audio.cue(cue_dir / "open.wav", "open", cancel=self.cancel)
            finish = self.audio.cue(cue_dir / "finish.wav", "finish", cancel=self.cancel)
            timeline.audio_events.append((180, opening.path))
            for scene in timeline.scenes:
                if scene.kind == "word" and scene.phase == "question":
                    timeline.audio_events.extend(
                        (scene.start_ms + offset * 1_000 + 80, tick.path)
                        for offset in range(3)
                    )
                if scene.kind == "word" and scene.phase == "answer":
                    timeline.audio_events.append((scene.start_ms, reveal.path))
                if scene.kind == "score":
                    timeline.audio_events.append((scene.start_ms + 160, finish.path))
                if scene.kind == "cta":
                    timeline.audio_events.append((scene.start_ms + 120, opening.path))
            raw_mix = self.audio.assemble(
                timeline.audio_events,
                self.workspace / "audio" / "timeline-v2-raw.wav",
                timeline.duration_ms,
            )
            mixed = self.audio.master(
                raw_mix,
                self.workspace / "audio" / "timeline-v2-master.wav",
                cancel=self.cancel,
            )
            self._emit(progress, 0.62, "Đã tạo timeline audio")
            output_folder = Path(self.project.output_folder).expanduser().resolve()
            output_folder.mkdir(parents=True, exist_ok=True)
            formats = MinigameFormat(self.project.output_format).outputs
            outputs: list[Path] = []
            for format_index, aspect in enumerate(formats):
                self.cancel.raise_if_cancelled()
                ass = self.subtitles.build(
                    self.project,
                    timeline,
                    self.workspace / f"minigame-{aspect.replace(':', 'x')}.ass",
                    aspect,
                )
                target = self._available_output(output_folder, aspect, len(formats) > 1)
                base = 0.62 + 0.38 * format_index / len(formats)
                span = 0.38 / len(formats)

                def render_progress(
                    event: ProgressEvent,
                    progress_base: float = base,
                    progress_span: float = span,
                    render_aspect: str = aspect,
                ) -> None:
                    self._emit(
                        progress,
                        progress_base + progress_span * event.fraction,
                        f"Render {render_aspect} — {round(event.fraction * 100)}%",
                    )

                rendered = self.renderer.render(
                    background,
                    mixed,
                    ass,
                    target,
                    aspect,
                    self.project.crop_focus.get(aspect, [0.5, 0.5]),
                    progress=render_progress,
                    cancel=self.cancel,
                )
                outputs.append(rendered.path)
            self._emit(progress, 1.0, "Đã hoàn thành video minigame")
            return MinigameResult(outputs, timeline.duration_ms, len(self.project.vocabulary))
        except (PipelineError, ValueError):
            raise
        except Exception as exc:
            raise PipelineError(
                "minigame_failed",
                "Không thể tạo video minigame.",
                debug_detail=str(exc),
            ) from exc

    def _speech_clip(
        self, speech: MinigameSpeechService, text: str, item_id: str
    ):
        key = stable_hash(
            {
                "provider": self.project.tts_provider,
                "voice": self.project.voice,
                "language": self.project.voice_language,
                "volume": self.project.volume,
                "text": text,
                "speech_processing_version": 2,
            }
        )[:20]
        directory = self.workspace / "audio"
        normalized = directory / f"{item_id}-{key}.wav"
        if normalized.is_file():
            try:
                return self.runner.probe.audio_artifact(normalized).refresh()
            except PipelineError:
                pass
        suffix = ".wav" if self.project.tts_provider == MinigameTtsProvider.GOOGLE.value else ".mp3"
        raw = directory / f"{item_id}-{key}.source{suffix}"
        speech.synthesize(text, raw, cancel=self.cancel)
        return self.audio.normalize(raw, normalized, cancel=self.cancel)

    def _slow_clip(self, normal, index: int):
        rate = max(0.5, min(1.0, float(self.project.slow_rate)))
        key = stable_hash({"source": normal.checksum, "rate": rate})[:20]
        target = self.workspace / "audio" / f"word-{index}-{key}-slow.wav"
        if target.is_file():
            try:
                return self.runner.probe.audio_artifact(target).refresh()
            except PipelineError:
                pass
        return self.audio.slow(normal, target, rate, cancel=self.cancel)

    def _available_output(self, folder: Path, aspect: str, include_suffix: bool) -> Path:
        safe = re.sub(r"[^\w.-]+", "_", self.project.output_name.strip(), flags=re.UNICODE).strip("._")
        safe = safe or "english_minigame"
        suffix = f"_{aspect.replace(':', 'x')}" if include_suffix else ""
        candidate = folder / f"{safe}{suffix}.mp4"
        index = 2
        while candidate.exists():
            candidate = folder / f"{safe}{suffix}_{index}.mp4"
            index += 1
        return candidate

    @staticmethod
    def _emit(progress: ProgressCallback | None, fraction: float, message: str) -> None:
        if progress:
            progress(ProgressEvent(0, max(0.0, min(1.0, fraction)), message))
