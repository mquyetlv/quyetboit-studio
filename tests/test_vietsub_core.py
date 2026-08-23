import json
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from quyetboit_studio.core import (
    Artifact,
    ArtifactCache,
    AudioArtifact,
    AudioChunk,
    PartialFailureError,
    PipelineError,
    ProgressEvent,
    SrtCue,
    VideoArtifact,
)
from quyetboit_studio.core.models import stable_hash
from quyetboit_studio.core.retry import RetryPolicy
from quyetboit_studio.models import ProjectModel, SourceMode
from quyetboit_studio.pipeline import PipelineDependencies, VietSubPipeline
from quyetboit_studio.pipeline.vietsub import studio_config_hash
from quyetboit_studio.services.capcut import (
    AwsV4Signer,
    CapCutTtsService,
    StsCredentials,
    Voice,
    crc32_hex,
    make_sign,
    xor5_hex,
)
from quyetboit_studio.services.douyin import (
    DouyinDownloader,
    VideoMetadata,
    convert_cookie_json_to_netscape,
    resolve_browser_profile,
    validate_douyin_url,
)
from quyetboit_studio.services.media import (
    AudioProcessor,
    FFmpegRunner,
    VideoConcatenator,
    atempo_chain,
)
from quyetboit_studio.services.media.ffmpeg import MediaProbe, SplitConfig
from quyetboit_studio.services.subtitles import (
    find_untranslated,
    merge_chunk_cues,
    parse_srt,
    serialize_srt,
)


def test_studio_renderer_version_invalidates_legacy_cache_key():
    settings = ProjectModel().studio_settings

    assert studio_config_hash(settings) != stable_hash(settings)
    changed = {**settings, "zoom": 125}
    assert studio_config_hash(changed) != studio_config_hash(settings)


def test_ffprobe_output_is_always_decoded_as_utf8(monkeypatch) -> None:
    received: list[dict[str, object]] = []

    def fake_run(_command, **kwargs):
        received.append(kwargs)
        return SimpleNamespace(stdout='{"streams": [], "format": {}}')

    monkeypatch.setattr("quyetboit_studio.services.media.ffmpeg.subprocess.run", fake_run)

    assert MediaProbe("ffprobe").inspect("video.mp4") == {"streams": [], "format": {}}
    assert received == [
        {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "check": True,
        }
    ]


def test_ffmpeg_preflight_output_is_always_decoded_as_utf8(monkeypatch) -> None:
    received: list[dict[str, object]] = []

    def fake_run(command, **kwargs):
        received.append(kwargs)
        output = "ffmpeg version test" if "-version" in command else "libx264"
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr("quyetboit_studio.services.media.ffmpeg.shutil.which", lambda value: value)
    monkeypatch.setattr("quyetboit_studio.services.media.ffmpeg.subprocess.run", fake_run)

    preflight = FFmpegRunner().preflight()

    assert preflight["encoder"] == "libx264"
    assert len(received) == 3
    assert all(
        options
        == {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "check": True,
        }
        for options in received
    )


def test_douyin_url_is_https_and_domain_bounded():
    assert validate_douyin_url("https://v.douyin.com/abc") == "https://v.douyin.com/abc"
    for invalid in (
        "http://douyin.com/x",
        "https://evil-douyin.com/x",
        "https://douyin.com@evil.test/x",
    ):
        with pytest.raises(PipelineError):
            validate_douyin_url(invalid)
    assert (
        validate_douyin_url("https://www.douyin.com/jingxuan?modal_id=7660401665056558388")
        == "https://www.douyin.com/video/7660401665056558388"
    )


def test_chrome_display_name_maps_to_profile_directory(tmp_path):
    state = tmp_path / "Google" / "Chrome" / "User Data" / "Local State"
    state.parent.mkdir(parents=True)
    state.write_text(
        '{"profile":{"info_cache":{"Profile 6":{"name":"YTB - Cát Cát Review"}}}}',
        encoding="utf-8",
    )
    assert resolve_browser_profile("chrome", "YTB - Cát Cát Review", tmp_path) == "Profile 6"


def test_cookie_extension_json_converts_to_netscape_and_filters_domains(tmp_path):
    source = tmp_path / "cookies.json"
    source.write_text(
        '{"url":"https://www.douyin.com","cookies":['
        '{"domain":".douyin.com","hostOnly":false,"httpOnly":true,"secure":true,'
        '"path":"/","expirationDate":1800000000,"name":"sessionid","value":"secret"},'
        '{"domain":"evil.test","name":"other","value":"leak"}]}',
        encoding="utf-8",
    )
    output = tmp_path / "cookies.txt"
    assert convert_cookie_json_to_netscape(source, output) == 1
    converted = output.read_text(encoding="utf-8")
    assert "#HttpOnly_.douyin.com" in converted
    assert "sessionid\tsecret" in converted
    assert "evil.test" not in converted


