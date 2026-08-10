from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from quyetboit_studio.core.minigame import (
    MinigameProject,
    MinigameScene,
    MinigameTimeline,
    MinigameTimelineBuilder,
    VocabularyItem,
    score_ranges,
)
from quyetboit_studio.minigame import MinigamePage
from quyetboit_studio.minigame_voice import MinigameVoiceDialog
from quyetboit_studio.services.media import FFmpegRunner
from quyetboit_studio.services.media.minigame import (
    MinigameAudioService,
    MinigameSubtitleBuilder,
    MinigameVideoRenderer,
)
from quyetboit_studio.services.minigame_tts import CatalogVoice, google_voice_family


def _item(index: int = 1) -> VocabularyItem:
    return VocabularyItem(
        f"nghĩa {index}", f"word{index}", f"/wɜːd {index}/", f"This is example {index}."
    )


def test_project_requires_every_user_supplied_field(tmp_path: Path) -> None:
    video = tmp_path / "background.mp4"
    video.write_bytes(b"fixture")
    project = MinigameProject(
        topic="Travel",
        background_video=str(video),
        tts_provider="capcut",
        voice="English Voice",
        vocabulary=[VocabularyItem("du lịch", "travel", "", "I love to travel.")],
    )

    with pytest.raises(ValueError, match="phiên âm IPA"):
        project.validate()


def test_safe_project_serialization_excludes_service_account_path() -> None:
    project = MinigameProject(service_account_path="C:/secret/service-account.json")

    assert project.to_safe_dict()["service_account_path"] == ""


def test_score_ranges_are_dynamic() -> None:
    assert score_ranges(5) == ((0, 2), (3, 4), (5, 5))
    assert score_ranges(7) == ((0, 4), (5, 6), (7, 7))
    assert score_ranges(1) == ((0, 0), (1, 0), (1, 1))


def test_timeline_uses_all_pronunciations_and_review_examples(tmp_path: Path) -> None:
    normal = [tmp_path / "normal-1.wav", tmp_path / "normal-2.wav"]
    slow = [tmp_path / "slow-1.wav", tmp_path / "slow-2.wav"]
    examples = [tmp_path / "example-1.wav", tmp_path / "example-2.wav"]
    durations = {
        normal[0]: 500,
        normal[1]: 600,
        slow[0]: 800,
        slow[1]: 900,
        examples[0]: 1_000,
        examples[1]: 1_100,
    }

    timeline = MinigameTimelineBuilder().build(
        [_item(1), _item(2)], normal, slow, examples, durations
    )

    assert [scene.kind for scene in timeline.scenes].count("word") == 4
    assert [scene.kind for scene in timeline.scenes].count("review") == 2
    assert sum(path == normal[0] for _, path in timeline.audio_events) == 3
    assert sum(path == slow[0] for _, path in timeline.audio_events) == 1
    assert sum(path == examples[0] for _, path in timeline.audio_events) == 1
    assert [scene.phase for scene in timeline.scenes if scene.kind == "voice"] == [
        "listen",
        "slow",
        "repeat",
        "listen",
        "slow",
        "repeat",
    ]
    assert timeline.duration_ms > 0


def test_subtitles_keep_supplied_content_and_dynamic_score(tmp_path: Path) -> None:
    project = MinigameProject(topic="Food", vocabulary=[_item(index) for index in range(1, 6)])
    timeline = MinigameTimeline(
        scenes=[
            MinigameScene("hook", 0, 1_000),
            MinigameScene("review", 1_000, 2_000, 0),
            MinigameScene("score", 2_000, 3_000),
        ],
        audio_events=[],
        duration_ms=3_000,
    )

    output = MinigameSubtitleBuilder().build(project, timeline, tmp_path / "game.ass", "9:16")
    text = output.read_text(encoding="utf-8-sig")

    assert "PlayResX: 1080" in text
    assert "CAN YOU GET\\N5 OUT OF 5?" in text
    assert "This is example 1." in text
    assert "0–2   KEEP PRACTICING" in text
    assert "5/5   PERFECT SCORE" in text
    assert "Segoe UI Semibold" in text
    assert "🌱" not in text


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
def test_minigame_speech_normalization_trims_provider_silence(tmp_path: Path) -> None:
    runner = FFmpegRunner()
    source = tmp_path / "speech-with-silence.wav"
    runner.run(
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono:d=0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.25:sample_rate=48000",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono:d=0.6",
            "-filter_complex",
            "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]",
            "-map",
            "[a]",
            "-c:a",
            "pcm_s16le",
            str(source),
        ]
    )

    output = MinigameAudioService(runner).normalize(source, tmp_path / "trimmed.wav")

    assert 250 <= output.duration_ms <= 500


