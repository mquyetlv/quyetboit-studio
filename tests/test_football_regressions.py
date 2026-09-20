import json
import shutil
import subprocess
import sys
import threading
import time
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from quyetboit_studio.core.football import (
    FootballHighlightInput,
    FootballRenderConfig,
    FootballSegment,
)
from quyetboit_studio.core.models import CancellationToken, CancelledError, PipelineError
from quyetboit_studio.pipeline.football import FootballHighlightPipeline
from quyetboit_studio.services.football_worker import run_provider
from quyetboit_studio.services.media.ffmpeg import FFmpegRunner
from quyetboit_studio.services.media.football import FootballMediaService
from quyetboit_studio.services.subtitles.football import paired_clauses
from quyetboit_studio.services.youtube import YouTubeDownload, YouTubeDownloader


@pytest.fixture
def media(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg/ffprobe required")
    runner = FFmpegRunner()
    service = FootballMediaService(tmp_path, runner, FootballRenderConfig(), CancellationToken())
    # Deterministic pixels in assertions; GPU fallback is exercised separately.
    service._encoder = "libx264"
    return service


def tone(path, duration=6, frequency=550, silence_after=None):
    samples = np.arange(round(duration * 48000)) / 48000
    values = 0.3 * np.sin(2 * np.pi * frequency * samples)
    if silence_after is not None:
        values[samples >= silence_after] = 0
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes((values * 32767).astype("<i2").tobytes())
    return path


def pictures(directory, count=3):
    result = []
    for index, color in enumerate(("red", "blue", "green")[:count]):
        path = directory / f"frame{index}.jpg"
        Image.new("RGB", (640, 360), color).save(path)
        result.append(path)
    return result


def decoded(runner, video, path):
    runner.run(
        ["-i", str(video), "-vn", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(path)]
    )
    with wave.open(str(path)) as stream:
        return np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2") / 32768


def magnitude(samples, start, end, frequency):
    section = samples[round(start * 48000) : round(end * 48000)]
    t = np.arange(len(section)) / 48000
    return abs(np.mean(section * np.exp(-2j * np.pi * frequency * t)))


def test_full_voice_survives_three_images_and_event_join(media, tmp_path):
    first, second = tmp_path / "first.mp4", tmp_path / "second.mp4"
    frames = pictures(tmp_path)
    media._render_segment(frames, tone(tmp_path / "a.wav"), None, first, None, 0, 1)
    media._render_segment(frames, tone(tmp_path / "b.wav", frequency=900), None, second, None, 0, 1)
    for path in (first, second):
        streams = media.runner.probe.inspect(path)["streams"]
        assert all(abs(float(stream["duration"]) - 6) < 0.04 for stream in streams)
    output = tmp_path / "joined.mp4"
    media._concat([first, second], output, None)
    assert abs(media.runner.probe.video_artifact(output).duration_ms - 12000) < 70
    audio = decoded(media.runner, output, tmp_path / "decoded.wav")
    # No crossfade: the last words of event 1 and first words of event 2 retain
    # their own frequencies, without the other event speaking over them.
    assert magnitude(audio, 5.7, 5.95, 550) > 20 * magnitude(audio, 5.7, 5.95, 900)
    assert magnitude(audio, 6.05, 6.3, 900) > 20 * magnitude(audio, 6.05, 6.3, 550)
    assert not list(tmp_path.rglob("*.partial.*"))


def test_frame_cache_changes_with_timestamp_source_and_corruption(media, tmp_path):
    source = tmp_path / "source.mp4"
    media.runner.run(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=2",
            "-c:v",
            "libx264",
            str(source),
        ]
    )
    artifact = media.runner.probe.video_artifact(source)
    segment = FootballSegment("1", "Goal", {"finish": "00:00"}, "Goal", "Goal")
    first = media._extract_frames(artifact, segment, 0)[0]
    original = first.read_bytes()
    stamp = first.stat().st_mtime_ns
    assert media._extract_frames(artifact, segment, 0)[0].stat().st_mtime_ns == stamp
    second = media._extract_frames(artifact, replace(segment, frames={"finish": "00:01"}), 0)[0]
    assert second != first and second.read_bytes() != original
    first.write_bytes(b"x" * len(original))
    assert media._extract_frames(artifact, segment, 0)[0].read_bytes() == original
    media.runner.run(
        ["-f", "lavfi", "-i", "color=blue:size=320x180:duration=2", "-c:v", "libx264", str(source)]
    )
    third = media._extract_frames(media.runner.probe.video_artifact(source), segment, 0)[0]
    assert third != first
    with pytest.raises(PipelineError, match="vượt"):
        media._extract_frames(artifact, replace(segment, frames={"finish": "00:02"}), 0)
    with pytest.raises(PipelineError, match="không có timestamp"):
        media._extract_frames(artifact, replace(segment, frames={"finish": "not found"}), 0)