def test_douyin_series_resolves_mix_from_episode_and_paginates_in_order():
    class FakeExtractor:
        def _download_json(self, endpoint, _video_id, _message, query):
            if endpoint.endswith("/aweme/detail/"):
                return {
                    "aweme_detail": {
                        "mix_info": {
                            "mix_id": "900",
                            "mix_name": "Bộ phim",
                        }
                    }
                }
            if query["cursor"] == 0:
                return {
                    "aweme_list": [
                        {"aweme_id": "101", "desc": "Tập 1", "duration": 12_000},
                        {"aweme_id": "102", "desc": "Tập 2", "duration": 13_000},
                    ],
                    "has_more": True,
                    "cursor": 2,
                }
            return {
                "aweme_list": [
                    {"aweme_id": "102", "desc": "Tập 2 trùng", "duration": 13_000},
                    {"aweme_id": "103", "desc": "Tập 3", "duration": 14_000},
                ],
                "has_more": False,
            }

    class FakeYdl:
        def __init__(self, _options):
            self.extractor = FakeExtractor()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "id": "102",
                "title": "Tập 2",
                "duration": 13,
                "webpage_url": "https://www.douyin.com/video/102",
            }

        def get_info_extractor(self, name):
            assert name == "Douyin"
            return self.extractor

    series = DouyinDownloader(ydl_factory=FakeYdl).resolve_series(
        "https://www.douyin.com/video/102",
    )

    assert series.id == "900"
    assert series.title == "Bộ phim"
    assert [episode.id for episode in series.episodes] == ["101", "102", "103"]
    assert series.episodes[0].duration_seconds == 12


def test_douyin_series_null_list_reports_provider_block_not_empty_series():
    class FakeExtractor:
        def _download_json(self, *_args, **_kwargs):
            return {"status_code": 0, "aweme_list": None}

    class FakeYdl:
        def __init__(self, _options):
            self.extractor = FakeExtractor()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_info_extractor(self, name):
            assert name == "Douyin"
            return self.extractor

    with pytest.raises(PipelineError) as captured:
        DouyinDownloader(ydl_factory=FakeYdl).resolve_series(
            "https://www.douyin.com/collection/7651800697272272931",
        )

    assert captured.value.error_code == "douyin_series_list_unavailable"
    assert "không trả danh sách tập" in captured.value.user_message


def test_multi_source_pipeline_downloads_in_order_and_concatenates(tmp_path):
    class FakeDownloader:
        def probe(self, url, _auth, _cookies):
            video_id = url.rsplit("/", 1)[-1]
            return VideoMetadata(
                video_id,
                f"Video {video_id}",
                1,
                f"https://www.douyin.com/video/{video_id}",
            )

        def download(self, request, progress, _cancel):
            video_id = request.url.rsplit("/", 1)[-1]
            path = request.directory / f"{video_id}.mp4"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(video_id.encode())
            progress(ProgressEvent(0, 1, "done"))
            return VideoArtifact(
                path,
                duration_ms=1000,
                width=1080,
                height=1920,
                fps=30,
            ).refresh()

    class FakeConcatenator:
        def __init__(self):
            self.ids = []

        def concatenate(self, videos, output, *, progress, cancel):
            self.ids = [video.path.stem for video in videos]
            output.write_bytes(b"combined")
            progress(ProgressEvent(0, 1, "combined"))
            return VideoArtifact(
                output,
                duration_ms=2000,
                width=1080,
                height=1920,
                fps=30,
            ).refresh()

    concatenator = FakeConcatenator()
    project = ProjectModel(
        source_mode=SourceMode.MULTI.value,
        source_urls=[
            "https://www.douyin.com/video/2",
            "https://www.douyin.com/video/1",
        ],
        workspace_path=str(tmp_path),
    )
    pipeline = VietSubPipeline(
        project,
        workspace=tmp_path,
        dependencies=PipelineDependencies(
            downloader=FakeDownloader(),  # type: ignore[arg-type]
            concatenator=concatenator,  # type: ignore[arg-type]
        ),
    )
    result = pipeline.run_step(0)

    assert concatenator.ids == ["2", "1"]
    assert result.artifacts[0].path.name == "combined.mp4"
    assert project.metadata["video_count"] == 2


