import json
from pathlib import Path

import pytest

from quyetboit_studio.core import (
    Artifact,
    AudioArtifact,
    PartialFailureError,
    PipelineError,
    RetryPolicy,
    SrtCue,
    VideoArtifact,
)
from quyetboit_studio.models import ProjectModel
from quyetboit_studio.pipeline import PipelineDependencies, VietSubPipeline
from quyetboit_studio.services.capcut import Voice
from quyetboit_studio.services.gemini import GeminiTranslationService
from quyetboit_studio.services.gemini.client import TranslatedBatch, TranslatedItem
from quyetboit_studio.services.subtitles import write_srt


class SelectiveGemini:
    def __init__(self, failed_ids=()):
        self.failed_ids = set(failed_ids)
        self.calls = []

    async def generate(self, model, prompt):
        items = json.loads(prompt.split("Items: ", 1)[1])
        ids = [item["id"] for item in items]
        self.calls.append(ids)
        if self.failed_ids.intersection(ids):
            raise PipelineError(
                "gemini_http_503",
                "Gemini tạm thời không phản hồi.",
                provider="gemini",
                retryable=True,
                log_detail="HTTP 503",
            )
        return TranslatedBatch(items=[
            TranslatedItem(id=item_id, translated_text=f"Việt {item_id}")
            for item_id in ids
        ])


def test_gemini_pipeline_retries_only_cues_missing_from_partial_checkpoint(tmp_path):
    project = ProjectModel(
        workspace_path=str(tmp_path),
        translation_batch_size=10,
        translation_concurrency=2,
    )
    cues = [SrtCue(index, index * 1000, index * 1000 + 900, f"中文 {index}")
            for index in range(1, 13)]
    source_path = write_srt(tmp_path / "source.srt", cues)

    failing_client = SelectiveGemini({11})
    failing_translator = GeminiTranslationService(
        failing_client, RetryPolicy(max_attempts=1)
    )
    first = VietSubPipeline(
        project,
        workspace=tmp_path,
        dependencies=PipelineDependencies(translator=failing_translator),
    )
    first._remember(2, Artifact(source_path), "source", "asr", "source-srt")

    with pytest.raises(PartialFailureError) as failure:
        first.run_step(3)

    assert failure.value.completed == 10
    assert failure.value.failed_item_ids == [11, 12]
    assert first.cache.find(3, filename="translated.partial.vi.srt")

    successful_client = SelectiveGemini()
    second = VietSubPipeline(
        project,
        workspace=tmp_path,
        dependencies=PipelineDependencies(
            translator=GeminiTranslationService(successful_client, RetryPolicy(max_attempts=1))
        ),
    )
    result = second.run_step(3)

    assert successful_client.calls == [[11, 12]]
    assert len(result.data) == 12
    assert result.data[0].translated_text == "Việt 1"
    assert result.data[-1].translated_text == "Việt 12"


class FakeProbe:
    def audio_artifact(self, path):
        return AudioArtifact(Path(path), duration_ms=500, sample_rate=48000, channels=1).refresh()

    def video_artifact(self, path):
        return VideoArtifact(Path(path), duration_ms=4000, width=1280, height=720, fps=30).refresh()


class FakeRunner:
    def __init__(self):
        self.probe = FakeProbe()

    def run(self, arguments, **kwargs):
        source = Path(arguments[arguments.index("-i") + 1])
        Path(arguments[-1]).write_bytes(source.read_bytes())


class FakeMedia:
    def __init__(self):
        self.runner = FakeRunner()


class FakeAssembler:
    def assemble(self, cues, clips, output, **kwargs):
        Path(output).write_bytes(b"timeline")
        return AudioArtifact(Path(output), duration_ms=4000, sample_rate=48000, channels=1).refresh()


class PartialTts:
    def __init__(self):
        self.fail_text = "Việt 2"
        self.calls = []

    def list_voices(self):
        return [Voice("Vietnamese", "voice", categories=("vietnamese",))]

    def synthesize_batch(self, requests, *, progress=None, on_success=None, **kwargs):
        self.calls.append([request[0] for request in requests])
        completed = 0
        failed = []
        for index, (text, _voice, output, _rate, _volume) in enumerate(requests):
            if text == self.fail_text:
                failed.append(index + 1)
                continue
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_bytes(text.encode())
            artifact = AudioArtifact(Path(output)).refresh()
            if on_success:
                on_success(index, artifact)
            completed += 1
            if progress:
                progress(completed, len(requests))
        if failed:
            raise PartialFailureError(
                "capcut_tts_partial",
                "Một phần audio thất bại.",
                completed=completed,
                total=len(requests),
                failed_item_ids=failed,
                provider="capcut",
            )
        return []


def test_tts_pipeline_checkpoints_successes_and_retries_only_failed_cue(tmp_path):
    project = ProjectModel(workspace_path=str(tmp_path), capcut_voice="voice")
    translated_cues = [
        SrtCue(index, (index - 1) * 1000, index * 1000, f"source {index}", f"Việt {index}")
        for index in range(1, 4)
    ]
    translated_path = write_srt(
        tmp_path / "translated.vi.srt", translated_cues, translated=True
    )
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    tts = PartialTts()

    def pipeline():
        value = VietSubPipeline(
            project,
            workspace=tmp_path,
            dependencies=PipelineDependencies(
                media=FakeMedia(), assembler=FakeAssembler(), tts=tts
            ),
        )
        value._remember(0, VideoArtifact(video_path), "video", "source")
        value._remember(3, Artifact(translated_path), "translated", "gemini", "translated-srt")
        return value

    with pytest.raises(PartialFailureError) as failure:
        pipeline().run_step(4)

    assert failure.value.completed == 2
    assert failure.value.failed_item_ids == [2]
    assert len(pipeline().cache.find(4, filename="cue_00001.wav")) == 1
    assert len(pipeline().cache.find(4, filename="cue_00003.wav")) == 1

    tts.fail_text = ""
    result = pipeline().run_step(4)

    assert tts.calls == [["Việt 1", "Việt 2", "Việt 3"], ["Việt 2"]]
    assert len(result.data["cues"]) == 3
    assert result.data["timeline"].path.name == "voice_timeline.wav"