def test_clause_subtitles_use_measured_audio_and_voice_cache(media, tmp_path):
    spoken = ["Man-chét-tơ ghi bàn.", "Bru-nô kiến tạo."]
    clips = [tone(tmp_path / "a.wav", 1), tone(tmp_path / "b.wav", 2)]
    calls = []

    class Speech:
        def synthesize(self, text, output, *, cancel=None):
            calls.append(text)
            shutil.copyfile(clips[spoken.index(text)], output)

    segment = FootballSegment(
        "12",
        "Bàn thắng",
        {"finish": "00:00"},
        "Manchester ghi bàn. Bruno kiến tạo.",
        " ".join(spoken),
    )
    pipeline = FootballHighlightPipeline(
        FootballHighlightInput("https://youtu.be/test", (segment,)),
        output_folder=tmp_path,
        workspace=tmp_path,
        speech_factory=Speech,
        runner=media.runner,
        voice_cache_key={"voice": "one"},
    )
    audio, cues = pipeline._segment_speech(Speech(), segment, 0)
    assert cues == [(0, 1000, "Manchester ghi bàn."), (1000, 3000, "Bruno kiến tạo.")]
    assert media.runner.probe.audio_artifact(audio).duration_ms == 3000
    pipeline._segment_speech(Speech(), segment, 0)
    assert calls == spoken
    pipeline.voice_cache_key = {"voice": "two"}
    pipeline._segment_speech(Speech(), segment, 0)
    assert calls == spoken * 2
    assert paired_clauses("Một câu.", "Câu một. Câu hai.") == [("Một câu.", "Câu một. Câu hai.")]
    assert not list(tmp_path.rglob("*.partial.*"))

    class InterruptedSpeech:
        def synthesize(self, _text, output, *, cancel=None):
            output.with_suffix(output.suffix + ".partial").write_bytes(b"partial-capcut")
            output.with_name(f".{output.stem}.partial{output.suffix}").write_bytes(
                b"partial-google"
            )
            cancel.cancel()
            cancel.raise_if_cancelled()

    pipeline.voice_cache_key = {"voice": "interrupted"}
    pipeline.tts_suffix = ".mp3"
    with pytest.raises(CancelledError):
        pipeline._segment_speech(InterruptedSpeech(), segment, 0)
    assert not any(".partial" in path.name for path in tmp_path.rglob("*") if path.is_file())


def test_motion_music_event_caption_and_progress_end_to_end(media, tmp_path):
    source = tmp_path / "source.mp4"
    media.runner.run(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=4",
            "-c:v",
            "libx264",
            str(source),
        ]
    )
    voice = tone(tmp_path / "speech.wav", 3, 440, silence_after=1.3)
    music = tone(tmp_path / "music.wav", 1, 1300)

    class Download:
        def download(self, *_args, **_kwargs):
            return YouTubeDownload("Fixture", media.runner.probe.video_artifact(source))

    class Speech:
        def synthesize(self, _text, output, **_kwargs):
            shutil.copyfile(voice, output)

    segment = FootballSegment(
        "12:34",
        "Bàn thắng",
        {"setup": "00:00", "finish": "00:03"},
        "Manchester United ghi bàn!",
        "Man-chét-tơ Yu-nai-tịt ghi bàn!",
    )
    config = FootballRenderConfig(
        use_motion=True, music_path=str(music), top_text="100%: Bàn thắng 'đẹp'", focus_x=0.7
    )
    pipeline = FootballHighlightPipeline(
        FootballHighlightInput("https://youtu.be/test", (segment,)),
        output_folder=tmp_path / "out",
        workspace=tmp_path / "work",
        speech_factory=Speech,
        runner=media.runner,
        downloader=Download(),
        render_config=config,
    )
    pipeline.media._encoder = "libx264"
    events = []
    result = pipeline.run(events.append)
    assert abs(result.duration_ms - 3000) < 70
    assert events[-1].fraction == 1 and all(event.fraction < 1 for event in events[:-1])
    assert [event.fraction for event in events] == sorted(event.fraction for event in events)
    assert (result.output.parent / "cover.jpg").is_file()
    ass = (tmp_path / "work/subtitles/segment-001.ass").read_text(encoding="utf-8-sig")
    assert "12:34 • Bàn thắng" in ass and "Manchester United" in ass and "Yu-nai-tịt" not in ass
    audio = decoded(media.runner, result.output, tmp_path / "mixed.wav")
    assert magnitude(audio, 2.5, 2.9, 1300) > magnitude(audio, 0.6, 1.1, 1300) * 1.5
    assert magnitude(audio, 0.6, 1.1, 440) > magnitude(audio, 0.6, 1.1, 1300) * 3
    assert not list((tmp_path / "work").rglob("*.partial.*"))