def test_video_concatenator_normalizes_and_validates_real_ffmpeg_output(tmp_path, monkeypatch):
    runner = FFmpegRunner()
    try:
        runner.preflight()
    except PipelineError:
        pytest.skip("FFmpeg/ffprobe không có trong PATH")
    monkeypatch.setattr(
        runner,
        "preflight",
        lambda: {"encoder": "libx264", "rubberband": False},
    )
    inputs = []
    for index, size in enumerate(("320x240", "240x320")):
        path = tmp_path / f"input-{index}.mp4"
        runner.run(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={size}:d=0.45",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.45",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(path),
            ]
        )
        inputs.append(runner.probe.video_artifact(path))

    output = tmp_path / "combined.mp4"
    combined = VideoConcatenator(runner).concatenate(inputs, output)

    assert combined.path == output
    assert combined.width == 320
    assert combined.height == 240
    assert combined.duration_ms >= 800
    assert combined.metadata["source_count"] == 2
    assert not (tmp_path / ".combined.partial.mp4").exists()
    assert not (tmp_path / ".combined.concat-filters.txt").exists()


def test_srt_roundtrip_merge_overlap_and_validation():
    cues = parse_srt("\ufeff1\n00:00:00,000 --> 00:00:01,000\n你好\n")
    assert serialize_srt(cues, bom=True).startswith("\ufeff1")
    merged = merge_chunk_cues(
        [
            (0, cues),
            (500, [SrtCue(1, 0, 600, "你好")]),
            (1000, [SrtCue(1, 0, 500, "不同")]),
        ]
    )
    assert [cue.text for cue in merged] == ["你好", "不同"]
    cues[0].translated_text = "你好"
    assert find_untranslated(cues) == cues
    cues[0].translated_text = "Xin chào"
    assert find_untranslated(cues) == []


def test_split_boundary_prefers_nearby_silence():
    config = SplitConfig(target_seconds=240, min_seconds=180, max_seconds=300, search_seconds=30)
    bounds = AudioProcessor.boundaries(700_000, [235_000, 481_000], config)
    assert bounds == [(0, 235_000), (235_000, 481_000), (481_000, 700_000)]


def test_atempo_chain_supports_out_of_range_values():
    assert atempo_chain(4.5).startswith("atempo=2,atempo=2")
    assert atempo_chain(0.2).startswith("atempo=0.5,atempo=0.5")


def test_artifact_cache_checks_checksum_and_invalidates_downstream(tmp_path):
    cache = ArtifactCache(tmp_path)
    first = tmp_path / "first.txt"
    first.write_text("first", encoding="utf-8")
    second = tmp_path / "second.txt"
    second.write_text("second", encoding="utf-8")
    cache.put(1, Artifact(first, input_hash="a", config_hash="b"))
    cache.put(2, Artifact(second, input_hash="c", config_hash="d"))
    assert cache.get(1, "a", "b") is not None
    first.write_text("tampered", encoding="utf-8")
    assert cache.get(1, "a", "b") is None
    assert cache.invalidate(2) == [second.resolve()]


def test_artifact_cache_restores_typed_chunks_and_pipeline_dependency(tmp_path):
    cache = ArtifactCache(tmp_path)
    chunk_path = tmp_path / "audio_001.m4a"
    chunk_path.write_bytes(b"audio")
    chunk = AudioChunk(
        chunk_path,
        input_hash="audio",
        config_hash="split",
        index=0,
        source_start_ms=0,
        source_end_ms=1000,
    )
    cache.put(1, chunk, item_id="chunk-0")
    mixed_path = tmp_path / "mixed.mp4"
    mixed_path.write_bytes(b"video")
    cache.put(5, Artifact(mixed_path, input_hash="video", config_hash="mix"))

    restored = ArtifactCache(tmp_path).find(1)
    assert len(restored) == 1
    assert isinstance(restored[0], AudioChunk)
    assert restored[0].source_end_ms == 1000

    pipeline = VietSubPipeline(ProjectModel(workspace_path=str(tmp_path)), workspace=tmp_path)
    assert pipeline._previous_artifact(5).path == mixed_path.resolve()


class FakeVoiceClient:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def list_voices(self, category: str) -> list[Voice]:
        with self.lock:
            self.calls[category] = self.calls.get(category, 0) + 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.02)
            if category == "english" and self.calls[category] == 1:
                raise PipelineError("temporary", "Thử lại", retryable=True)
            return [Voice("Shared", "speaker-1", category=category, categories=(category,))]
        finally:
            with self.lock:
                self.active -= 1