def test_minigame_page_has_real_editor_and_dynamic_rows(qtbot) -> None:
    page = MinigamePage()
    qtbot.addWidget(page)

    assert page.table.rowCount() == 5
    assert page.aspect.count() == 3
    assert page.voice_config_button.text() == "Cấu hình Voice…"
    assert "Google Cloud TTS" in page.voice_summary.text()
    page.word_count.setValue(7)
    assert page.table.rowCount() == 7


def test_google_voice_family_follows_official_voice_tiers() -> None:
    assert google_voice_family("en-US-Chirp3-HD-Achernar") == "Chirp 3 HD"
    assert google_voice_family("en-US-Studio-O") == "Studio"
    assert google_voice_family("en-US-Neural2-F") == "Neural2"
    assert google_voice_family("en-US-Wavenet-A") == "WaveNet"
    assert google_voice_family("en-US-Standard-B") == "Standard"
    assert google_voice_family("Achernar") == "Chirp 3 HD"


def test_voice_dialog_filters_catalog_and_returns_metadata(qtbot) -> None:
    dialog = MinigameVoiceDialog(
        provider="google",
        service_account_path="services-account.json",
        selected_voice_id="en-US-Neural2-F",
        selected_language="en-US",
        volume=90,
        slow_rate=0.7,
        auto_load=False,
    )
    qtbot.addWidget(dialog)
    voices = [
        CatalogVoice(
            "google",
            "en-US-Neural2-F",
            "en-US-Neural2-F",
            ("af-ZA", "en-US"),
            "FEMALE",
            "Neural2",
            ("Neural2", "en"),
            24000,
        ),
        CatalogVoice(
            "google",
            "vi-VN-Standard-A",
            "vi-VN-Standard-A",
            ("vi-VN",),
            "FEMALE",
            "Standard",
            ("Standard", "vi"),
            24000,
        ),
    ]
    dialog._loaded((object(), voices))

    assert dialog.list.count() == 1
    config = dialog.configuration()
    assert config["voice"] == "en-US-Neural2-F"
    assert config["voice_language"] == "en-US"
    assert config["voice_family"] == "Neural2"
    dialog.language.setCurrentIndex(0)
    assert dialog.list.count() == 2


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
@pytest.mark.parametrize(
    ("aspect", "expected_size"), [("16:9", (1920, 1080)), ("9:16", (1080, 1920))]
)
def test_minigame_renderer_real_ffmpeg_output(
    tmp_path: Path, aspect: str, expected_size: tuple[int, int]
) -> None:
    runner = FFmpegRunner()
    background_path = tmp_path / "background.mp4"
    audio_path = tmp_path / "timeline.wav"
    runner.run(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=30:duration=0.8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(background_path),
        ]
    )
    runner.run(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.8:sample_rate=48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
    )
    background = runner.probe.video_artifact(background_path)
    audio = runner.probe.audio_artifact(audio_path)
    project = MinigameProject(topic="Test", vocabulary=[_item()])
    timeline = MinigameTimeline(
        [MinigameScene("hook", 0, audio.duration_ms)], [], audio.duration_ms
    )
    subtitles = MinigameSubtitleBuilder().build(project, timeline, tmp_path / "game.ass", aspect)
    progress: list[float] = []

    output = MinigameVideoRenderer(runner).render(
        background,
        audio,
        subtitles,
        tmp_path / f"result-{aspect.replace(':', 'x')}.mp4",
        aspect,
        [0.5, 0.5],
        progress=lambda event: progress.append(event.fraction),
    )

    info = runner.probe.inspect(output.path)
    assert (output.width, output.height) == expected_size
    assert {stream["codec_type"] for stream in info["streams"]} >= {"video", "audio"}
    assert progress[-1] == 1.0