def test_downloader_url_cache_and_corruption(media, tmp_path, monkeypatch):
    source = tmp_path / "fixture.mp4"
    media.runner.run(
        ["-f", "lavfi", "-i", "color=red:size=160x90:duration=1", "-c:v", "libx264", str(source)]
    )
    calls = []

    def provider(payload, cancel, progress):
        calls.append(payload["url"])
        target = Path(payload["directory"]) / "source.mp4"
        shutil.copyfile(source, target)
        return {"source": str(target), "title": "Fixture", "thumbnail": None}

    monkeypatch.setattr("quyetboit_studio.services.youtube.downloader.run_provider", provider)
    downloader = YouTubeDownloader(media.runner)
    a = downloader.download("https://youtu.be/a", tmp_path / "cache")
    b = downloader.download("https://youtu.be/b", tmp_path / "cache")
    assert a.source.path != b.source.path
    downloader.download("https://youtu.be/a", tmp_path / "cache")
    assert len(calls) == 2
    a.source.path.write_bytes(b"x" * a.source.size)
    downloader.download("https://youtu.be/a", tmp_path / "cache")
    assert len(calls) == 3


def test_gpu_fallback_once_and_no_retry_on_cancel(media, monkeypatch):
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        if "h264_nvenc" in args:
            raise PipelineError("gpu", "GPU unavailable")

    monkeypatch.setattr(media.runner, "run", run)
    media._encoder = "h264_nvenc"
    args = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "output.mp4"]
    media._run_video(args, duration_ms=1000)
    media._run_video(args, duration_ms=1000)
    assert [call[1] for call in calls] == ["h264_nvenc", "libx264", "libx264"]
    media.cancel.cancel()
    with pytest.raises(CancelledError):
        media._run_video(args, duration_ms=1000)
    assert len(calls) == 3


