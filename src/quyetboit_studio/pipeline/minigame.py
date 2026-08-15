from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..core.minigame import (
    MinigameAudioEvent,
    MinigameAudioRole,
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
    sha256_file,
    stable_hash,
)
from ..services.media.ffmpeg import FFmpegRunner
from ..services.media.minigame import (
    MinigameAudioCueManifest,
    MinigameAudioService,
    MinigameSubtitleBuilder,
    MinigameVideoRenderer,
    MinigameVisualScene,
)
from ..services.media.minigame_frame import MinigameVisualFrameService
from ..services.media.minigame_mask import MinigameAlphaMaskService
from ..services.media.minigame_text import MinigameTextLayerService
from ..services.media.minigame_theme import MinigameThemeService
from ..services.minigame_tts import (
    CapCutEnglishSpeechService,
    GoogleCloudSpeechService,
    MinigameSpeechService,
)


@dataclass(slots=True)
class MinigameResult:
    outputs: list[Path]
    covers: list[Path]
    duration_ms: int
    word_count: int


class MinigamePipeline:
    TIMELINE_CACHE_VERSION = 4

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
        self.cue_manifest = MinigameAudioCueManifest()
        self.renderer = MinigameVideoRenderer(self.runner)
        self.subtitles = MinigameSubtitleBuilder()
        self.themes = MinigameThemeService()
        self.masks = MinigameAlphaMaskService()
        self.frames = MinigameVisualFrameService()
        self.text_layers = MinigameTextLayerService()
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
            manifest = self.themes.get(self.project.theme_id)
            mask = self.masks.build(manifest, self.workspace / "theme-mask")
            hook_text = self.text_layers.build_hook(
                self.project, manifest, self.workspace / "theme-text"
            )
            review_start_text = self.text_layers.build_review_start(
                self.project, manifest, self.workspace / "theme-text"
            )
            cta_text = self.text_layers.build_cta(
                self.project, manifest, self.workspace / "theme-text"
            )
            frame_layout_key = {
                "version": manifest.layout_version,
                "slots": manifest.slots,
                "styles": manifest.styles,
                "placements": manifest.placements,
            }
            hook_frame = self.frames.build(
                manifest.start_base,
                (hook_text,),
                self.workspace / "visual-frame",
                layout_key=frame_layout_key,
            )
            body_frame = self.frames.build(
                manifest.background_video,
                (mask,),
                self.workspace / "visual-frame",
                layout_key=frame_layout_key,
            )
            review_start_frame = self.frames.build(
                manifest.review_start_base,
                (review_start_text,),
                self.workspace / "visual-frame",
                layout_key=frame_layout_key,
            )
            review_frame = self.frames.build(
                manifest.review_screen,
                (),
                self.workspace / "visual-frame",
                layout_key=frame_layout_key,
            )
            cta_frame = self.frames.build(
                manifest.cta_base,
                (cta_text,),
                self.workspace / "visual-frame",
                layout_key=frame_layout_key,
            )
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
                self._emit(
                    progress, 0.52 * completed_clips / total_clips, "Đang tạo voice câu ví dụ"
                )
                slow = self._slow_clip(normal, index)
                word_audio.append(normal.path)
                slow_audio.append(slow.path)
                example_audio.append(example.path)
                durations[normal.path] = normal.duration_ms
                durations[slow.path] = slow.duration_ms
                durations[example.path] = example.duration_ms
            cue_dir = self.workspace / "audio" / "cues-v3"
            cues = {
                semantic: self.audio.normalize_cue(
                    self.cue_manifest, semantic, cue_dir, cancel=self.cancel
                )
                for semantic in self.cue_manifest.CUES
            }
            timeline = self.timeline_builder.build(
                self.project.vocabulary,
                word_audio,
                slow_audio,
                example_audio,
                durations,
                answer_reveal_duration_ms=cues["answer_reveal"].duration_ms,
            )
            for scene in timeline.scenes:
                if scene.kind != "boundary":
                    continue
                cue = cues.get(scene.phase)
                if cue is not None:
                    timeline.audio_events.append(
                        MinigameAudioEvent(
                            scene.start_ms, cue.path, scene.phase, MinigameAudioRole.EFFECT
                        )
                    )
            timeline_key = self._timeline_cache_key(timeline)
            raw_path = self.workspace / "audio" / f"timeline-{timeline_key}-raw.wav"
            master_path = self.workspace / "audio" / f"timeline-{timeline_key}-master.wav"
            raw_mix = self._cached_audio(raw_path, timeline.duration_ms)
            if raw_mix is None:
                raw_mix = self.audio.assemble(timeline.audio_events, raw_path, timeline.duration_ms)
            mixed = self._cached_audio(master_path, timeline.duration_ms)
            if mixed is None:
                mixed = self.audio.master(raw_mix, master_path, cancel=self.cancel)
            self._emit(progress, 0.62, "Đã tạo timeline audio")
            output_folder = Path(self.project.output_folder).expanduser().resolve()
            output_folder.mkdir(parents=True, exist_ok=True)
            formats = MinigameFormat(self.project.output_format).outputs
            if formats != ("9:16",):
                raise ValueError("Theme hiện chỉ hỗ trợ xuất video dọc 9:16.")
            outputs: list[Path] = []
            covers: list[Path] = []
            for format_index, aspect in enumerate(formats):
                self.cancel.raise_if_cancelled()
                ass = self.subtitles.build(
                    self.project,
                    timeline,
                    self.workspace / f"minigame-{aspect.replace(':', 'x')}.ass",
                    aspect,
                    manifest,
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
                    None,
                    mixed,
                    ass,
                    target,
                    aspect,
                    self.project.crop_focus.get(aspect, [0.5, 0.5]),
                    progress=render_progress,
                    cancel=self.cancel,
                    theme_background=manifest.background_video,
                    theme_overlay=mask,
                    start_background=manifest.start_base,
                    hook_text_overlay=hook_text,
                    hook_duration_ms=self.timeline_builder.HOOK_MS,
                    visual_scenes=self._visual_scenes(
                        timeline,
                        hook_frame,
                        body_frame,
                        manifest.score_screen,
                        review_start_frame,
                        review_frame,
                        cta_frame,
                    ),
                )
                outputs.append(rendered.path)
                covers.append(self._write_cover(hook_frame, rendered.path))
            self._emit(progress, 1.0, "Đã hoàn thành video minigame")
            return MinigameResult(
                outputs, covers, timeline.duration_ms, len(self.project.vocabulary)
            )
        except (PipelineError, ValueError):
            raise
        except Exception as exc:
            raise PipelineError(
                "minigame_failed",
                "Không thể tạo video minigame.",
                debug_detail=str(exc),
            ) from exc

    @staticmethod
    def _visual_scenes(
        timeline,
        hook_frame: Path,
        body_frame: Path,
        score_frame: Path,
        review_start_frame: Path,
        review_frame: Path,
        cta_frame: Path | None = None,
    ) -> list[MinigameVisualScene]:
        hook = next(scene for scene in timeline.scenes if scene.kind == "hook")
        review_start = next(scene for scene in timeline.scenes if scene.kind == "review_start")
        score = next(scene for scene in timeline.scenes if scene.kind == "score")
        reviews = [scene for scene in timeline.scenes if scene.kind == "review"]
        cta = next((scene for scene in timeline.scenes if scene.kind == "cta"), None)
        scenes = [MinigameVisualScene(hook_frame, hook.end_ms - hook.start_ms)]
        before_score = score.start_ms - hook.end_ms
        if before_score > 0:
            scenes.append(MinigameVisualScene(body_frame, before_score))
        scenes.append(MinigameVisualScene(score_frame, score.end_ms - score.start_ms))
        scenes.append(
            MinigameVisualScene(review_start_frame, review_start.end_ms - review_start.start_ms)
        )
        scenes.extend(
            MinigameVisualScene(
                review_frame,
                review.end_ms - review.start_ms,
                fade_in_ms=200 if index else 0,
            )
            for index, review in enumerate(reviews)
        )
        if cta is not None:
            if cta_frame is None:
                raise PipelineError("theme_scene_invalid", "Thiếu frame CTA của minigame.")
            scenes.append(
                MinigameVisualScene(cta_frame, cta.end_ms - cta.start_ms, fade_in_ms=220)
            )
        return scenes

    def _speech_clip(self, speech: MinigameSpeechService, text: str, item_id: str):
        key = self._speech_cache_key(text)
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

    def _speech_cache_key(self, text: str) -> str:
        return stable_hash(
            {
                "provider": self.project.tts_provider,
                "voice": self.project.voice,
                "language": self.project.voice_language,
                "volume": self.project.volume,
                "text": text,
                "speech_processing_version": 2,
            }
        )[:20]

    def _cached_audio(self, path: Path, duration_ms: int):
        if not path.is_file():
            return None
        try:
            artifact = self.runner.probe.audio_artifact(path).refresh()
        except PipelineError:
            return None
        return artifact if abs(artifact.duration_ms - duration_ms) <= 40 else None

    def _timeline_cache_key(self, timeline) -> str:
        """Timing and cue edits invalidate only mix/render, never the TTS cache."""
        return stable_hash(
            {
                "version": self.TIMELINE_CACHE_VERSION,
                "timing": self.timeline_builder.timing,
                "duration_ms": timeline.duration_ms,
                "events": [
                    (event.start_ms, event.semantic, event.role, sha256_file(event.path))
                    for event in timeline.audio_events
                ],
            }
        )[:20]

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
        safe = re.sub(r"[^\w.-]+", "_", self.project.output_name.strip(), flags=re.UNICODE).strip(
            "._"
        )
        safe = safe or "english_minigame"
        suffix = f"_{aspect.replace(':', 'x')}" if include_suffix else ""
        candidate = folder / f"{safe}{suffix}.mp4"
        index = 2
        while candidate.exists() or candidate.with_name(f"{candidate.stem}_cover.png").exists():
            candidate = folder / f"{safe}{suffix}_{index}.mp4"
            index += 1
        return candidate

    @staticmethod
    def _write_cover(source: Path, video_output: Path) -> Path:
        """Atomically export the exact rendered start frame next to its video."""
        cover = video_output.with_name(f"{video_output.stem}_cover.png")
        if cover.exists():
            raise PipelineError(
                "minigame_cover_exists", "Ảnh bìa đã tồn tại; không ghi đè output cũ."
            )
        partial = cover.with_name(f".{cover.stem}.partial{cover.suffix}")
        try:
            data = source.read_bytes()
            partial.write_bytes(data)
            with Image.open(partial) as image:
                if image.size != (1080, 1920) or image.mode not in {"RGB", "RGBA"}:
                    raise PipelineError("minigame_cover_invalid", "Ảnh bìa minigame không hợp lệ.")
            os.replace(partial, cover)
            return cover
        finally:
            partial.unlink(missing_ok=True)

    @staticmethod
    def _emit(progress: ProgressCallback | None, fraction: float, message: str) -> None:
        if progress:
            progress(ProgressEvent(0, max(0.0, min(1.0, fraction)), message))