def test_capcut_voice_catalog_is_concurrent_retried_merged_and_cached(tmp_path):
    client = FakeVoiceClient()
    service = CapCutTtsService(
        client,  # type: ignore[arg-type]
        RetryPolicy(max_attempts=2, base_delay=0, max_delay=0, full_jitter=False),
        cache_path=tmp_path / "voices.json",
        categories=("vietnamese", "english", "female_voice", "male_voice"),
        catalog_concurrency=4,
    )
    voices = service.list_voices()
    assert client.max_active > 1
    assert client.calls["english"] == 2
    assert voices[0].categories == ("vietnamese", "english", "female_voice", "male_voice")
    assert service.catalog_fetched_at > 0

    before = dict(client.calls)
    cached = service.list_voices()
    assert cached == voices
    assert client.calls == before

    cache_payload = json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))
    cache_payload["fetched_at"] = 1
    (tmp_path / "voices.json").write_text(json.dumps(cache_payload), encoding="utf-8")
    old_cache_client = FakeVoiceClient()
    old_cache_service = CapCutTtsService(
        old_cache_client,  # type: ignore[arg-type]
        cache_path=tmp_path / "voices.json",
        categories=("vietnamese", "english", "female_voice", "male_voice"),
    )
    assert old_cache_service.list_voices() == voices
    assert old_cache_service.catalog_fetched_at == 1
    assert old_cache_client.calls == {}

    project = ProjectModel()
    assert project.tts_settings["concurrency"] == 25


class FakeBatchTtsService(CapCutTtsService):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def synthesize(self, text, voice, output, **kwargs):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.02)
            output.write_bytes(text.encode())
            return AudioArtifact(output).refresh()
        finally:
            with self.lock:
                self.active -= 1


def test_capcut_tts_batch_uses_bounded_concurrency_and_preserves_order(tmp_path):
    service = FakeBatchTtsService()
    voice = Voice("Test", "speaker")
    requests = [(str(index), voice, tmp_path / f"{index}.audio", 1.0, 100) for index in range(6)]
    results = service.synthesize_batch(requests, concurrency=3)
    assert service.max_active == 3
    assert [item.path.read_text() for item in results] == [str(index) for index in range(6)]


def test_capcut_tts_batch_keeps_successes_when_one_item_fails(tmp_path):
    class OneFailure(FakeBatchTtsService):
        def synthesize(self, text, voice, output, **kwargs):
            if text == "2":
                raise PipelineError(
                    "capcut_tts_download",
                    "Không tải được audio TTS.",
                    provider="capcut",
                )
            return super().synthesize(text, voice, output, **kwargs)

    service = OneFailure()
    voice = Voice("Test", "speaker")
    requests = [(str(index), voice, tmp_path / f"{index}.audio", 1.0, 100) for index in range(5)]
    checkpointed = []

    with pytest.raises(PartialFailureError) as failure:
        service.synthesize_batch(
            requests,
            concurrency=3,
            on_success=lambda index, _artifact: checkpointed.append(index),
        )

    assert failure.value.completed == 4
    assert failure.value.failed_item_ids == [3]
    assert sorted(checkpointed) == [0, 1, 3, 4]


def test_capcut_sign_crc_obfuscation_and_sigv4_are_deterministic():
    assert xor5_hex("A") == "44"
    assert crc32_hex(b"abc") == "352441c2"
    assert (
        make_sign("/lv/v1/caption/query", "8.4.0", 1)["sign"]
        == make_sign("/lv/v1/caption/query", "8.4.0", 1)["sign"]
    )
    signer = AwsV4Signer()
    headers = signer.sign(
        "GET",
        "https://example.com/?Version=1&Action=Apply",
        StsCredentials("AK", "SK", "TOKEN"),
        now=datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    assert headers["X-Amz-Date"] == "20250102T030405Z"
    assert "20250102/ap-singapore-1/vod/aws4_request" in headers["Authorization"]


def test_project_v1_migrates_to_v2():
    legacy = ProjectModel().to_dict()
    legacy["schema_version"] = 1
    for key in ("project_uuid", "source_language", "gemini_model", "translation_batch_size"):
        legacy.pop(key)
    migrated = ProjectModel.from_dict(legacy)
    assert migrated.schema_version == 2
    assert migrated.source_language == "zh-CN"
    assert migrated.translation_batch_size == 50