def test_stop_kills_owned_blocking_provider_process(tmp_path, monkeypatch):
    import quyetboit_studio.services.football_worker as worker

    actual = subprocess.Popen
    started = []

    # Substitute a local blocking process for the provider, never a network call.
    def popen(_command, **kwargs):
        process = actual(
            [sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(60)"], **kwargs
        )
        started.append(process)
        return process

    monkeypatch.setattr(worker.subprocess, "Popen", popen)
    # taskkill itself needs the actual Popen on Windows.
    actual_run = subprocess.run

    def run(*args, **kwargs):
        monkeypatch.setattr(worker.subprocess, "Popen", actual)
        try:
            return actual_run(*args, **kwargs)
        finally:
            monkeypatch.setattr(worker.subprocess, "Popen", popen)

    monkeypatch.setattr(worker.subprocess, "run", run)
    cancel = CancellationToken()
    timer = threading.Timer(0.5, cancel.cancel)
    timer.start()
    start = time.monotonic()
    try:
        with pytest.raises(CancelledError):
            run_provider({"operation": "download"}, cancel)
    finally:
        timer.cancel()
    assert time.monotonic() - start < 5
    assert started and all(process.poll() is not None for process in started)
    assert not any(thread.name == "football-provider-cancel" for thread in threading.enumerate())


def test_provider_process_failure_reports_safe_stderr_diagnostic(monkeypatch):
    import io

    import quyetboit_studio.services.football_worker as worker

    class FailedProcess:
        returncode = 1
        stdin = io.StringIO()
        stdout = io.StringIO()
        stderr = io.StringIO("ModuleNotFoundError: No module named 'yt_dlp'\napi_key=must-not-appear")

        def wait(self):
            return self.returncode

        def poll(self):
            return self.returncode

    captured = {}

    def popen(_command, **kwargs):
        captured.update(kwargs)
        return FailedProcess()

    monkeypatch.setattr(worker.subprocess, "Popen", popen)
    with pytest.raises(PipelineError) as raised:
        worker.run_provider({"operation": "download"})

    assert raised.value.error_code == "football_provider_process_failed"
    assert raised.value.log_detail == "exit_code=1; missing_module=yt_dlp"
    assert "must-not-appear" not in raised.value.log_detail
    assert str(Path(worker.__file__).resolve().parents[2]) in captured["env"]["PYTHONPATH"]


def test_provider_child_uses_utf8_for_vietnamese_json(tmp_path):
    import os

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-m", "quyetboit_studio.services.football_worker"],
        input='{"operation":"invalid"}',
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["error"] == "football_provider_failed"


def test_highlight_requires_a_google_service_account_for_its_own_session(qtbot, tmp_path, monkeypatch):
    from quyetboit_studio.football import HighlightFootballPage

    monkeypatch.chdir(tmp_path)
    page = HighlightFootballPage()
    qtbot.addWidget(page)
    page.voice_provider = "google"
    page.service_account_path = str(tmp_path / "missing-service-account.json")

    assert page._google_service_account_path() == ""


def test_render_stop_preserves_old_output_and_cleans_partial(media, tmp_path, monkeypatch):
    frames = pictures(tmp_path, 1)
    audio = tone(tmp_path / "voice.wav", duration=60)
    output = tmp_path / "old.mp4"
    output.write_bytes(b"existing-output")
    original = media.runner.run

    def slow(args, **kwargs):
        # Exercise the real FFmpeg cancellation watcher during a long render.
        if "-filter_complex" in args:
            args = ["-re", *args]
        return original(args, **kwargs)

    monkeypatch.setattr(media.runner, "run", slow)
    timer = threading.Timer(0.6, media.cancel.cancel)
    timer.start()
    try:
        with pytest.raises(CancelledError):
            media._render_segment(frames, audio, None, output, None, 0, 1)
    finally:
        timer.cancel()
    assert output.read_bytes() == b"existing-output"
    assert not list(tmp_path.rglob("*.partial.*"))


@pytest.mark.parametrize(
    "settings",
    [
        {"top_font_color": "red;bad"},
        {"subtitle_margin": 1000, "subtitle_font_size": 100},
        {"focus_x": 2},
    ],
)
def test_visual_validation_rejects_bad_filters_and_offscreen_text(settings):
    with pytest.raises(ValueError):
        FootballRenderConfig(**settings)


def test_ui_handles_wrong_json_type_and_new_settings(qtbot):
    from quyetboit_studio.football import HighlightFootballPage

    page = HighlightFootballPage()
    qtbot.addWidget(page)
    page.youtube_url.setText("https://youtu.be/test")
    page.json_editor.setPlainText(json.dumps([1]))
    assert page.validate() is None
    page.set_settings(
        {
            "show_event": False,
            "emphasize_finish": False,
            "use_motion": True,
            "music_path": "music.wav",
            "focus_x": 0.8,
            "focus_y": 0.3,
        }
    )
    settings = page.to_safe_dict()
    assert settings["use_motion"] and settings["focus_x"] == 0.8
    assert not settings["show_event"] and not settings["emphasize_finish"]
    page._set_job_controls(False)
    assert not page.config_card.isEnabled() and not page.preview_button.isEnabled()
    page._set_job_controls(True)
    assert page.config_card.isEnabled() and page.preview_button.isEnabled()


def test_preview_snapshots_voice_and_selects_requested_segment(qtbot, tmp_path, monkeypatch):
    from quyetboit_studio.football import HighlightFootballPage
    from quyetboit_studio.pipeline.football import FootballResult

    captured, opened = [], []

    class Pipeline:
        def __init__(self, content, **kwargs):
            captured.append((content, kwargs))

        def run(self, _progress):
            return FootballResult(tmp_path / "preview.mp4", 1000, 1)

    monkeypatch.setattr("quyetboit_studio.football.FootballHighlightPipeline", Pipeline)
    monkeypatch.setattr(
        "quyetboit_studio.football.QDesktopServices.openUrl", lambda url: opened.append(url)
    )
    page = HighlightFootballPage()
    qtbot.addWidget(page)
    page.youtube_url.setText("https://youtu.be/test")
    rows = json.loads(FootballHighlightInput.example_json()) * 2
    rows[1] = dict(rows[1], match_minute="20")
    page.json_editor.setPlainText(json.dumps(rows))
    page.voice_id = "voice-one"
    page.voice_rate_input.setValue(1.25)
    page.preview_index.setValue(2)
    page.start(preview=True)
    page.voice_rate_input.setValue(2)
    qtbot.waitUntil(lambda: page.thread is None)
    content, config = captured[0]
    assert len(content.segments) == 1 and content.segments[0].match_minute == "20"
    assert config["voice_cache_key"]["rate"] == 1.25
    assert config["speech_factory"]().config["rate"] == 1.25
    assert config["output_folder"].name == "previews" and opened


def test_shutdown_reaps_worker_without_main_thread_event_loop(qtbot, monkeypatch):
    from quyetboit_studio.football import HighlightFootballPage

    class Pipeline:
        def __init__(self, _content, **kwargs):
            self.cancel = kwargs["cancel"]

        def run(self, _progress):
            self.cancel.wait(10)
            self.cancel.raise_if_cancelled()

        def cancel_run(self):
            self.cancel.cancel()

    monkeypatch.setattr("quyetboit_studio.football.FootballHighlightPipeline", Pipeline)
    page = HighlightFootballPage()
    qtbot.addWidget(page)
    page.youtube_url.setText("https://youtu.be/test")
    page.voice_id = "voice"
    page.start(preview=True)
    assert page.shutdown(2000)
    qtbot.waitUntil(lambda: page.thread is None)
    assert "Đã dừng" in page.status.text()


def test_filter_paths_with_quotes_and_punctuation_render(media, tmp_path):
    folder = tmp_path / "It's [a],b;fixture"
    folder.mkdir()
    media.workspace = folder
    media.render_config = replace(media.render_config, top_text="100%: Goal's [win]")
    ass = media._write_ass("Bàn thắng!", 0, 600)
    output = tmp_path / "escaped.mp4"
    media._render_segment(
        pictures(tmp_path, 1), tone(tmp_path / "voice.wav", 0.6), ass, output, None, 0, 1
    )
    assert media.runner.probe.video_artifact(output).duration_ms >= 600


def test_download_rejects_external_final_url_before_media_transfer(tmp_path, monkeypatch):
    import types

    calls = []

    class Client:
        def __init__(self, options):
            assert options["progress_hooks"]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def extract_info(self, _url, download):
            calls.append(download)
            return {"webpage_url": "https://example.com/video"}

        def process_ie_result(self, *_args, **_kwargs):
            pytest.fail("External source must not be downloaded")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=Client))
    with pytest.raises(PipelineError, match="URL cuối"):
        YouTubeDownloader()._download("https://youtu.be/test", tmp_path)
    assert calls == [False]


def test_cancelled_download_removes_only_its_staging_files(tmp_path, monkeypatch):
    cancel = CancellationToken()
    preserved = tmp_path / "user.mp4"
    preserved.write_bytes(b"keep")

    def provider(payload, *_args):
        (Path(payload["directory"]) / "source.part").write_bytes(b"incomplete")
        cancel.cancel()
        cancel.raise_if_cancelled()

    monkeypatch.setattr("quyetboit_studio.services.youtube.downloader.run_provider", provider)
    with pytest.raises(CancelledError):
        YouTubeDownloader().download("https://youtu.be/test", tmp_path / "cache", cancel=cancel)
    assert preserved.read_bytes() == b"keep"
    assert not list(tmp_path.rglob("*.part"))


def test_json_rejects_segment_without_usable_frames():
    rows = json.loads(FootballHighlightInput.example_json())
    rows[0]["frames"] = {"setup": "not found", "finish": []}
    with pytest.raises(ValueError, match="không có timestamp"):
        FootballHighlightInput.from_json("https://youtu.be/test", json.dumps(rows))
