from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image

from ..core.minigame import (
    MinigameAudioEvent,
    MinigameAudioRole,
    MinigameFormat,
    MinigameMultipartContent,
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
    TIMELINE_CACHE_VERSION = 6

    def __init__(
        self,
        project: MinigameProject,
        *,
        workspace: str | Path | None = None,
        speech_factory: Callable[[MinigameProject], MinigameSpeechService] | None = None,
        runner: FFmpegRunner | None = None,
        preserve_output_name: bool = False,
    ) -> None:
        self.project = project
        self.preserve_output_name = preserve_output_name
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
            custom_background = self._custom_background_video()
            manifest = self.themes.get(self.project.theme_id)
            mask = self.masks.build(manifest, self.workspace / "theme-mask")
            review_mask = (
                self.masks.build_review(manifest, self.workspace / "theme-mask")
                if custom_background is not None
                else None
            )
            hook_text = self.text_layers.build_hook(
                self.project, manifest, self.workspace / "theme-text"
            )
            review_start_text = self.text_layers.build_review_start(
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
                manifest.cta_screen,
                (),
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
                for semantic, cue in self.cue_manifest.CUES.items()
                if not cue.is_background_bed
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
            scene_beds = {
                "hook": "hook_bed",
                "review_start": "review_start_bed",
                "cta": "cta_bed",
            }
            for scene in timeline.scenes:
                semantic = scene_beds.get(scene.kind)
                if semantic is None:
                    continue
                cue = self.audio.normalize_cue(
                    self.cue_manifest,
                    semantic,
                    cue_dir,
                    duration_ms=self.timeline_builder.timing.transition_bed_duration_ms(
                        scene.kind, scene.end_ms - scene.start_ms
                    ),
                    cancel=self.cancel,
                )
                timeline.audio_events.append(
                    MinigameAudioEvent(scene.start_ms, cue.path, semantic, MinigameAudioRole.EFFECT)
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
                        custom_background=custom_background,
                        theme_text_overlay=mask,
                        review_text_overlay=review_mask,
                        crop_focus=self.project.crop_focus.get("9:16", [0.5, 0.5]),
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
        body_frame: Path | None,
        score_frame: Path,
        review_start_frame: Path,
        review_frame: Path,
        cta_frame: Path | None = None,
        *,
        custom_background: Path | None = None,
        theme_text_overlay: Path | None = None,
        review_text_overlay: Path | None = None,
        crop_focus: list[float] | None = None,
    ) -> list[MinigameVisualScene]:
        hook = next(scene for scene in timeline.scenes if scene.kind == "hook")
        review_start = next(scene for scene in timeline.scenes if scene.kind == "review_start")
        score = next(scene for scene in timeline.scenes if scene.kind == "score")
        reviews = [scene for scene in timeline.scenes if scene.kind == "review"]
        cta = next((scene for scene in timeline.scenes if scene.kind == "cta"), None)
        scenes = [MinigameVisualScene(hook_frame, hook.end_ms - hook.start_ms)]
        crop_focus = crop_focus or [0.5, 0.5]
        focus_x = max(0.0, min(1.0, float(crop_focus[0] if crop_focus else 0.5)))
        focus_y = max(
            0.0,
            min(1.0, float(crop_focus[1] if len(crop_focus) > 1 else 0.5)),
        )
        before_score = score.start_ms - hook.end_ms
        if before_score > 0:
            if custom_background is not None:
                if theme_text_overlay is None:
                    raise PipelineError("theme_scene_invalid", "Thiếu lớp nền chữ của theme.")
                scenes.append(
                    MinigameVisualScene(
                        custom_background,
                        before_score,
                        (theme_text_overlay,),
                        background_is_video=True,
                        crop_focus=(focus_x, focus_y),
                        background_start_ms=hook.end_ms,
                    )
                )
            elif body_frame is not None:
                scenes.append(MinigameVisualScene(body_frame, before_score))
            else:
                raise PipelineError("theme_scene_invalid", "Thiếu nền phần học của minigame.")
        scenes.append(MinigameVisualScene(score_frame, score.end_ms - score.start_ms))
        scenes.append(
            MinigameVisualScene(review_start_frame, review_start.end_ms - review_start.start_ms)
        )
        if custom_background is not None:
            if review_text_overlay is None:
                raise PipelineError("theme_scene_invalid", "Thiếu lớp nền chữ Review của theme.")
            scenes.extend(
                MinigameVisualScene(
                    custom_background,
                    review.end_ms - review.start_ms,
                    (review_text_overlay,),
                    fade_in_ms=0,
                    background_is_video=True,
                    crop_focus=(focus_x, focus_y),
                    background_start_ms=review.start_ms,
                )
                for review in reviews
            )
        else:
            scenes.extend(
                MinigameVisualScene(
                    review_frame,
                    review.end_ms - review.start_ms,
                    # Review content itself transitions in ASS. Never fade the whole
                    # frame here: that produced a black blink between cards.
                    fade_in_ms=0,
                )
                for review in reviews
            )
        if cta is not None:
            if cta_frame is None:
                raise PipelineError("theme_scene_invalid", "Thiếu frame CTA của minigame.")
            scenes.append(MinigameVisualScene(cta_frame, cta.end_ms - cta.start_ms, fade_in_ms=220))
        return scenes

    def _custom_background_video(self) -> Path | None:
        value = self.project.background_video.strip()
        if not value:
            return None
        path = Path(value).expanduser().resolve()
        try:
            self.runner.probe.video_artifact(path)
        except PipelineError as exc:
            raise PipelineError(
                "minigame_background_invalid",
                "Video nền không có video stream hợp lệ. Hãy chọn lại file MP4/MOV/MKV.",
                debug_detail=exc.debug_detail,
            ) from exc
        return path

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
                # Version 3 adds click-safe fades after trimming.  Do not
                # reuse cache entries created before the waveform fix.
                "speech_processing_version": 3,
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
        if self.preserve_output_name:
            safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", self.project.output_name).strip(" .")
        else:
            safe = re.sub(
                r"[^\w.-]+", "_", self.project.output_name.strip(), flags=re.UNICODE
            ).strip("._")
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


class MinigameMultipartPipeline:
    """Render each supplied part through the unchanged single-video pipeline."""

    def __init__(
        self,
        project: MinigameProject,
        content: MinigameMultipartContent,
        *,
        workspace: str | Path | None = None,
        speech_factory: Callable[[MinigameProject], MinigameSpeechService] | None = None,
        runner: FFmpegRunner | None = None,
    ) -> None:
        self.project = project
        self.content = content
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.workspace = Path(
            workspace
            or base / "QuyetBoITStudio" / "workspaces" / project.project_uuid / "minigame-multipart"
        ).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.speech_factory = speech_factory
        self.runner = runner
        self._current: MinigamePipeline | None = None
        self._cancelled = False

    def cancel_run(self) -> None:
        self._cancelled = True
        if self._current is not None:
            self._current.cancel_run()

    def run(self, progress: ProgressCallback | None = None) -> MinigameResult:
        outputs: list[Path] = []
        covers: list[Path] = []
        duration_ms = 0
        total_parts = len(self.content.parts)
        for position, (part_number, content) in enumerate(self.content.parts):
            if self._cancelled:
                raise PipelineError("cancelled", "Đã dừng tạo video minigame nhiều phần.")
            part_project = replace(
                self.project,
                topic=self.content.topic,
                vocabulary=list(content.words),
                output_name=f"{self.content.topic} Part {part_number}",
            )
            self._current = MinigamePipeline(
                part_project,
                workspace=self.workspace / f"part-{part_number}",
                speech_factory=self.speech_factory,
                runner=self.runner,
                preserve_output_name=True,
            )

            def part_progress(
                event: ProgressEvent,
                *,
                offset: int = position,
                number: int = part_number,
            ) -> None:
                fraction = (offset + event.fraction) / total_parts
                self._emit(
                    progress,
                    fraction,
                    f"Phần {number}/{total_parts} — {event.message}",
                )

            result = self._current.run(part_progress)
            outputs.extend(result.outputs)
            covers.extend(result.covers)
            duration_ms += result.duration_ms
        self._current = None
        self._emit(progress, 1.0, f"Đã hoàn thành {total_parts} video minigame")
        return MinigameResult(outputs, covers, duration_ms, self.content.total_words)

    @staticmethod
    def _emit(progress: ProgressCallback | None, fraction: float, message: str) -> None:
        if progress:
            progress(ProgressEvent(0, max(0.0, min(1.0, fraction)), message))
