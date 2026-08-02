from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import (
    Artifact,
    ArtifactCache,
    AudioArtifact,
    CancellationToken,
    PipelineError,
    ProgressCallback,
    ProgressEvent,
    SrtCue,
    StepResult,
)
from ..core.models import AudioChunk, stable_hash
from ..models import ProjectModel
from ..services.capcut import CapCutCaptionService, CapCutTtsService
from ..services.douyin import DouyinDownloader, DownloadRequest
from ..services.gemini import GeminiTranslationService, TranslationConfig
from ..services.media import (
    AudioProcessor,
    TimelineAudioAssembler,
    VideoRenderer,
    atempo_chain,
)
from ..services.subtitles import merge_chunk_cues, parse_srt, write_srt


def workspace_for_project(project: ProjectModel, project_path: str | Path | None = None) -> Path:
    if project.workspace_path:
        return Path(project.workspace_path).expanduser().resolve()
    if project_path:
        return Path(project_path).resolve().parent / ".qbstudio-work" / project.project_uuid
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "QuyetBoITStudio" / "workspaces"
    return base / project.project_uuid


@dataclass(slots=True)
class PipelineDependencies:
    downloader: DouyinDownloader = field(default_factory=DouyinDownloader)
    media: AudioProcessor = field(default_factory=AudioProcessor)
    renderer: VideoRenderer = field(default_factory=VideoRenderer)
    assembler: TimelineAudioAssembler = field(default_factory=TimelineAudioAssembler)
    captions: CapCutCaptionService | None = None
    translator: GeminiTranslationService | None = None
    tts: CapCutTtsService | None = None


class VietSubPipeline:
    """Orchestrates services only; provider and FFmpeg details remain in service modules."""

    WEIGHTS = (0.14, 0.08, 0.20, 0.16, 0.22, 0.05, 0.10, 0.05)

    def __init__(
        self, project: ProjectModel, workspace: str | Path | None = None,
        dependencies: PipelineDependencies | None = None,
    ) -> None:
        self.project = project
        self.workspace = Path(workspace or workspace_for_project(project)).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.cache = ArtifactCache(self.workspace, project.artifact_manifest)
        self.services = dependencies or PipelineDependencies()
        self.cancel = CancellationToken()
        self.results: dict[int, StepResult] = {}
        self.force = False

    def cancel_run(self) -> None:
        self.cancel.cancel()

    def preflight(self) -> dict[str, Any]:
        media = self.services.media.runner.preflight()
        return {
            "media": media,
            "gemini": bool(self.services.translator and self.services.translator.client.api_key),
            "capcut": bool(self.services.captions and self.services.tts),
            "browser": {"name": self.project.browser, "profile": self.project.browser_profile},
        }

    def run_all(self, progress: ProgressCallback | None = None, *, force: bool = False) -> list[StepResult]:
        self.force = force
        output = []
        for step in range(8):
            output.append(self.run_step(step, progress))
        return output

    def run_step(self, step: int, progress: ProgressCallback | None = None) -> StepResult:
        if not 0 <= step < 8:
            raise ValueError("Bước pipeline không hợp lệ.")
        self.cancel.raise_if_cancelled()
        started = time.monotonic()

        def emit(event: ProgressEvent) -> None:
            if progress:
                progress(event)

        methods = (self._download, self._audio, self._caption, self._translate, self._tts,
                   self._mix, self._studio, self._export)
        try:
            result = methods[step](emit)
            result.elapsed_seconds = time.monotonic() - started
            self.results[step] = result
            return result
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                "pipeline_step_failed", f"Bước {step + 1} thất bại.",
                debug_detail=str(exc),
            ) from exc

    def _cached(self, step: int, input_hash: str, config_hash: str, item_id: str = "") -> Artifact | None:
        return None if self.force else self.cache.get(step, input_hash, config_hash, item_id)

    def _remember(self, step: int, artifact: Artifact, input_hash: str, config_hash: str, item_id: str = "") -> Artifact:
        artifact.input_hash, artifact.config_hash = input_hash, config_hash
        return self.cache.put(step, artifact, item_id=item_id)

    def _previous_artifact(self, step: int, filename: str | None = None) -> Artifact:
        result = self.results.get(step)
        if result:
            if filename:
                found = next((item for item in result.artifacts if item.path.name == filename), None)
                if found: return found
            elif result.artifacts:
                return result.artifacts[0]
        cached = self.cache.find(step, filename=filename)
        if cached:
            return cached[-1]
        raise PipelineError("missing_dependency", f"Thiếu artifact từ bước {step + 1}.")

    def _restored_audio_chunks(self) -> list[AudioChunk]:
        chunks = [item for item in self.cache.find(1) if isinstance(item, AudioChunk)]
        return sorted(chunks, key=lambda item: item.index)

    def _download(self, progress: ProgressCallback) -> StepResult:
        cookies_file = self.project.__dict__.get("_douyin_cookies_file")
        metadata = self.services.downloader.probe(
            self.project.source_url, (self.project.browser, self.project.browser_profile), cookies_file
        )
        input_hash = stable_hash({"url": metadata.webpage_url, "id": metadata.id})
        config_hash = stable_hash({"format": "bestvideo*+bestaudio/best", "format_id": metadata.format_id})
        cached = self._cached(0, input_hash, config_hash)
        if cached:
            return StepResult(0, [cached], metadata, cache_hit=True)
        artifact = self.services.downloader.download(
            DownloadRequest(
                self.project.source_url, self.workspace / "source", self.project.browser,
                self.project.browser_profile, cookies_file=Path(cookies_file) if cookies_file else None,
            ),
            progress, self.cancel,
        )
        remembered = self._remember(0, artifact, input_hash, config_hash)
        self.project.metadata = {"id": metadata.id, "title": metadata.title,
                                 "duration_seconds": metadata.duration_seconds,
                                 "resolution": f"{metadata.width} × {metadata.height}"}
        return StepResult(0, [remembered], metadata)

    def _audio(self, progress: ProgressCallback) -> StepResult:
        video = self._previous_artifact(0)
        input_hash, config_hash = video.checksum, stable_hash(self.project.audio_settings)
        audio_cached = self._cached(1, input_hash, config_hash, "audio")
        video_probe = self.services.media.runner.probe.video_artifact(video.path)
        if audio_cached:
            audio = self.services.media.runner.probe.audio_artifact(audio_cached.path)
        else:
            audio = self.services.media.extract(video, self.workspace / "audio" / "source.m4a", progress=progress, cancel=self.cancel)
            audio = self._remember(1, audio, input_hash, config_hash, "audio")  # type: ignore[assignment]
        from ..services.media.ffmpeg import SplitConfig
        config = SplitConfig(
            target_seconds=int(self.project.audio_settings.get("chunk_seconds", 240)),
            min_seconds=int(self.project.audio_settings.get("chunk_min_seconds", 180)),
            max_seconds=int(self.project.audio_settings.get("chunk_max_seconds", 300)),
            overlap_ms=int(self.project.audio_settings.get("overlap_ms", 250)),
        )
        chunks = self.services.media.split_on_silence(audio, self.workspace / "audio" / "chunks", config,
                                                       progress=progress, cancel=self.cancel)
        artifacts: list[Artifact] = [audio]
        for chunk in chunks:
            artifacts.append(self._remember(1, chunk, audio.checksum, config_hash, f"chunk-{chunk.index}"))
        return StepResult(1, artifacts, {"chunks": chunks, "video_duration_ms": video_probe.duration_ms}, cache_hit=bool(audio_cached))

    def _caption(self, progress: ProgressCallback) -> StepResult:
        if not self.services.captions or not self.services.captions.uploader:
            raise PipelineError("capcut_not_connected", "Hãy đăng nhập CapCut trước khi chạy ASR.", provider="capcut")
        # Restore may only contain cookies. Resolve and persist UID/workspace before upload.
        self.services.captions.client.ensure_session()
        audio_result = self.results.get(1)
        chunks = audio_result.data["chunks"] if audio_result else self._restored_audio_chunks()
        if not chunks:
            raise PipelineError("missing_audio_chunks", "Hãy chạy bước tách audio trước.")
        merged_input = stable_hash([chunk.checksum for chunk in chunks])
        config_hash = stable_hash({"language": self.project.source_language, "words_per_line": 55})
        cached = self._cached(2, merged_input, config_hash, "source-srt")
        if cached:
            cues = parse_srt(cached.path.read_bytes())
            return StepResult(2, [cached], cues, cache_hit=True)
        chunk_cues = []
        for index, chunk in enumerate(chunks):
            self.cancel.raise_if_cancelled()
            item_id = f"chunk-{chunk.index}"
            chunk_cached = self._cached(2, chunk.checksum, config_hash, item_id)
            if chunk_cached:
                cues = parse_srt(chunk_cached.path.read_bytes())
            else:
                resource = self.services.captions.uploader.upload_asset(chunk.path, cancel=self.cancel)
                cues = self.services.captions.transcribe_resource(resource, self.project.source_language, cancel=self.cancel)
                chunk_path = write_srt(self.workspace / "captions" / f"chunk_{chunk.index:03d}.srt", cues)
                self._remember(2, Artifact(chunk_path), chunk.checksum, config_hash, item_id)
            chunk_cues.append((chunk.source_start_ms, cues))
            progress(ProgressEvent(2, (index + 1) / len(chunks), "CapCut ASR", item_id=str(index)))
        cues = merge_chunk_cues(chunk_cues)
        path = write_srt(self.workspace / "source.srt", cues)
        artifact = self._remember(2, Artifact(path), merged_input, config_hash, "source-srt")
        return StepResult(2, [artifact], cues)

    def _translate(self, progress: ProgressCallback) -> StepResult:
        if not self.services.translator:
            raise PipelineError("gemini_not_configured", "Hãy nhập GEMINI_API_KEY trước khi dịch.", provider="gemini")
        source = self._previous_artifact(2, "source.srt")
        cues = self.results.get(2).data if self.results.get(2) else parse_srt(source.path.read_bytes())
        config = TranslationConfig(self.project.gemini_model, self.project.translation_batch_size,
                                   self.project.translation_concurrency,
                                   self.project.custom_context or self.project.translation_context)
        config_hash = stable_hash(config)
        cached = self._cached(3, source.checksum, config_hash, "translated-srt")
        if cached:
            cached_cues = parse_srt(cached.path.read_bytes())
            for cue in cached_cues:
                cue.translated_text = cue.text
            return StepResult(3, [cached], cached_cues, cache_hit=True)
        translated = self.services.translator.translate_sync(cues, config, progress=progress, cancel=self.cancel)
        path = write_srt(self.workspace / "translated.vi.srt", translated.cues, translated=True)
        artifact = self._remember(3, Artifact(path), source.checksum, config_hash, "translated-srt")
        return StepResult(3, [artifact], translated.cues, warnings=[f"Gemini retry: {translated.retries}"] if translated.retries else [])

    def _tts(self, progress: ProgressCallback) -> StepResult:
        if not self.services.tts:
            raise PipelineError("capcut_not_connected", "Hãy đăng nhập CapCut trước khi tạo voice.", provider="capcut")
        translated = self._previous_artifact(3, "translated.vi.srt")
        cues = self.results.get(3).data if self.results.get(3) else parse_srt(translated.path.read_bytes())
        if not self.results.get(3):
            for cue in cues:
                cue.translated_text = cue.text
        voices = self.services.tts.list_voices()
        voice = next((item for item in voices if item.speaker == self.project.capcut_voice), None)
        if voice is None:
            voice = next(
                (item for item in voices if "vietnamese" in item.categories),
                voices[0] if voices else None,
            )
        if not voice:
            raise PipelineError("capcut_voice_missing", "Không tìm thấy voice tiếng Việt trên CapCut.", provider="capcut")
        self.project.capcut_voice = voice.speaker
        clips: list[AudioArtifact | None] = [None] * len(cues)
        runner = self.services.media.runner
        pending: list[tuple[int, SrtCue, Path, str]] = []
        for index, cue in enumerate(cues):
            cue_hash = stable_hash({"text": cue.translated_text, "voice": voice.speaker, **self.project.tts_settings})
            cached = self._cached(4, cue_hash, "pcm48", f"cue-{cue.id}")
            wav = self.workspace / "voice" / f"cue_{cue.id:05d}.wav"
            if cached:
                clips[index] = runner.probe.audio_artifact(cached.path)
            else:
                raw = self.workspace / "voice" / f"cue_{cue.id:05d}.audio"
                pending.append((index, cue, raw, cue_hash))
        if pending:
            requests = [
                (cue.translated_text, voice, raw,
                 float(self.project.tts_settings.get("speed", 1)),
                 int(self.project.tts_settings.get("volume", 100)))
                for _, cue, raw, _ in pending
            ]
            self.services.tts.synthesize_batch(
                requests,
                concurrency=max(1, min(50, int(self.project.tts_settings.get("concurrency", 25)))),
                cancel=self.cancel,
                progress=lambda completed, total: progress(
                    ProgressEvent(4, completed / max(1, total), "CapCut TTS")
                ),
            )
        for index, cue, raw, cue_hash in pending:
            self.cancel.raise_if_cancelled()
            wav = self.workspace / "voice" / f"cue_{cue.id:05d}.wav"
            measured = runner.probe.audio_artifact(raw)
            factor = measured.duration_ms / max(1, cue.duration_ms)
            filters = atempo_chain(factor) if factor > 1 else "apad"
            duration = cue.duration_ms / 1000
            partial = wav.with_name(wav.stem + ".partial.wav")
            wav.parent.mkdir(parents=True, exist_ok=True)
            runner.run([
                "-i", str(raw), "-af", filters, "-t", f"{duration:.3f}", "-ac", "1",
                "-ar", "48000", "-c:a", "pcm_s16le", str(partial),
            ], cancel=self.cancel)
            partial.replace(wav)
            clip = runner.probe.audio_artifact(wav)
            clip = self._remember(4, clip, cue_hash, "pcm48", f"cue-{cue.id}")  # type: ignore[assignment]
            if factor > 1.5:
                cue.warnings.append(f"Voice speed-up {factor:.2f}×")
            clips[index] = clip
        ready_clips = [clip for clip in clips if clip is not None]
        if len(ready_clips) != len(cues):
            raise PipelineError("capcut_tts_incomplete", "Chưa tạo đủ voice cho các câu.", provider="capcut")
        video = self.services.media.runner.probe.video_artifact(self._previous_artifact(0).path)
        timeline = self.services.assembler.assemble(cues, ready_clips, self.workspace / "voice_timeline.wav", video_duration_ms=video.duration_ms)
        timeline = self._remember(4, timeline, translated.checksum, stable_hash({"voice": voice.speaker, "settings": self.project.tts_settings}), "timeline")
        return StepResult(4, [*ready_clips, timeline], {"cues": cues, "timeline": timeline})

    def _mix(self, progress: ProgressCallback) -> StepResult:
        video = self.services.media.runner.probe.video_artifact(self._previous_artifact(0).path)
        tts_result = self.results.get(4)
        voice = tts_result.data["timeline"] if tts_result else self._previous_artifact(4, "voice_timeline.wav")
        config_hash = stable_hash(self.project.mix_settings)
        cached = self._cached(5, stable_hash([video.checksum, voice.checksum]), config_hash)
        if cached: return StepResult(5, [cached], cache_hit=True)
        mixed = self.services.renderer.mix_audio(video, voice, self.workspace / "mixed.mp4", self.project.mix_settings, cancel=self.cancel, progress=progress)
        mixed = self._remember(5, mixed, stable_hash([video.checksum, voice.checksum]), config_hash)
        return StepResult(5, [mixed])

    def _studio(self, progress: ProgressCallback) -> StepResult:
        mixed = self.services.media.runner.probe.video_artifact(self._previous_artifact(5).path)
        subtitle = self._previous_artifact(3, "translated.vi.srt")
        config_hash = stable_hash(self.project.studio_settings)
        cached = self._cached(6, stable_hash([mixed.checksum, subtitle.checksum]), config_hash)
        if cached: return StepResult(6, [cached], cache_hit=True)
        rendered = self.services.renderer.render(mixed, self.workspace / "studio.mp4", self.project.studio_settings,
                                                 self.project.export_settings, subtitle=subtitle.path,
                                                 cancel=self.cancel, progress=progress)
        rendered = self._remember(6, rendered, stable_hash([mixed.checksum, subtitle.checksum]), config_hash)
        return StepResult(6, [rendered])

    def _export(self, progress: ProgressCallback) -> StepResult:
        rendered = self._previous_artifact(6)
        settings = self.project.export_settings
        folder = Path(settings.get("folder", "output")).expanduser()
        if not folder.is_absolute(): folder = Path.cwd() / folder
        folder.mkdir(parents=True, exist_ok=True)
        stem, suffix = str(settings.get("filename", "video_vietsub")), str(settings.get("format", "mp4"))
        target = folder / f"{stem}.{suffix}"
        counter = 2
        while target.exists():
            target = folder / f"{stem}-{counter}.{suffix}"; counter += 1
        partial = target.with_name(f".{target.stem}.partial{target.suffix}")
        shutil.copy2(rendered.path, partial)
        checked = self.services.media.runner.probe.video_artifact(partial)
        partial.replace(target); checked.path = target; checked.refresh()
        self.project.output = {"video": str(target.resolve()), "size": checked.size,
                               "duration_ms": checked.duration_ms, "workspace": str(self.workspace)}
        progress(ProgressEvent(7, 1, "Đã xuất video"))
        return StepResult(7, [checked], self.project.output)
