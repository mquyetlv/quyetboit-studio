from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialogButtonBox, QLabel

from quyetboit_studio.core.minigame import (
    MinigameAudioEvent,
    MinigameContent,
    MinigameProject,
    MinigameScene,
    MinigameTimeline,
    MinigameTimelineBuilder,
    MinigameTimingProfile,
    VocabularyItem,
    VocabularyLevel,
    answer_placeholder,
    format_score_range,
    score_ranges,
)
from quyetboit_studio.core.models import CancellationToken, PipelineError
from quyetboit_studio.minigame import MinigamePage
from quyetboit_studio.minigame_voice import MinigameVoiceDialog
from quyetboit_studio.pipeline.minigame import MinigamePipeline
from quyetboit_studio.services.media import FFmpegRunner
from quyetboit_studio.services.media.minigame import (
    MinigameAudioCueManifest,
    MinigameAudioService,
    MinigameSubtitleBuilder,
    MinigameVideoRenderer,
    MinigameVisualScene,
)
from quyetboit_studio.services.media.minigame_frame import MinigameVisualFrameService
from quyetboit_studio.services.media.minigame_mask import MinigameAlphaMaskService
from quyetboit_studio.services.media.minigame_text import MinigameTextLayerService
from quyetboit_studio.services.media.minigame_theme import MinigameThemeService
from quyetboit_studio.services.media.minigame_video_theme import MinigameVideoThemeService
from quyetboit_studio.services.minigame_tts import (
    CatalogVoice,
    GoogleVoiceCatalogService,
    google_voice_family,
)


def _item(index: int = 1) -> VocabularyItem:
    level = tuple(VocabularyLevel)[(index - 1) % 3].value
    return VocabularyItem(
        f"nghĩa {index}",
        f"word{index}",
        f"/wɜːd {index}/",
        f"This is example {index}.",
        level=level,
    )


# Fixed A/B content spans compact labels, long vocabulary and a multi-clause
# bilingual example; no provider call is needed to exercise timing/layout.
VNEXT_AB_FIXTURES = (
    VocabularyItem("mèo", "cat", "/kæt/", "The cat sleeps.", "Noun", "Con mèo ngủ.", "Dễ"),
    VocabularyItem(
        "trách nhiệm", "responsibility", "/rɪˌspɒn.səˈbɪl.ə.ti/",
        "Responsibility helps us make careful choices every day.", "Noun",
        "Trách nhiệm giúp chúng ta đưa ra lựa chọn cẩn thận mỗi ngày.", "Trung bình",
    ),
    VocabularyItem(
        "kiên trì", "perseverance", "/ˌpɜː.səˈvɪə.rəns/",
        "With perseverance, she continued practicing even when the assignment became difficult.",
        "Noun", "Nhờ sự kiên trì, cô ấy vẫn tiếp tục luyện tập dù bài tập trở nên khó khăn.", "Khó",
    ),
)


def test_vnext_ab_fixtures_cover_short_long_and_bilingual_example() -> None:
    assert len(VNEXT_AB_FIXTURES) == 3
    assert len(VNEXT_AB_FIXTURES[0].english) < len(VNEXT_AB_FIXTURES[1].english)
    assert len(VNEXT_AB_FIXTURES[2].example) > 70
    assert all(item.example_vietnamese for item in VNEXT_AB_FIXTURES)
    for index, item in enumerate(VNEXT_AB_FIXTURES, 1):
        item.validate(index)


def _content_json(count: int = 1, topic: str = "Hospital") -> str:
    return MinigameContent(
        topic,
        [
            VocabularyItem(
                f"nghĩa {index}",
                f"word{index}",
                f"/wɜːd {index}/",
                f"This is example {index}.",
                "Noun",
                f"Đây là ví dụ {index}.",
                tuple(VocabularyLevel)[(index - 1) % 3].value,
            )
            for index in range(1, count + 1)
        ],
    ).to_json()


def test_vnext_timing_profile_builds_semantic_boundaries_and_full_reveal_gap(
    tmp_path: Path,
) -> None:
    normal = tmp_path / "normal.wav"
    slow = tmp_path / "slow.wav"
    example = tmp_path / "example.wav"
    profile = MinigameTimingProfile()
    timeline = MinigameTimelineBuilder(profile).build(
        [_item()],
        [normal],
        [slow],
        [example],
        {normal: 800, slow: 1_100, example: 900},
        answer_reveal_duration_ms=1_858,
    )

    boundaries = {
        scene.phase: scene.start_ms for scene in timeline.scenes if scene.kind == "boundary"
    }
    assert boundaries["countdown_bed"] - boundaries["question_start"] == profile.question_intro_ms
    assert boundaries["answer_reveal"] - boundaries["countdown_bed"] == profile.countdown_ms
    listen = next(event for event in timeline.audio_events if event.semantic == "voice_listen")
    assert listen.start_ms - boundaries["answer_reveal"] == 1_858 + profile.post_reveal_sfx_gap_ms
    voice_scenes = [scene for scene in timeline.scenes if scene.kind == "voice"]
    assert [(scene.phase, scene.start_ms, scene.end_ms) for scene in voice_scenes] == [
        ("listen", listen.start_ms, next(scene.start_ms for scene in voice_scenes if scene.phase == "slow")),
        (
            "slow",
            next(scene.start_ms for scene in voice_scenes if scene.phase == "slow"),
            next(scene.start_ms for scene in voice_scenes if scene.phase == "repeat"),
        ),
        ("repeat", next(scene.start_ms for scene in voice_scenes if scene.phase == "repeat"), timeline.scenes[5].end_ms),
    ]
    assert (
        profile.review_after_example_min_ms
        <= profile.review_hold_ms(_item(), 900)
        <= profile.review_after_example_max_ms
    )


def test_audio_cue_manifest_resolves_bundled_assets_and_reports_missing(tmp_path: Path) -> None:
    manifest = MinigameAudioCueManifest()
    assert all(manifest.source(name).is_file() for name in manifest.CUES)
    with pytest.raises(PipelineError, match="cài lại"):
        MinigameAudioCueManifest(tmp_path).source("score_win")


def test_transition_music_is_restricted_to_short_faded_beds() -> None:
    manifest = MinigameAudioCueManifest()
    expected_fades = {"hook_bed": 450, "review_start_bed": 450, "cta_bed": 180}
    for semantic, fade_ms in expected_fades.items():
        cue = manifest.CUES[semantic]
        assert cue.is_background_bed
        assert cue.fade_ms == fade_ms
        assert manifest.source(semantic).is_file()


def test_hook_and_review_start_have_a_silent_hold_after_music_fades() -> None:
    timing = MinigameTimingProfile()

    assert timing.hook_ms - timing.hook_bed_ms == timing.hook_tail_hold_ms == 650
    assert (
        timing.review_start_ms - timing.review_start_bed_ms
        == timing.review_start_tail_hold_ms
        == 650
    )
    assert timing.transition_bed_duration_ms("hook", timing.hook_ms) == timing.hook_bed_ms
    assert (
        timing.transition_bed_duration_ms("review_start", timing.review_start_ms)
        == timing.review_start_bed_ms
    )


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
def test_transition_music_is_trimmed_and_faded_to_its_scene(tmp_path: Path) -> None:
    service = MinigameAudioService(FFmpegRunner())
    artifact = service.normalize_cue(MinigameAudioCueManifest(), "hook_bed", tmp_path, duration_ms=3_200)

    assert abs(artifact.duration_ms - 3_200) <= 40
    with wave.open(str(artifact.path), "rb") as source:
        source.setpos(max(0, source.getnframes() - 480))
        tail = np.frombuffer(source.readframes(480), dtype="<i2")
    assert np.abs(tail).max() < 80


def test_timing_changes_invalidate_timeline_cache_but_not_tts_cache(tmp_path: Path) -> None:
    project = MinigameProject(topic="Cache", vocabulary=[_item()])
    pipeline = MinigamePipeline(project, workspace=tmp_path)
    timeline = MinigameTimeline([], [], 1_000)
    original_timeline_key = pipeline._timeline_cache_key(timeline)
    original_tts_key = pipeline._speech_cache_key("word")
    pipeline.timeline_builder = MinigameTimelineBuilder(
        replace(MinigameTimingProfile(), countdown_ms=4_000)
    )

    assert pipeline._timeline_cache_key(timeline) != original_timeline_key
    assert pipeline._speech_cache_key("word") == original_tts_key


def test_review_cards_keep_the_background_stable_between_items(tmp_path: Path) -> None:
    timeline = MinigameTimeline(
        [
            MinigameScene("hook", 0, 100),
            MinigameScene("score", 100, 200),
            MinigameScene("review_start", 200, 300),
            MinigameScene("review", 300, 600, 0),
            MinigameScene("review", 600, 900, 1),
        ],
        [],
        900,
    )
    frames = [
        tmp_path / name for name in ("hook.png", "body.png", "score.png", "start.png", "review.png")
    ]
    scenes = MinigamePipeline._visual_scenes(timeline, *frames)

    assert [scene.fade_in_ms for scene in scenes[-2:]] == [0, 0]
    assert sum(scene.duration_ms for scene in scenes) == timeline.duration_ms


def test_custom_background_video_replaces_only_the_theme_body_background(tmp_path: Path) -> None:
    timeline = MinigameTimeline(
        [
            MinigameScene("hook", 0, 100),
            MinigameScene("score", 500, 600),
            MinigameScene("review_start", 600, 700),
            MinigameScene("review", 700, 800, 0),
        ],
        [],
        800,
    )
    hook, body, score, review_start, review, custom_video, theme_text = (
        tmp_path / name
        for name in (
            "hook.png", "body.png", "score.png", "review-start.png", "review.png",
            "custom.mp4", "theme-text.png",
        )
    )

    scenes = MinigamePipeline._visual_scenes(
        timeline,
        hook,
        body,
        score,
        review_start,
        review,
        custom_background=custom_video,
        theme_text_overlay=theme_text,
        crop_focus=[0.2, 0.8],
    )

    body_scene = scenes[1]
    assert body_scene.background == custom_video
    assert body_scene.overlays == (theme_text,)
    assert body_scene.background_is_video
    assert body_scene.crop_focus == (0.2, 0.8)
    assert scenes[0].background == hook
    assert scenes[2].background == score


def test_video_background_flow_covers_every_scene_with_continuous_offsets(tmp_path: Path) -> None:
    timeline = MinigameTimeline(
        [
            MinigameScene("hook", 0, 100),
            MinigameScene("word", 100, 300, 0, "question"),
            MinigameScene("score", 300, 400),
            MinigameScene("review_start", 400, 500),
            MinigameScene("review", 500, 700, 0),
            MinigameScene("cta", 700, 800),
        ],
        [],
        800,
    )
    background = tmp_path / "background.mp4"
    surfaces = {kind: tmp_path / f"{kind}.png" for kind in (
        "hook", "body", "score", "review_start", "review", "cta"
    )}
    hook_text = tmp_path / "hook-text.png"
    review_start_text = tmp_path / "review-start-text.png"
    cta_text = tmp_path / "cta-text.png"

    scenes = MinigamePipeline._video_visual_scenes(
        timeline,
        background,
        surfaces,
        hook_text,
        review_start_text,
        cta_text,
        [0.25, 0.75],
    )

    assert [scene.background for scene in scenes] == [background] * 6
    assert [scene.background_start_ms for scene in scenes] == [0, 100, 300, 400, 500, 700]
    assert all(scene.background_is_video for scene in scenes)
    assert all(scene.crop_focus == (0.25, 0.75) for scene in scenes)
    assert scenes[0].overlays == (surfaces["hook"], hook_text)
    assert scenes[-1].overlays == (surfaces["cta"], cta_text)
    assert sum(scene.duration_ms for scene in scenes) == timeline.duration_ms


def test_answer_word_reveal_and_review_transition_use_motion(tmp_path: Path) -> None:
    manifest = MinigameThemeService().get("theme-1")
    project = MinigameProject(topic="Hospital", vocabulary=[_item()])
    timeline = MinigameTimeline(
        [
            MinigameScene("word", 0, 1_000, 0, "answer"),
            MinigameScene("review", 1_000, 2_000, 0),
        ],
        [],
        2_000,
    )
    content = MinigameSubtitleBuilder().build(
        project, timeline, tmp_path / "motion.ass", "9:16", manifest
    ).read_text(encoding="utf-8-sig")

    assert r"\fscx0\fscy0\t(0,240,\alpha&H00&\fscx100\fscy100)" in content
    assert r"\move(540,1175,540,1147,0,240)" in content
    assert r"\move(540,383,540,363,0,200)" in content


def test_timeline_timestamps_cover_multiple_words_and_clamp_long_review_hold(
    tmp_path: Path,
) -> None:
    clips = [tmp_path / f"clip-{index}.wav" for index in range(6)]
    normal, slow, example = clips[:2], clips[2:4], clips[4:]
    items = [_item(1), VocabularyItem("dài", "extraordinary", "/ɪkˈstrɔː.dɪ.nər.i/", "x" * 400)]
    durations = {
        normal[0]: 250,
        normal[1]: 1_500,
        slow[0]: 500,
        slow[1]: 2_000,
        example[0]: 300,
        example[1]: 800,
    }
    timing = MinigameTimingProfile()
    timeline = MinigameTimelineBuilder(timing).build(
        items, normal, slow, example, durations, answer_reveal_duration_ms=1_858
    )

    reviews = [scene for scene in timeline.scenes if scene.kind == "review"]
    voices = [event for event in timeline.audio_events if event.semantic.startswith("voice_")]
    assert len(voices) == 6
    assert all(0 <= event.start_ms < timeline.duration_ms for event in timeline.audio_events)
    assert reviews[1].end_ms - reviews[1].start_ms >= (
        timing.review_lead_ms
        + durations[normal[1]]
        + timing.review_word_to_example_ms
        + durations[example[1]]
        + timing.review_after_example_max_ms
    )


def test_audio_bus_limiter_preserves_valid_pcm_peak(tmp_path: Path) -> None:
    source = tmp_path / "cue.wav"
    samples = (np.ones(4_800, dtype="<i2") * 30_000).tobytes()
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(samples)
    mixed = MinigameAudioService.assemble(
        [MinigameAudioEvent(0, source), MinigameAudioEvent(0, source, "effect")],
        tmp_path / "mixed.wav",
        100,
    )
    with wave.open(str(mixed.path), "rb") as output:
        peak = (
            np.abs(np.frombuffer(output.readframes(output.getnframes()), dtype="<i2")).max() / 32768
        )
    assert mixed.sample_rate == 48_000
    assert peak <= 0.961


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
def test_corrupt_normalized_cue_cache_is_rebuilt(tmp_path: Path) -> None:
    service = MinigameAudioService(FFmpegRunner())
    manifest = MinigameAudioCueManifest()
    first = service.normalize_cue(manifest, "question_start", tmp_path)
    first.path.write_bytes(b"corrupt-cache")

    rebuilt = service.normalize_cue(manifest, "question_start", tmp_path)

    assert rebuilt.path == first.path
    assert rebuilt.duration_ms > 1_000
    assert rebuilt.path.read_bytes() != b"corrupt-cache"


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
def test_countdown_progress_bar_frames_shrink_monotonically(tmp_path: Path) -> None:
    runner = FFmpegRunner()
    audio_path = tmp_path / "countdown.wav"
    runner.run(
        [
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1",
            "-c:a", "pcm_s16le", str(audio_path),
        ]
    )
    audio = runner.probe.audio_artifact(audio_path)
    manifest = MinigameThemeService().get("theme-1")
    mask = MinigameAlphaMaskService().build(manifest, tmp_path / "mask")
    body = MinigameVisualFrameService().build(
        manifest.background_video, (mask,), tmp_path / "frames", layout_key=manifest.layout_version
    )
    timeline = MinigameTimeline(
        [
            MinigameScene("word", 0, 1_000, 0, "question"),
            MinigameScene("boundary", 0, 0, 0, "countdown_bed"),
        ],
        [],
        1_000,
    )
    subtitles = MinigameSubtitleBuilder().build(
        MinigameProject(topic="Test", vocabulary=[_item()]), timeline, tmp_path / "countdown.ass",
        "9:16", manifest,
    )
    video = MinigameVideoRenderer(runner).render(
        None, audio, subtitles, tmp_path / "countdown.mp4", "9:16", [0.5, 0.5],
        theme_background=manifest.background_video, theme_overlay=mask,
        visual_scenes=[MinigameVisualScene(body, 1_000)],
    )
    widths: list[int] = []
    for index, timestamp in enumerate((0.05, 0.25, 0.50, 0.75, 0.95)):
        frame_path = tmp_path / f"countdown-{index}.png"
        runner.run(["-ss", str(timestamp), "-i", str(video.path), "-frames:v", "1", str(frame_path)])
        with Image.open(frame_path) as frame:
            x1, y1, x2, y2 = manifest.slots["countdown_bar"].bounds
            pixels = np.asarray(frame.convert("RGB"))[y1:y2, x1:x2]
        cyan = (pixels[:, :, 0] < 120) & (pixels[:, :, 1] > 170) & (pixels[:, :, 2] > 170)
        widths.append(int(np.count_nonzero(np.any(cyan, axis=0))))

    assert widths == sorted(widths, reverse=True)
    assert widths[0] > widths[-1] + 400


def test_countdown_bar_uses_each_theme_measured_divider_slot(tmp_path: Path) -> None:
    project = MinigameProject(topic="Test", vocabulary=[_item()])
    timeline = MinigameTimeline(
        [
            MinigameScene("word", 0, 1_000, 0, "question"),
            MinigameScene("boundary", 0, 0, 0, "countdown_bed"),
        ],
        [],
        1_000,
    )
    positions = set()
    for theme_id, manifest in MinigameThemeService().discover().items():
        content = MinigameSubtitleBuilder().build(
            project, timeline, tmp_path / f"{theme_id}.ass", "9:16", manifest
        ).read_text(encoding="utf-8-sig")
        x1, y1, _x2, _y2 = manifest.slots["countdown_bar"].bounds
        positions.add((x1, y1))
        assert rf"\pos({x1},{y1})" in content
        assert "REVEALING ANSWER IN" not in content
    assert len(positions) == 5


def test_minigame_content_json_round_trips_every_field_and_normalizes_alias() -> None:
    value = _content_json()
    content = MinigameContent.from_json(value)

    assert content.topic == "Hospital"
    assert content.words[0].word_type == "Noun"
    assert content.words[0].example_vietnamese == "Đây là ví dụ 1."
    assert content.words[0].level == "Dễ"
    assert (
        MinigameContent.from_json(value.replace('"wordType"', '"workType"')).words[0].word_type
        == "Noun"
    )
    assert '"wordType": "Noun"' in content.to_json()
    assert '"level": "Dễ"' in content.to_json()
    assert '"workType"' not in content.to_json()


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("{", "dòng 1, cột 2"),
        ('{"schemaVersion": 1, "topic": "x", "words": []}', "từ 1 đến 50"),
        (
            '{"schemaVersion": 1, "topic": "x", "words": [{}]}',
            "thiếu trường",
        ),
        (
            '{"schemaVersion": 1, "topic": "x", "words": [], "token": "secret"}',
            "trường gốc không hỗ trợ: token",
        ),
    ],
)
def test_minigame_content_json_reports_actionable_paths(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        MinigameContent.from_json(value)


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


def test_video_theme_selection_round_trips_in_project_v2() -> None:
    project = MinigameProject.from_dict(
        MinigameProject(video_theme_id="video-violet").to_safe_dict()
    )

    assert project.video_theme_id == "video-violet"


@pytest.mark.parametrize("video_theme_id", ["video-ocean", "video-violet", "video-emerald"])
def test_video_theme_profiles_build_all_rgba_surfaces(
    tmp_path: Path, video_theme_id: str
) -> None:
    service = MinigameVideoThemeService()
    manifest = service.manifest(video_theme_id)

    surfaces = service.build_surfaces(video_theme_id, manifest, tmp_path / video_theme_id)

    assert set(surfaces) == {"hook", "body", "score", "review_start", "review", "cta"}
    for surface in surfaces.values():
        with Image.open(surface) as image:
            assert image.mode == "RGBA"
            assert image.size == (1080, 1920)
            assert image.getchannel("A").getextrema()[0] < 255


@pytest.mark.parametrize(
    ("video_theme_id", "base_theme_id", "art_direction", "display_name"),
    [
        ("video-ocean", "theme-2", "medical", "Theme 2 · Medical Blue"),
        ("video-violet", "theme-3", "scrapbook", "Theme 3 · Scrapbook"),
        ("video-emerald", "theme-5", "garden", "Theme 5 · Garden"),
    ],
)
def test_video_theme_profiles_inherit_the_intended_static_art_direction(
    video_theme_id: str,
    base_theme_id: str,
    art_direction: str,
    display_name: str,
) -> None:
    service = MinigameVideoThemeService()
    profile = service.get(video_theme_id)
    manifest = service.manifest(video_theme_id)

    assert profile.base_theme_id == base_theme_id
    assert profile.art_direction == art_direction
    assert manifest.display_name == display_name
    assert manifest.start_screen == service.themes.get(base_theme_id).start_screen


def test_legacy_minigame_project_migrates_to_default_theme() -> None:
    project = MinigameProject.from_dict(
        {
            "schema_version": 1,
            "topic": "Food",
            "background_video": "legacy.mp4",
            "vocabulary": [
                {"vietnamese": "một", "english": "one", "ipa": "/wʌn/", "example": "One."},
                {"vietnamese": "hai", "english": "two", "ipa": "/tuː/", "example": "Two."},
                {"vietnamese": "ba", "english": "three", "ipa": "/θriː/", "example": "Three."},
            ],
        }
    )

    assert project.schema_version == 2
    assert project.theme_id == "theme-1"
    assert project.background_video == "legacy.mp4"
    assert [item.level for item in project.vocabulary] == ["Dễ", "Trung bình", "Khó"]


def test_content_json_rejects_unknown_level_with_word_path() -> None:
    value = _content_json().replace('"level": "Dễ"', '"level": "Siêu khó"')

    with pytest.raises(ValueError, match=r"words\[0\]\.level phải là một trong"):
        MinigameContent.from_json(value)


def test_image_theme_does_not_require_legacy_background_video() -> None:
    project = MinigameProject(
        topic="Food",
        tts_provider="capcut",
        voice="voice",
        vocabulary=[_item(1), _item(2), _item(3)],
    )

    project.validate()


def test_theme_discovery_and_mask_are_complete_for_all_five_themes(tmp_path: Path) -> None:
    manifests = MinigameThemeService().discover()

    assert set(manifests) == {f"theme-{index}" for index in range(1, 6)}
    for manifest in manifests.values():
        assert manifest.start_screen.is_file()
        assert manifest.start_base.is_file()
        assert manifest.background_video.is_file()
        assert manifest.score_screen.is_file()
        assert manifest.review_start_screen.is_file()
        assert manifest.review_start_base.is_file()
        assert manifest.review_screen.is_file()
        assert manifest.cta_screen.name == "cta-new.png"
        assert manifest.cta_screen.is_file()
        with Image.open(manifest.cta_screen) as cta:
            assert cta.size == (941, 1672)
        assert manifest.digit_atlas.is_file()
        assert manifest.digit_atlas_spec.glyph_count == 10
        result = MinigameAlphaMaskService().build(manifest, tmp_path)

        with Image.open(result) as image:
            assert image.mode == "RGBA"
            assert image.size == (1080, 1920)
            assert image.getextrema()[3][0] == 0
            assert image.getextrema()[3][1] == 255


def test_review_asset_aliases_resolve_for_all_five_themes() -> None:
    manifests = MinigameThemeService().discover()

    assert manifests["theme-2"].review_start_screen.name == "start-review-screen.png"
    assert all(manifest.review_screen.name == "rewview.png" for manifest in manifests.values())
    for manifest in manifests.values():
        with Image.open(manifest.review_start_screen) as review_start:
            assert review_start.size == (941, 1672)
        with Image.open(manifest.review_screen) as review:
            assert review.size == (941, 1672)


def _relative_luminance(rgb: np.ndarray) -> float:
    values = rgb.astype(np.float64) / 255.0
    linear = np.where(
        values <= 0.04045,
        values / 12.92,
        ((values + 0.055) / 1.055) ** 2.4,
    )
    return float(0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2])


def _contrast_ratio(color: str, background: np.ndarray) -> float:
    foreground = np.array(
        [int(color[index : index + 2], 16) for index in (1, 3, 5)], dtype=np.uint8
    )
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_every_theme_palette_is_readable_on_its_actual_surfaces(tmp_path: Path) -> None:
    for theme_id, manifest in MinigameThemeService().discover().items():
        assert manifest.palette == (
            manifest.colors.primary,
            manifest.colors.accent,
            manifest.colors.secondary,
        )
        mask = MinigameAlphaMaskService().build(manifest, tmp_path / theme_id / "mask")
        body = MinigameVisualFrameService().build(
            manifest.background_video,
            (mask,),
            tmp_path / theme_id / "frame",
            layout_key={"contrast": theme_id},
        )
        surfaces = (
            (
                body,
                ("meaning", "word", "ipa", "phase"),
                (manifest.colors.primary, manifest.colors.secondary, manifest.colors.accent),
                manifest.colors.outline,
            ),
            (
                manifest.review_screen,
                ("review_meaning", "review_word", "review_ipa", "review_example"),
                (
                    manifest.colors.review_primary,
                    manifest.colors.review_secondary,
                    manifest.colors.review_accent,
                ),
                manifest.colors.review_outline,
            ),
            (
                manifest.start_screen,
                ("start_topic",),
                (manifest.styles["topic"].color,),
                manifest.styles["topic"].outline_color,
            ),
        )
        for source, slots, colors, outline in surfaces:
            with Image.open(source) as opened:
                image = np.asarray(
                    opened.convert("RGB").resize((1080, 1920), Image.Resampling.LANCZOS)
                )
            for slot_name in slots:
                x1, y1, x2, y2 = manifest.slots[slot_name].bounds
                pixels = image[y1 : y2 + 1, x1 : x2 + 1].reshape(-1, 3)
                background = np.median(pixels, axis=0).astype(np.uint8)
                assert all(
                    max(_contrast_ratio(color, background), _contrast_ratio(outline, background))
                    >= 4.5
                    for color in colors
                ), (theme_id, slot_name)
        assert manifest.styles["topic"].outline_width >= 1
        assert all(
            style.outline_width >= 2 for name, style in manifest.styles.items() if name != "topic"
        )


def test_theme_two_ass_uses_dark_palette_and_visible_outline(tmp_path: Path) -> None:
    manifest = MinigameThemeService().get("theme-2")
    project = MinigameProject(topic="Hospital", vocabulary=[_item()])
    timeline = MinigameTimeline([MinigameScene("word", 0, 1_000, 0, "answer")], [], 1_000)

    content = (
        MinigameSubtitleBuilder()
        .build(project, timeline, tmp_path / "theme-2.ass", "9:16", manifest)
        .read_text(encoding="utf-8-sig")
    )

    word_style = next(line for line in content.splitlines() if line.startswith("Style: Word,"))
    assert "&H005A3A12" in word_style  # #123A5A converted to ASS BGR.
    assert "&H00FFFFFF" in word_style
    assert ",1,2,1,5," in word_style


def test_theme_preflight_reports_missing_bundled_font_with_action(tmp_path: Path) -> None:
    with pytest.raises(PipelineError, match="cài đặt lại ứng dụng") as caught:
        MinigameThemeService(fonts_root=tmp_path).get("theme-1")

    assert caught.value.error_code == "theme_font_missing"


def test_border_flood_fill_keeps_disconnected_dark_panel_pixels() -> None:
    rgb = np.full((7, 7, 3), 180, dtype=np.uint8)
    rgb[0, :, :] = 0
    rgb[:, 0, :] = 0
    rgb[3, 3, :] = 0
    rgb[4, 4, :] = 0

    matte = MinigameAlphaMaskService._border_matte(rgb, threshold=32)

    assert matte[0, 3]
    assert matte[3, 0]
    assert not matte[3, 3]
    assert not matte[4, 4]


def test_alpha_masks_have_expected_coverage_feather_and_detail_for_all_themes(
    tmp_path: Path,
) -> None:
    masks = MinigameAlphaMaskService()
    for theme_id, manifest in MinigameThemeService().discover().items():
        result = masks.build(manifest, tmp_path)
        with Image.open(result) as image:
            alpha = np.asarray(image.getchannel("A"))
        coverage = float(np.count_nonzero(alpha) / alpha.size)
        feather = float(np.count_nonzero((alpha > 0) & (alpha < 255)) / alpha.size)

        assert alpha.min() == 0
        assert alpha.max() == 255
        assert 0.90 < coverage < 0.98, theme_id
        assert feather > 0.02, theme_id
        assert np.all(alpha[0, :] == 0), theme_id
        assert np.all(alpha[-1, :] == 0), theme_id
        assert np.all(alpha[:, 0] == 0), theme_id
        assert np.all(alpha[:, -1] == 0), theme_id


def test_alpha_mask_cache_key_changes_with_profile_or_asset_checksum(tmp_path: Path) -> None:
    service = MinigameAlphaMaskService()
    manifest = MinigameThemeService().get("theme-1")
    original = service.build(manifest, tmp_path)
    algorithm_changed = replace(
        manifest,
        mask_profile=replace(
            manifest.mask_profile, algorithm_version=manifest.mask_profile.algorithm_version + 1
        ),
    )
    asset_changed = replace(manifest, checksum=f"changed-{manifest.checksum}")

    assert service.build(manifest, tmp_path) == original
    assert service.build(algorithm_changed, tmp_path) != original
    assert service.build(asset_changed, tmp_path) != original


def test_visual_frame_cache_invalidates_for_layout_without_changing_tts_key(tmp_path: Path) -> None:
    manifest = MinigameThemeService().get("theme-1")
    mask = MinigameAlphaMaskService().build(manifest, tmp_path / "mask")
    frames = MinigameVisualFrameService()
    initial = frames.build(
        manifest.background_video, (mask,), tmp_path / "frames", layout_key={"version": 1}
    )
    cached = frames.build(
        manifest.background_video, (mask,), tmp_path / "frames", layout_key={"version": 1}
    )
    changed = frames.build(
        manifest.background_video, (mask,), tmp_path / "frames", layout_key={"version": 2}
    )
    first_project = MinigameProject(theme_id="theme-1", tts_provider="capcut", voice="voice")
    second_project = replace(first_project, theme_id="theme-5")

    first_tts_key = MinigamePipeline(
        first_project, workspace=tmp_path / "pipeline-1"
    )._speech_cache_key("healthcare")
    second_tts_key = MinigamePipeline(
        second_project, workspace=tmp_path / "pipeline-2"
    )._speech_cache_key("healthcare")

    assert cached == initial
    assert changed != initial
    assert first_tts_key == second_tts_key


@pytest.mark.parametrize("theme_id", [f"theme-{index}" for index in range(1, 6)])
def test_real_composite_keeps_background_visible_without_black_matte(
    tmp_path: Path, theme_id: str
) -> None:
    manifest = MinigameThemeService().get(theme_id)
    mask_path = MinigameAlphaMaskService().build(manifest, tmp_path / "masks")
    frame_path = MinigameVisualFrameService().build(
        manifest.background_video,
        (mask_path,),
        tmp_path / "frames",
        layout_key={"theme": theme_id},
    )
    with Image.open(manifest.background_video) as image:
        background = np.asarray(
            image.convert("RGBA").resize((1080, 1920), Image.Resampling.LANCZOS)
        )
    with Image.open(mask_path) as image:
        alpha = np.asarray(image.getchannel("A"))
    with Image.open(frame_path) as image:
        composite = np.asarray(image.convert("RGBA"))

    transparent = alpha == 0
    translucent = (alpha > 0) & (alpha < 255)
    assert np.array_equal(composite[transparent], background[transparent]), theme_id
    assert np.count_nonzero(
        np.any(composite[translucent, :3] != background[translucent, :3], axis=1)
    )
    assert np.array_equal(composite[0, 0], background[0, 0]), theme_id


def test_native_to_canvas_conversion_is_within_one_pixel_for_every_slot() -> None:
    manifest = MinigameThemeService().get("theme-1")
    native_size = (971, 1619)
    for slot in manifest.slots.values():
        native = (
            round(slot.bounds[0] * native_size[0] / manifest.canvas.width),
            round(slot.bounds[1] * native_size[1] / manifest.canvas.height),
            round(slot.bounds[2] * native_size[0] / manifest.canvas.width),
            round(slot.bounds[3] * native_size[1] / manifest.canvas.height),
        )
        restored = manifest.canvas.scale_bounds(native, native_size)

        assert max(abs(actual - expected) for actual, expected in zip(restored, slot.bounds)) <= 1


def test_theme_subtitles_follow_reference_scene_hierarchy(tmp_path: Path) -> None:
    project = MinigameProject(topic="Hospital", vocabulary=[_item()])
    timeline = MinigameTimeline(
        [
            MinigameScene("hook", 0, 1_000),
            MinigameScene("word", 1_000, 2_000, 0, "question"),
            MinigameScene("word", 2_000, 3_000, 0, "answer"),
            MinigameScene("voice", 2_100, 2_500, 0, "listen"),
        ],
        [],
        3_000,
    )

    output = MinigameSubtitleBuilder().build(
        project, timeline, tmp_path / "theme.ass", "9:16", MinigameThemeService().get("theme-1")
    )
    content = output.read_text(encoding="utf-8-sig")

    assert "CAN YOU GET" not in content
    assert "VIETNAMESE MEANING" in content
    assert "REVEALING ANSWER IN" not in content
    assert "ENGLISH WORD" in content
    assert "1 • LISTEN" in content
    assert "Listen first, then repeat aloud" in content
    assert "Style: Header,Montserrat" in content
    assert "Style: Start,Bebas Neue" in content
    assert "Arial" not in content


def test_theme_subtitles_take_style_and_coordinates_from_manifest(tmp_path: Path) -> None:
    manifest = MinigameThemeService().get("theme-1")
    placements = dict(manifest.placements)
    placements["meaning_label"] = replace(placements["meaning_label"], y=444)
    styles = dict(manifest.styles)
    styles["label"] = replace(styles["label"], min_size=16, max_size=19)
    manifest = replace(manifest, placements=placements, styles=styles)
    project = MinigameProject(topic="Hospital", vocabulary=[_item()])
    timeline = MinigameTimeline([MinigameScene("word", 0, 1_000, 0, "question")], [], 1_000)

    content = (
        MinigameSubtitleBuilder()
        .build(project, timeline, tmp_path / "manifest-layout.ass", "9:16", manifest)
        .read_text(encoding="utf-8-sig")
    )

    assert "Style: Label,Montserrat,19" in content
    assert r"\pos(540,444)\fs19" in content


def test_question_scene_never_contains_english_or_ipa_before_reveal(tmp_path: Path) -> None:
    project = MinigameProject(topic="Hospital", vocabulary=[_item()])
    timeline = MinigameTimeline([MinigameScene("word", 0, 3_000, 0, "question")], [], 3_000)

    content = (
        MinigameSubtitleBuilder()
        .build(
            project,
            timeline,
            tmp_path / "question-only.ass",
            "9:16",
            MinigameThemeService().get("theme-1"),
        )
        .read_text(encoding="utf-8-sig")
    )

    assert "nghĩa 1" in content
    assert "word1" not in content
    assert "/wɜːd 1/" not in content
    assert "ENGLISH WORD" in content
    assert "w____" in content
    assert "REVEALING ANSWER IN" not in content


def test_voice_phase_text_uses_exact_scene_timestamp(tmp_path: Path) -> None:
    project = MinigameProject(topic="Hospital", vocabulary=[_item()])
    timeline = MinigameTimeline([MinigameScene("voice", 2_100, 2_500, 0, "listen")], [], 2_500)

    content = (
        MinigameSubtitleBuilder()
        .build(
            project,
            timeline,
            tmp_path / "voice-time.ass",
            "9:16",
            MinigameThemeService().get("theme-1"),
        )
        .read_text(encoding="utf-8-sig")
    )

    assert "Dialogue: 2,0:00:02.10,0:00:02.50,Header" in content
    assert "1 • LISTEN" in content


def test_voice_phase_helper_uses_large_mobile_readable_style(tmp_path: Path) -> None:
    manifest = MinigameThemeService().get("theme-1")
    project = MinigameProject(topic="Hospital", vocabulary=[_item()])
    timeline = MinigameTimeline([MinigameScene("voice", 0, 1_000, 0, "repeat")], [], 1_000)
    content = MinigameSubtitleBuilder().build(
        project, timeline, tmp_path / "voice-helper.ass", "9:16", manifest
    ).read_text(encoding="utf-8-sig")

    style = manifest.styles["voice_helper"]
    assert (style.min_size, style.max_size, style.bold) == (26, 42, True)
    assert "Style: VoiceHelper,Montserrat,42" in content
    assert ",VoiceHelper," in content


def test_review_example_wraps_by_bundled_font_pixel_width(tmp_path: Path) -> None:
    item = VocabularyItem(
        "chăm sóc sức khỏe",
        "healthcare",
        "/ˈhelθ.keər/",
        "This deliberately long example sentence must wrap inside the review panel safely.",
    )
    project = MinigameProject(topic="Hospital", vocabulary=[item])
    timeline = MinigameTimeline([MinigameScene("review", 0, 2_000, 0)], [], 2_000)

    content = (
        MinigameSubtitleBuilder()
        .build(
            project,
            timeline,
            tmp_path / "review-wrap.ass",
            "9:16",
            MinigameThemeService().get("theme-1"),
        )
        .read_text(encoding="utf-8-sig")
    )

    example_line = next(line for line in content.splitlines() if "deliberately" in line)
    assert r"\N" in example_line


def test_review_content_uses_new_full_screen_design_slots(tmp_path: Path) -> None:
    manifest = MinigameThemeService().get("theme-1")
    item = VocabularyItem("bác sĩ", "Doctor", "/ˈdɒk.tər/", "The doctor checks the patient.")
    project = MinigameProject(topic="Hospital", vocabulary=[item])
    timeline = MinigameTimeline(
        [
            MinigameScene("review_start", 0, 1_000),
            MinigameScene("review", 1_000, 3_000, 0),
        ],
        [],
        3_000,
    )

    content = (
        MinigameSubtitleBuilder()
        .build(project, timeline, tmp_path / "new-review.ass", "9:16", manifest)
        .read_text(encoding="utf-8-sig")
    )

    assert "bác sĩ" in content
    assert "Doctor" in content
    assert "/ˈdɒk.tər/" in content
    assert "The doctor checks the patient." in content
    assert "WORD TYPE" not in content
    assert "Dialogue: 0,0:00:00.00,0:00:01.00" not in content
    for placement_name in ("review_meaning", "review_word", "review_ipa", "review_example"):
        placement = manifest.placements[placement_name]
        x1, y1, x2, y2 = manifest.slots[placement.slot].bounds
        assert rf"\pos({(x1 + x2) // 2},{(y1 + y2) // 2})" in content


def test_all_theme_layout_rules_center_text_in_measured_panels() -> None:
    expected_score_centers = {
        "theme-1": ((559, 567), (562, 978), (562, 1362)),
        "theme-2": ((567, 575), (572, 1020), (577, 1417)),
        "theme-3": ((615, 625), (615, 1032), (615, 1405)),
        "theme-4": ((562, 587), (565, 1005), (572, 1405)),
        "theme-5": ((565, 604), (565, 1025), (567, 1415)),
    }
    expected_review_centers = {
        "theme-1": ((540, 1076), (540, 1251)),
        "theme-2": ((540, 1075), (540, 1249)),
        "theme-3": ((540, 1102), (540, 1281)),
        "theme-4": ((540, 1079), (540, 1255)),
        "theme-5": ((540, 1082), (540, 1262)),
    }
    for theme_id, manifest in MinigameThemeService().discover().items():
        centers = []
        for name in ("score_beginner", "score_good", "score_perfect"):
            x1, y1, x2, y2 = manifest.slots[name].bounds
            centers.append(((x1 + x2) // 2, (y1 + y2) // 2))
        assert tuple(centers) == expected_score_centers[theme_id]

        review_centers = []
        for name in ("review_ipa", "review_word_type"):
            x1, y1, x2, y2 = manifest.slots[name].bounds
            review_centers.append(((x1 + x2) // 2, (y1 + y2) // 2))
        assert tuple(review_centers) == expected_review_centers[theme_id]


def test_answer_footer_regions_do_not_overlap() -> None:
    for manifest in MinigameThemeService().discover().values():
        word_type = manifest.slots["answer_word_type"].bounds
        voice_header = manifest.slots["voice_header"].bounds
        voice_helper = manifest.slots["voice_helper"].bounds
        assert word_type[3] < voice_header[1]
        assert voice_header[3] < voice_helper[1]


def test_bilingual_review_example_uses_separate_centered_regions(tmp_path: Path) -> None:
    manifest = MinigameThemeService().get("theme-3")
    item = VocabularyItem(
        "hành lý",
        "Luggage",
        "/ˈlʌɡ.ɪdʒ/",
        "My luggage is ready.",
        "Noun",
        "Hành lý của tôi đã sẵn sàng.",
    )
    project = MinigameProject(topic="Travel", vocabulary=[item])
    timeline = MinigameTimeline([MinigameScene("review", 0, 2_000, 0)], [], 2_000)

    content = (
        MinigameSubtitleBuilder()
        .build(project, timeline, tmp_path / "bilingual-review.ass", "9:16", manifest)
        .read_text(encoding="utf-8-sig")
    )

    assert ",ReviewExample," in content
    assert ",ReviewExampleVi," in content
    for name in ("review_example_en", "review_example_vi"):
        placement = manifest.placements[name]
        x1, y1, x2, y2 = manifest.slots[placement.slot].bounds
        assert rf"\pos({(x1 + x2) // 2},{(y1 + y2) // 2})" in content


def test_json_only_fields_are_rendered_in_answer_and_review(tmp_path: Path) -> None:
    manifest = MinigameThemeService().get("theme-2")
    item = VocabularyItem(
        "bác sĩ",
        "Doctor",
        "/ˈdɒk.tər/",
        "The doctor checks the patient.",
        "Noun",
        "Bác sĩ khám cho bệnh nhân.",
    )
    project = MinigameProject(topic="Hospital", vocabulary=[item])
    timeline = MinigameTimeline(
        [
            MinigameScene("word", 0, 1_000, 0, "answer"),
            MinigameScene("review", 1_000, 2_000, 0),
        ],
        [],
        2_000,
    )

    content = (
        MinigameSubtitleBuilder()
        .build(project, timeline, tmp_path / "json-fields.ass", "9:16", manifest)
        .read_text(encoding="utf-8-sig")
    )

    assert "WORD TYPE • NOUN" in content
    assert "NOUN" in content
    assert "Bác sĩ khám cho bệnh nhân." in content
    placement = manifest.placements["review_word_type"]
    x1, y1, x2, y2 = manifest.slots[placement.slot].bounds
    assert rf"\pos({(x1 + x2) // 2},{(y1 + y2) // 2})" in content


def test_theme_review_and_level_score_have_dedicated_content(tmp_path: Path) -> None:
    project = MinigameProject(topic="Hospital", vocabulary=[_item(1), _item(2), _item(3)])
    timeline = MinigameTimeline(
        [
            MinigameScene("review", 0, 1_000, 0),
            MinigameScene("score", 1_000, 2_000),
        ],
        [],
        2_000,
    )
    content = (
        MinigameSubtitleBuilder()
        .build(
            project,
            timeline,
            tmp_path / "ending.ass",
            "9:16",
            MinigameThemeService().get("theme-1"),
        )
        .read_text(encoding="utf-8-sig")
    )

    assert "nghĩa 1" in content
    assert "word1" in content
    assert "/wɜːd 1/" in content
    assert "This is example 1." in content
    assert "WORD TYPE" not in content
    assert "0–1 correct" in content
    assert "2/3 correct" in content
    assert "3/3 correct" in content
    assert "Save this video" not in content


def test_start_topic_is_pre_rendered_to_rgba(tmp_path: Path) -> None:
    project = MinigameProject(topic="At the Hospital", vocabulary=[_item()])
    result = MinigameTextLayerService().build_hook(
        project, MinigameThemeService().get("theme-1"), tmp_path
    )

    from PIL import Image

    with Image.open(result) as image:
        assert image.mode == "RGBA"
        assert image.getbbox() is not None


@pytest.mark.parametrize("theme_id", [f"theme-{index}" for index in range(1, 6)])
def test_digit_atlas_has_ten_isolated_rgba_cells(theme_id: str) -> None:
    manifest = MinigameThemeService().get(theme_id)
    spec = manifest.digit_atlas_spec

    with Image.open(manifest.digit_atlas) as atlas:
        assert atlas.mode == "RGBA"
        assert atlas.getchannel("A").getextrema()[0] == 0
        hashes = []
        for digit in range(10):
            column, row = digit % spec.columns, digit // spec.columns
            cell = atlas.crop((
                round(column * atlas.width / spec.columns),
                round(row * atlas.height / spec.rows),
                round((column + 1) * atlas.width / spec.columns),
                round((row + 1) * atlas.height / spec.rows),
            ))
            alpha = cell.getchannel("A").point(
                lambda value: 255 if value >= spec.alpha_threshold else 0
            )
            assert alpha.getbbox() is not None, (theme_id, digit)
            hashes.append(hashlib.sha256(alpha.tobytes()).hexdigest())
    assert len(set(hashes)) == 10


@pytest.mark.parametrize("count", [1, 5, 10, 50])
@pytest.mark.parametrize("theme_id", [f"theme-{index}" for index in range(1, 6)])
def test_dynamic_hook_review_and_cta_layers_stay_inside_declared_slots(
    tmp_path: Path, theme_id: str, count: int
) -> None:
    manifest = MinigameThemeService().get(theme_id)
    project = MinigameProject(topic="Hospital", vocabulary=[_item()] * count)
    service = MinigameTextLayerService()
    outputs = (
        (service.build_hook(project, manifest, tmp_path / theme_id / "hook"),
         ("start_topic", "hook_first", "hook_second")),
        (service.build_review_start(project, manifest, tmp_path / theme_id / "review"),
         ("review_sentence", "review_badge")),
        (service.build_cta(project, manifest, tmp_path / theme_id / "cta"),
         ("cta_eyebrow", "cta_headline", "cta_primary", "cta_secondary")),
    )
    for output, slot_names in outputs:
        with Image.open(output) as image:
            alpha = np.asarray(image.getchannel("A"))
        allowed = np.zeros_like(alpha, dtype=bool)
        for slot_name in slot_names:
            x1, y1, x2, y2 = manifest.slots[slot_name].bounds
            allowed[y1 : y2 + 1, x1 : x2 + 1] = True
        assert np.any(alpha > 0)
        assert not np.any((alpha > 0) & ~allowed)
        for slot_name in slot_names:
            x1, y1, x2, y2 = manifest.slots[slot_name].bounds
            assert np.any(alpha[y1 : y2 + 1, x1 : x2 + 1] > 0), (theme_id, count, slot_name)


@pytest.mark.parametrize("theme_id", [f"theme-{index}" for index in range(1, 6)])
def test_review_badge_uses_a_phone_readable_lockup_in_every_theme(
    tmp_path: Path, theme_id: str
) -> None:
    """Keep the score badge large enough after future theme/layout changes."""
    manifest = MinigameThemeService().get(theme_id)
    project = MinigameProject(topic="Hospital", vocabulary=[_item()] * 5)
    output = MinigameTextLayerService().build_review_start(project, manifest, tmp_path)

    with Image.open(output) as image:
        x1, y1, x2, y2 = manifest.slots["review_badge"].bounds
        lockup = image.getchannel("A").crop((x1, y1, x2, y2)).getbbox()

    assert lockup is not None
    width, height = lockup[2] - lockup[0], lockup[3] - lockup[1]
    assert width >= 240, (theme_id, width)
    assert height >= 60, (theme_id, height)


def test_theme_three_review_badge_reserves_space_for_its_wider_check_icon() -> None:
    theme_three = MinigameThemeService().get("theme-3")
    shared_badge_left = MinigameThemeService().get("theme-1").slots["review_badge"].bounds[0]

    assert theme_three.slots["review_sentence"].bounds == (145, 720, 935, 790)
    assert theme_three.slots["review_sentence"].max_lines == 1
    assert theme_three.slots["review_badge"].bounds == (463, 817, 733, 891)
    assert theme_three.slots["review_badge"].bounds[0] > shared_badge_left


@pytest.mark.parametrize("theme_id", [f"theme-{index}" for index in range(1, 6)])
def test_question_meaning_and_placeholder_short_long_matrix_fits(theme_id: str) -> None:
    manifest = MinigameThemeService().get(theme_id)
    service = MinigameTextLayerService()
    meaning = manifest.slots["meaning"]
    meaning_style = manifest.styles["question_meaning"]
    placeholder = manifest.slots["english_word"]
    placeholder_style = manifest.styles["question_placeholder"]

    short_meaning = service.fit_size(
        "Bác sĩ", meaning.bounds, meaning_style.max_size, meaning_style.min_size,
        max_lines=meaning.max_lines, font_file=meaning_style.font_file,
    )
    long_meaning = service.fit_size(
        "Chăm sóc sức khỏe và giao tiếp chuyên nghiệp tại bệnh viện",
        meaning.bounds, meaning_style.max_size, meaning_style.min_size,
        max_lines=meaning.max_lines, font_file=meaning_style.font_file,
    )
    short_placeholder = service.fit_size(
        "___", placeholder.bounds, placeholder_style.max_size, placeholder_style.min_size,
        max_lines=placeholder.max_lines, font_file=placeholder_style.font_file,
        tracking=placeholder_style.spacing,
    )
    long_placeholder = service.fit_size(
        "____________-__________-____________", placeholder.bounds,
        placeholder_style.max_size, placeholder_style.min_size,
        max_lines=placeholder.max_lines, font_file=placeholder_style.font_file,
        tracking=placeholder_style.spacing,
    )

    assert short_meaning == 104
    assert 44 <= long_meaning <= short_meaning
    assert short_placeholder == 112
    assert 40 <= long_placeholder <= short_placeholder


@pytest.mark.parametrize("theme_id", [f"theme-{index}" for index in range(1, 6)])
def test_clean_hook_base_replaces_both_baked_number_regions(theme_id: str) -> None:
    manifest = MinigameThemeService().get(theme_id)
    with Image.open(manifest.start_screen) as original_image:
        original = np.asarray(original_image.convert("RGB").resize((1080, 1920)))
    with Image.open(manifest.start_base) as clean_image:
        clean = np.asarray(clean_image.convert("RGB").resize((1080, 1920)))

    for slot_name in ("hook_first", "hook_second"):
        x1, y1, x2, y2 = manifest.slots[slot_name].bounds
        changed = np.any(original[y1 : y2 + 1, x1 : x2 + 1] != clean[y1 : y2 + 1, x1 : x2 + 1], axis=2)
        assert changed.mean() >= 0.20, (theme_id, slot_name, changed.mean())


def test_hook_and_review_cache_change_with_vocabulary_count(tmp_path: Path) -> None:
    manifest = MinigameThemeService().get("theme-1")
    service = MinigameTextLayerService()
    five = MinigameProject(topic="Hospital", vocabulary=[_item()] * 5)
    ten = MinigameProject(topic="Hospital", vocabulary=[_item()] * 10)

    assert service.build_hook(five, manifest, tmp_path) != service.build_hook(
        ten, manifest, tmp_path
    )
    assert service.build_review_start(five, manifest, tmp_path) != service.build_review_start(
        ten, manifest, tmp_path
    )


def test_long_vietnamese_topic_stays_centered_inside_start_slot(tmp_path: Path) -> None:
    manifest = MinigameThemeService().get("theme-1")
    project = MinigameProject(
        topic="Chăm sóc sức khỏe và giao tiếp tại bệnh viện", vocabulary=[_item()] * 5
    )
    result = MinigameTextLayerService().build_hook(project, manifest, tmp_path)

    with Image.open(result) as image:
        alpha = np.asarray(image.getchannel("A"))
    allowed = np.zeros_like(alpha, dtype=bool)
    x1, y1, x2, y2 = manifest.slots["start_topic"].bounds
    for slot_name in ("start_topic", "hook_first", "hook_second"):
        ax1, ay1, ax2, ay2 = manifest.slots[slot_name].bounds
        allowed[ay1 : ay2 + 1, ax1 : ax2 + 1] = True

    assert np.any(alpha > 0)
    assert not np.any((alpha > 0) & ~allowed)
    ys, xs = np.nonzero(alpha[y1 : y2 + 1, x1 : x2 + 1])
    assert abs((xs.min() + xs.max()) / 2 + x1 - (x1 + x2) / 2) <= 3
    assert abs((ys.min() + ys.max()) / 2 + y1 - (y1 + y2) / 2) <= 3


@pytest.mark.parametrize("theme_id", [f"theme-{index}" for index in range(1, 6)])
def test_each_theme_uses_start_png_and_has_a_topic_safe_box(theme_id: str) -> None:
    manifest = MinigameThemeService().get(theme_id)

    assert manifest.start_screen.name == "start.png"
    with Image.open(manifest.start_screen) as image:
        assert image.size == (941, 1672)
    x1, y1, x2, y2 = manifest.slots["start_topic"].bounds
    assert 0 <= x1 < x2 < 1080
    assert 0 <= y1 < y2 < 1920


def test_cover_export_is_atomic_and_matches_the_start_frame(tmp_path: Path) -> None:
    source = tmp_path / "start-frame.png"
    Image.new("RGBA", (1080, 1920), "navy").save(source)
    video = tmp_path / "lesson.mp4"

    cover = MinigamePipeline._write_cover(source, video)

    assert cover == tmp_path / "lesson_cover.png"
    assert cover.read_bytes() == source.read_bytes()
    assert not list(tmp_path.glob(".*.partial.png"))


def test_hook_text_cache_is_invalidated_when_bundled_font_changes(tmp_path: Path) -> None:
    text = MinigameTextLayerService()
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    for name in ("BebasNeue-Regular.ttf", "Montserrat-Variable.ttf"):
        shutil.copyfile(text.font_dir / name, font_dir / name)
    text.font_dir = font_dir
    project = MinigameProject(topic="At the Hospital", vocabulary=[_item()])
    manifest = MinigameThemeService().get("theme-1")

    initial = text.build_hook(project, manifest, tmp_path / "cache")
    shutil.copyfile(font_dir / "Montserrat-Variable.ttf", font_dir / "BebasNeue-Regular.ttf")
    changed = text.build_hook(project, manifest, tmp_path / "cache")

    assert changed != initial
    assert initial.is_file()
    assert changed.is_file()


def test_theme_render_failure_removes_partial_and_preserves_existing_output(tmp_path: Path) -> None:
    class FailingRunner:
        args: list[str]

        def preflight(self) -> dict[str, str]:
            return {"encoder": "libx264"}

        def run(self, args: list[str], **_kwargs: object) -> None:
            self.args = args
            Path(args[-1]).write_bytes(b"incomplete render")
            raise PipelineError("ffmpeg_failed", "FFmpeg failed", provider="ffmpeg")

    background = tmp_path / "background.png"
    overlay = tmp_path / "overlay.png"
    subtitles = tmp_path / "theme.ass"
    audio = tmp_path / "timeline.wav"
    output = tmp_path / "existing.mp4"
    for path in (background, overlay, subtitles, audio):
        path.write_bytes(b"fixture")
    output.write_bytes(b"previous valid output")

    runner = FailingRunner()
    with pytest.raises(PipelineError, match="FFmpeg failed"):
        MinigameVideoRenderer(runner)._render_theme(
            SimpleNamespace(duration_ms=100, path=audio),
            subtitles,
            output,
            background,
            overlay,
            start_background=None,
            hook_text_overlay=None,
            hook_duration_ms=0,
            progress=None,
            cancel=None,
        )

    assert output.read_bytes() == b"previous valid output"
    assert not (tmp_path / ".existing.partial.mp4").exists()
    assert not (tmp_path / ".existing.scene-0.partial.mp4").exists()


def test_theme_renderer_falls_back_from_gpu_to_cpu_only_once(tmp_path: Path) -> None:
    class FakeArtifact:
        def __init__(self, path: Path, duration_ms: int) -> None:
            self.path = path
            self.duration_ms = duration_ms
            self.width = 1080
            self.height = 1920

        def refresh(self):
            return self

    class FakeProbe:
        def video_artifact(self, path: Path):
            return FakeArtifact(path, 200)

        def inspect(self, _path: Path) -> dict:
            return {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}

    class GpuFailRunner:
        def __init__(self) -> None:
            self.probe = FakeProbe()
            self.encoders: list[str] = []

        def preflight(self) -> dict[str, str]:
            return {"encoder": "h264_nvenc"}

        def run(self, args: list[str], **_kwargs: object) -> None:
            encoder = args[args.index("-c:v") + 1]
            self.encoders.append(encoder)
            Path(args[-1]).write_bytes(b"rendered")
            if encoder == "h264_nvenc":
                raise PipelineError("ffmpeg_failed", "GPU failed", provider="ffmpeg")

    background = tmp_path / "background.png"
    overlay = tmp_path / "overlay.png"
    subtitles = tmp_path / "theme.ass"
    audio_path = tmp_path / "timeline.wav"
    for path in (background, overlay, subtitles, audio_path):
        path.write_bytes(b"fixture")
    runner = GpuFailRunner()

    output = MinigameVideoRenderer(runner)._render_theme(
        SimpleNamespace(duration_ms=200, path=audio_path),
        subtitles,
        tmp_path / "gpu-fallback.mp4",
        background,
        overlay,
        progress=None,
        cancel=None,
        start_background=None,
        hook_text_overlay=None,
        hook_duration_ms=0,
    )

    assert output.path.is_file()
    assert runner.encoders == ["h264_nvenc", "libx264", "libx264"]


def test_text_measurement_reduces_long_vietnamese_and_ipa_to_safe_box() -> None:
    text = MinigameTextLayerService()
    bounds = (95, 973, 985, 1235)

    short = text.fit_size("Doctor", bounds, 126, 40)
    long = text.fit_size("Từ tiếng Anh rất dài cần tự co", bounds, 126, 40)
    ipa = text.fit_size("/ˌɪn.təˌnæʃ.ə.nəl.əˈzeɪ.ʃən/", bounds, 46, 20)

    assert 40 <= long <= short <= 126
    assert 20 <= ipa <= 46
    tracked = text.fit_size("TRACKED HEADER", bounds, 60, 20, tracking=8)
    untracked = text.fit_size("TRACKED HEADER", bounds, 60, 20, tracking=0)
    assert tracked <= untracked


def test_score_ranges_are_dynamic() -> None:
    items = [_item(index) for index in range(1, 6)]
    assert score_ranges(items) == ((0, 2), (3, 4), (5, 5))
    assert format_score_range((3, 3), 5) == "3/5 correct"
    assert format_score_range((3, 4), 5) == "3–4 correct"
    assert format_score_range((3, 2), 5) == "— correct"


def test_score_range_uses_level_distribution_and_single_value_fraction() -> None:
    items = [
        replace(_item(1), level="Dễ"),
        replace(_item(2), level="Dễ"),
        replace(_item(3), level="Trung bình"),
        replace(_item(4), level="Khó"),
        replace(_item(5), level="Khó"),
    ]

    beginner, good, perfect = score_ranges(items)

    assert format_score_range(beginner, 5) == "0–2 correct"
    assert format_score_range(good, 5) == "3/5 correct"
    assert format_score_range(perfect, 5) == "4–5 correct"


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
    assert [scene.kind for scene in timeline.scenes].count("review_start") == 1
    assert [scene.kind for scene in timeline.scenes].count("cta") == 1
    review_start = next(scene for scene in timeline.scenes if scene.kind == "review_start")
    last_word = max(scene.end_ms for scene in timeline.scenes if scene.kind == "word")
    first_review = min(scene.start_ms for scene in timeline.scenes if scene.kind == "review")
    score = next(scene for scene in timeline.scenes if scene.kind == "score")
    assert score.start_ms == last_word
    assert score.end_ms == review_start.start_ms
    assert review_start.end_ms == first_review
    assert review_start.end_ms - review_start.start_ms == MinigameTimelineBuilder.REVIEW_START_MS
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
    cta = next(scene for scene in timeline.scenes if scene.kind == "cta")
    assert cta.start_ms == max(scene.end_ms for scene in timeline.scenes if scene.kind == "review")
    assert cta.end_ms - cta.start_ms == MinigameTimelineBuilder.CTA_MS
    assert MinigameTimelineBuilder.CTA_MS == 6_500


def test_review_visual_scene_sequence_matches_timeline_boundaries(tmp_path: Path) -> None:
    normal = [tmp_path / "normal.wav"]
    slow = [tmp_path / "slow.wav"]
    example = [tmp_path / "example.wav"]
    durations = {normal[0]: 500, slow[0]: 800, example[0]: 900}
    timeline = MinigameTimelineBuilder().build([_item()], normal, slow, example, durations)
    paths = [
        tmp_path / name
        for name in (
            "hook.png", "body.png", "score.png", "review-start.png", "review.png", "cta.png"
        )
    ]

    scenes = MinigamePipeline._visual_scenes(timeline, *paths)

    assert [scene.background for scene in scenes] == [
        paths[0],
        paths[1],
        paths[2],
        paths[3],
        paths[4],
        paths[5],
    ]
    assert sum(scene.duration_ms for scene in scenes) == timeline.duration_ms
    assert scenes[2].duration_ms == MinigameTimelineBuilder.SCORE_MS
    assert scenes[3].duration_ms == MinigameTimelineBuilder.REVIEW_START_MS
    assert scenes[-1].duration_ms == MinigameTimelineBuilder.CTA_MS
    assert scenes[-1].fade_in_ms == 220


@pytest.mark.parametrize(
    ("english", "expected"),
    [
        ("Doctor", "D_____"),
        ("Environment", "E__________"),
        ("ice cream", "i__ c____"),
        ("mother-in-law", "m_____-i_-l__"),
        ("teacher's", "t______'s"),
        ("e\u0301lan", "é___"),
        ("👩‍⚕️", "👩‍⚕️"),
    ],
)
def test_answer_placeholder_reveals_each_word_initial_and_keeps_unicode_graphemes(
    english: str, expected: str
) -> None:
    assert answer_placeholder(english) == expected


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
    assert "0–2 correct" in text
    assert "5/5 correct" in text
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


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
def test_minigame_speech_normalization_fades_nonzero_clip_edges(tmp_path: Path) -> None:
    """A clipped TTS waveform must enter and exit at silence, not a hard edge."""
    runner = FFmpegRunner()
    source = tmp_path / "speech-hard-edge.wav"
    runner.run(
        [
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=0.8:s=48000:d=0.12",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(source),
        ]
    )

    output = MinigameAudioService(runner).normalize(source, tmp_path / "click-safe.wav")
    with wave.open(str(output.path), "rb") as audio:
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")

    fade_samples = round(MinigameAudioService.SPEECH_EDGE_FADE_SECONDS * 48_000)
    assert abs(int(samples[0])) <= 1
    assert abs(int(samples[fade_samples])) > 10_000
    assert np.max(np.abs(samples[-100:])) <= 1


def test_minigame_page_has_json_editor_and_tracks_valid_content(qtbot) -> None:
    page = MinigamePage()
    qtbot.addWidget(page)

    assert len(MinigameContent.from_json(page.content_editor.toPlainText()).words) == 5
    assert page.aspect.count() == 3
    assert page.theme_id.count() == 5
    assert page.theme_config_button.text() == "Cấu hình theme…"
    assert "hỗ trợ 9:16" in page.theme_summary.text()
    assert page.voice_config_button.text() == "Cấu hình Voice…"
    assert page.voice_summary.text()
    page.content_editor.setPlainText(_content_json(7, "Food"))
    assert "7 từ" in page.content_status.text()
    assert page.output_name.text() == "Food"
    page.output_name.setText("Food-custom")
    page.output_name.textEdited.emit("Food-custom")
    page.content_editor.setPlainText(_content_json(7, "Travel"))
    assert page.output_name.text() == "Travel"
    page.theme_id.setCurrentIndex(4)
    result = page._collect_project()
    assert result.theme_id == "theme-5"
    assert result.topic == "Travel"
    assert len(result.vocabulary) == 7
    assert "Theme 5" in page.theme_summary.text()


def test_minigame_page_orders_voice_and_export_before_required_content(qtbot) -> None:
    page = MinigamePage()
    qtbot.addWidget(page)

    titles = [label.text() for label in page.findChildren(QLabel) if label.objectName() == "SectionTitle"]

    assert titles.index("3. Cấu hình giọng đọc") < titles.index("4. Xuất video")
    assert titles.index("4. Xuất video") < titles.index("5. Nội dung bắt buộc")
    assert page.render_button.parent() is page.action_dock
    assert page.action_dock.parent() is page
    assert page.action_dock is not page.config_scroll.widget()


def test_minigame_page_keeps_last_valid_content_while_json_is_incomplete(qtbot) -> None:
    page = MinigamePage()
    qtbot.addWidget(page)
    page.content_editor.setPlainText(_content_json(2, "Travel"))
    page.content_editor.setPlainText("{")

    assert "JSON chưa hợp lệ" in page.content_status.text()
    assert page._collect_project().topic == "Travel"
    with pytest.raises(ValueError, match="JSON không hợp lệ"):
        page._collect_project(strict_content=True)


def test_minigame_page_uses_json_topic_after_restoring_new_project(qtbot) -> None:
    page = MinigamePage()
    qtbot.addWidget(page)
    # This mirrors MainWindow's normal startup path.
    page.set_project(MinigameProject())

    page.content_editor.setPlainText(_content_json(2, "Travel"))

    assert page.output_name.text() == "Travel"


def test_minigame_page_reuses_last_theme_voice_and_export_for_new_project(
    qtbot, tmp_path: Path
) -> None:
    settings_path = tmp_path / "minigame-settings.ini"
    source = MinigamePage(QSettings(str(settings_path), QSettings.Format.IniFormat))
    qtbot.addWidget(source)
    source.theme_id.setCurrentIndex(source.theme_id.findData("theme-5"))
    source.output_folder.setText(str(tmp_path / "exports"))
    source.output_folder.editingFinished.emit()
    source.voice_provider = "google"
    source.voice_id = "en-GB-Neural2-A"
    source.voice_name = "English UK A"
    source.voice_language = "en-GB"
    source.voice_gender = "MALE"
    source.voice_family = "Neural2"
    source.voice_categories = ["Neural2", "en"]
    source.voice_volume = 88
    source.slow_rate = 0.65
    source._cache_voice_configuration()
    source.preferences.sync()

    restored = MinigamePage(QSettings(str(settings_path), QSettings.Format.IniFormat))
    qtbot.addWidget(restored)
    restored.set_project(MinigameProject())

    assert restored.theme_id.currentData() == "theme-5"
    assert restored.aspect.currentData() == "9:16"
    assert restored.output_folder.text() == str(tmp_path / "exports")
    assert restored.voice_id == "en-GB-Neural2-A"
    assert restored.voice_name == "English UK A"
    assert restored.voice_language == "en-GB"
    assert restored.voice_volume == 88
    assert restored.slow_rate == pytest.approx(0.65)


def test_minigame_preview_runs_debounced_and_safe_boxes_use_manifest(qtbot) -> None:
    page = MinigamePage()
    qtbot.addWidget(page)
    page.show()
    page.content_editor.setPlainText(_content_json(2, "Chăm sóc sức khỏe bằng tiếng Việt"))
    page.theme_id.setCurrentIndex(2)

    qtbot.waitUntil(
        lambda: page.preview_thread is None and not page._preview_base.isNull(), timeout=10_000
    )
    assert page._collect_project().theme_id == "theme-3"
    assert not page.preview.pixmap().isNull()
    normal_key = page.preview.pixmap().cacheKey()

    page.safe_box_debug.setChecked(True)

    assert page.preview.pixmap().cacheKey() != normal_key
    assert page.shutdown()


def test_minigame_page_round_trips_selected_theme_and_content_safely(qtbot) -> None:
    source = MinigamePage()
    restored = MinigamePage()
    qtbot.addWidget(source)
    qtbot.addWidget(restored)
    source.show()
    restored.show()
    source.theme_id.setCurrentIndex(4)
    values = (
        "bệnh viện",
        "hospital",
        "/ˈhɒs.pɪ.təl/",
        "She works at a hospital.",
        "Noun",
        "Cô ấy làm việc tại bệnh viện.",
    )
    source.content_editor.setPlainText(
        MinigameContent("Hospital", [VocabularyItem(*values)]).to_json()
    )
    source.service_account_path = "C:/secret/service-account.json"

    payload = source._collect_project().to_safe_dict()
    restored.set_project(MinigameProject.from_dict(payload))
    result = restored._collect_project()

    assert result.theme_id == "theme-5"
    assert result.topic == "Hospital"
    assert result.vocabulary[0] == VocabularyItem(*values)
    assert MinigameContent.from_json(restored.content_editor.toPlainText()).words[
        0
    ] == VocabularyItem(*values)
    assert payload["service_account_path"] == ""
    assert source.shutdown()
    assert restored.shutdown()


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
    assert dialog.width() >= 1_100
    assert dialog.list.gridSize().width() == 246
    assert dialog.list.gridSize().height() == 62
    assert not hasattr(dialog, "family")
    assert not hasattr(dialog, "category")
    voice_card = dialog.list.itemWidget(dialog.list.item(0))
    assert voice_card is not None
    assert "EU" in [label.text() for label in voice_card.findChildren(QLabel)]
    assert "af-ZA, en-US  ·  Female" in [label.text() for label in voice_card.findChildren(QLabel)]
    assert dialog.preview_panel.name.text() == "en-US-Neural2-F"
    assert dialog.preview_panel.preview.text() == "▶  Nghe thử"
    assert "Tốc độ lần đọc chậm" in [
        label.text() for label in dialog.preview_panel.findChildren(QLabel)
    ]
    assert dialog.controls.button(QDialogButtonBox.StandardButton.Save).text() == "Lưu & đóng"
    config = dialog.configuration()
    assert config["voice"] == "en-US-Neural2-F"
    assert config["voice_language"] == "en-US"
    assert config["voice_family"] == "Neural2"
    dialog.language.setCurrentIndex(0)
    assert dialog.list.count() == 2

    dialog.search.setText("không có voice này")
    assert dialog.catalog_stack.currentWidget() is dialog.catalog_state
    assert "Không tìm thấy" in dialog.catalog_state.message.text()


def test_voice_dialog_preview_and_provider_error_states(qtbot) -> None:
    dialog = MinigameVoiceDialog(
        provider="google",
        service_account_path="missing.json",
        selected_voice_id="",
        selected_language="en-US",
        volume=100,
        slow_rate=0.72,
        auto_load=False,
    )
    qtbot.addWidget(dialog)

    dialog.preview_panel.text.setPlainText("x" * 350)
    assert len(dialog.preview_panel.text.toPlainText()) == 300
    assert dialog.preview_panel.count.text() == "300 / 300"

    dialog._catalog_loading = True
    dialog._failed("Service account không hợp lệ.")
    assert dialog.catalog_stack.currentWidget() is dialog.catalog_state
    assert dialog.provider_settings.property("invalid") is True
    assert dialog.provider_settings.badge.text() == "Lỗi kết nối"


def test_google_preview_uses_custom_preview_text(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, str] = {}

    class FakeSpeechService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def synthesize(self, text: str, output: Path) -> Path:
            captured["text"] = text
            return output

    monkeypatch.setattr(
        "quyetboit_studio.services.minigame_tts.GoogleCloudSpeechService",
        FakeSpeechService,
    )
    voice = CatalogVoice("google", "en-US-Neural2-F", "Voice", ("en-US",), "FEMALE", "Neural2")

    GoogleVoiceCatalogService(tmp_path / "account.json").preview(voice, "Custom preview")

    assert captured["text"] == "Custom preview"


def test_google_voice_catalog_uses_disk_cache_until_explicit_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    cache_path = tmp_path / "google-voices.json"
    calls = 0

    class FakeClient:
        def list_voices(self, timeout: int):
            nonlocal calls
            assert timeout == 60
            calls += 1
            suffix = "A" if calls == 1 else "B"
            return SimpleNamespace(
                voices=[
                    SimpleNamespace(
                        name=f"en-US-Neural2-{suffix}",
                        language_codes=["en-US"],
                        ssml_gender=1,
                        natural_sample_rate_hertz=24_000,
                    )
                ]
            )

    module = SimpleNamespace(
        SsmlVoiceGender=lambda _value: SimpleNamespace(name="FEMALE")
    )
    service = GoogleVoiceCatalogService(
        tmp_path / "account.json", cache_path=cache_path
    )
    monkeypatch.setattr(service, "_module_and_client", lambda: (module, FakeClient()))

    assert service.list_voices()[0].voice_id == "en-US-Neural2-A"
    assert cache_path.is_file()
    cached_service = GoogleVoiceCatalogService(
        tmp_path / "missing-account.json", cache_path=cache_path
    )
    monkeypatch.setattr(
        cached_service,
        "_module_and_client",
        lambda: pytest.fail("Không được gọi Google khi cache hợp lệ"),
    )
    assert cached_service.list_voices()[0].voice_id == "en-US-Neural2-A"
    assert calls == 1

    assert service.list_voices(refresh=True)[0].voice_id == "en-US-Neural2-B"
    assert calls == 2


def test_google_voice_catalog_recovers_from_invalid_cache(tmp_path: Path, monkeypatch) -> None:
    cache_path = tmp_path / "google-voices.json"
    cache_path.write_text("{invalid", encoding="utf-8")
    client = SimpleNamespace(
        list_voices=lambda timeout: SimpleNamespace(
            voices=[
                SimpleNamespace(
                    name="vi-VN-Standard-A",
                    language_codes=["vi-VN"],
                    ssml_gender=1,
                    natural_sample_rate_hertz=24_000,
                )
            ]
        )
    )
    module = SimpleNamespace(
        SsmlVoiceGender=lambda _value: SimpleNamespace(name="FEMALE")
    )
    service = GoogleVoiceCatalogService(
        tmp_path / "account.json", cache_path=cache_path
    )
    monkeypatch.setattr(service, "_module_and_client", lambda: (module, client))

    assert service.list_voices()[0].voice_id == "vi-VN-Standard-A"


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


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
def test_theme_renderer_uses_bundled_fonts_with_libass(tmp_path: Path) -> None:
    runner = FFmpegRunner()
    audio_path = tmp_path / "timeline.wav"
    runner.run(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2:sample_rate=48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
    )
    audio = runner.probe.audio_artifact(audio_path)
    manifest = MinigameThemeService().get("theme-1")
    project = MinigameProject(topic="Test", vocabulary=[_item()])
    timeline = MinigameTimeline(
        [MinigameScene("hook", 0, audio.duration_ms)], [], audio.duration_ms
    )
    subtitles = MinigameSubtitleBuilder().build(
        project, timeline, tmp_path / "theme.ass", "9:16", manifest
    )
    hook_text = MinigameTextLayerService().build_hook(project, manifest, tmp_path / "hook")
    output = MinigameVideoRenderer(runner).render(
        None,
        audio,
        subtitles,
        tmp_path / "theme.mp4",
        "9:16",
        [0.5, 0.5],
        theme_background=manifest.background_video,
        theme_overlay=MinigameAlphaMaskService().build(manifest, tmp_path / "mask"),
        start_background=manifest.start_screen,
        hook_text_overlay=hook_text,
        hook_duration_ms=100,
    )

    assert (output.width, output.height) == (1080, 1920)
    assert not list(tmp_path.glob(".*.scene-*.mp4"))


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
def test_theme_renderer_composites_theme_text_over_custom_background_video(tmp_path: Path) -> None:
    runner = FFmpegRunner()
    source = tmp_path / "custom-background.mp4"
    audio_path = tmp_path / "timeline.wav"
    runner.run(
        [
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=0.25",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ]
    )
    runner.run(
        [
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=0.25",
            "-c:a", "pcm_s16le", str(audio_path),
        ]
    )
    audio = runner.probe.audio_artifact(audio_path)
    manifest = MinigameThemeService().get("theme-1")
    mask = MinigameAlphaMaskService().build(manifest, tmp_path / "mask")
    subtitles = MinigameSubtitleBuilder().build(
        MinigameProject(topic="Test", vocabulary=[_item()]),
        MinigameTimeline([], [], audio.duration_ms),
        tmp_path / "custom-background.ass",
        "9:16",
        manifest,
    )
    progress: list[float] = []

    output = MinigameVideoRenderer(runner).render(
        None,
        audio,
        subtitles,
        tmp_path / "custom-background-result.mp4",
        "9:16",
        [0.2, 0.8],
        theme_background=manifest.background_video,
        theme_overlay=mask,
        visual_scenes=[
            MinigameVisualScene(
                source, audio.duration_ms, (mask,), background_is_video=True, crop_focus=(0.2, 0.8)
            )
        ],
        progress=lambda event: progress.append(event.fraction),
    )

    assert (output.width, output.height) == (1080, 1920)
    assert progress[-1] == 1.0
    assert not list(tmp_path.glob(".*.scene-*.mp4"))


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
def test_real_video_theme_runs_under_full_flow_and_exports_matching_cover(tmp_path: Path) -> None:
    runner = FFmpegRunner()
    source = tmp_path / "full-flow-background.mp4"
    audio_path = tmp_path / "full-flow.wav"
    runner.run(
        [
            "-f", "lavfi", "-i", "testsrc2=size=480x854:rate=30:duration=0.4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ]
    )
    runner.run(
        [
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1.2",
            "-c:a", "pcm_s16le", str(audio_path),
        ]
    )
    audio = runner.probe.audio_artifact(audio_path)
    timeline = MinigameTimeline(
        [
            MinigameScene("hook", 0, 200),
            MinigameScene("word", 200, 400, 0, "question"),
            MinigameScene("score", 400, 600),
            MinigameScene("review_start", 600, 800),
            MinigameScene("review", 800, 1_000, 0),
            MinigameScene("cta", 1_000, 1_200),
        ],
        [],
        1_200,
    )
    project = MinigameProject(topic="Travel", vocabulary=[_item()])
    video_themes = MinigameVideoThemeService()
    manifest = video_themes.manifest("video-violet")
    surfaces = video_themes.build_surfaces("video-violet", manifest, tmp_path / "surfaces")
    text_layers = MinigameTextLayerService()
    hook_text = text_layers.build_hook(project, manifest, tmp_path / "text")
    review_start_text = text_layers.build_review_start(project, manifest, tmp_path / "text")
    cta_text = text_layers.build_cta(project, manifest, tmp_path / "text")
    subtitles = MinigameSubtitleBuilder().build(
        project, timeline, tmp_path / "full-flow.ass", "9:16", manifest
    )
    scenes = MinigamePipeline._video_visual_scenes(
        timeline,
        source,
        surfaces,
        hook_text,
        review_start_text,
        cta_text,
        [0.5, 0.5],
    )
    progress: list[float] = []

    output = MinigameVideoRenderer(runner).render(
        None,
        audio,
        subtitles,
        tmp_path / "full-flow.mp4",
        "9:16",
        [0.5, 0.5],
        theme_background=manifest.background_video,
        visual_scenes=scenes,
        progress=lambda event: progress.append(event.fraction),
    )
    cover = MinigamePipeline(project, workspace=tmp_path / "workspace", runner=runner)._write_video_cover(
        output.path
    )

    assert (output.width, output.height) == (1080, 1920)
    assert abs(output.duration_ms - timeline.duration_ms) <= 100
    assert progress[-1] == 1.0
    with Image.open(cover) as image:
        assert image.size == (1080, 1920)
        assert np.asarray(image.convert("RGB")).std() > 10
    assert not list(tmp_path.glob(".*.scene-*.mp4"))
    assert not list(tmp_path.glob(".*.partial*"))


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
def test_real_question_answer_voice_review_frames_have_expected_pixel_changes(
    tmp_path: Path,
) -> None:
    runner = FFmpegRunner()
    audio_path = tmp_path / "timeline.wav"
    runner.run(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2:sample_rate=48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
    )
    audio = runner.probe.audio_artifact(audio_path)
    item = VocabularyItem(
        "chăm sóc sức khỏe chuyên sâu",
        "healthcare",
        "/ˈhelθ.keər/",
        "Healthcare workers support patients every day in the hospital.",
    )
    project = MinigameProject(topic="Hospital", vocabulary=[item])
    timeline = MinigameTimeline(
        [
            MinigameScene("word", 0, 500, 0, "question"),
            MinigameScene("word", 500, 1_000, 0, "answer"),
            MinigameScene("voice", 1_000, 1_500, 0, "listen"),
            MinigameScene("review", 1_500, 2_000, 0),
        ],
        [],
        2_000,
    )
    manifest = MinigameThemeService().get("theme-1")
    mask = MinigameAlphaMaskService().build(manifest, tmp_path / "mask")
    body = MinigameVisualFrameService().build(
        manifest.background_video, (mask,), tmp_path / "frames", layout_key=manifest.layout_version
    )
    subtitles = MinigameSubtitleBuilder().build(
        project, timeline, tmp_path / "scenes.ass", "9:16", manifest
    )
    progress: list[float] = []
    video = MinigameVideoRenderer(runner).render(
        None,
        audio,
        subtitles,
        tmp_path / "scenes.mp4",
        "9:16",
        [0.5, 0.5],
        theme_background=manifest.background_video,
        theme_overlay=mask,
        visual_scenes=[
            MinigameVisualScene(body, 1_500),
            MinigameVisualScene(manifest.review_screen, 500),
        ],
        progress=lambda event: progress.append(event.fraction),
    )
    samples: list[np.ndarray] = []
    for index, timestamp in enumerate((0.25, 0.75, 1.25, 1.75)):
        path = tmp_path / f"scene-{index}.png"
        runner.run(["-ss", str(timestamp), "-i", str(video.path), "-frames:v", "1", str(path)])
        with Image.open(path) as image:
            samples.append(np.asarray(image.convert("RGB")))

    word = manifest.slots["word"].bounds
    phase = manifest.slots["phase"].bounds
    word_diff = np.abs(samples[1].astype(np.int16) - samples[0].astype(np.int16))
    phase_diff = np.abs(samples[3].astype(np.int16) - samples[2].astype(np.int16))
    assert np.count_nonzero(word_diff[word[1] : word[3], word[0] : word[2]]) > 500
    assert np.count_nonzero(phase_diff[phase[1] : phase[3], phase[0] : phase[2]]) > 500
    assert np.mean(phase_diff) > 5
    assert progress[-1] == 1.0


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
def test_cancel_real_theme_render_preserves_output_and_cleans_process_files(tmp_path: Path) -> None:
    runner = FFmpegRunner()
    audio_path = tmp_path / "timeline.wav"
    runner.run(
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono:d=10",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
    )
    audio = runner.probe.audio_artifact(audio_path)
    manifest = MinigameThemeService().get("theme-1")
    mask = MinigameAlphaMaskService().build(manifest, tmp_path / "mask")
    body = MinigameVisualFrameService().build(
        manifest.background_video, (mask,), tmp_path / "frames", layout_key=manifest.layout_version
    )
    subtitles = MinigameSubtitleBuilder().build(
        MinigameProject(topic="Cancel", vocabulary=[_item()]),
        MinigameTimeline([], [], audio.duration_ms),
        tmp_path / "cancel.ass",
        "9:16",
        manifest,
    )
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"previous output")
    cancel = CancellationToken()
    timer = threading.Timer(0.15, cancel.cancel)
    timer.start()

    with pytest.raises(PipelineError) as caught:
        MinigameVideoRenderer(runner).render(
            None,
            audio,
            subtitles,
            output,
            "9:16",
            [0.5, 0.5],
            theme_background=manifest.background_video,
            theme_overlay=mask,
            visual_scenes=[MinigameVisualScene(body, audio.duration_ms)],
            cancel=cancel,
        )
    timer.join()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and any(
        thread.name == "ffmpeg-cancel" for thread in threading.enumerate()
    ):
        time.sleep(0.02)

    assert caught.value.error_code == "cancelled"
    assert output.read_bytes() == b"previous output"
    assert not list(tmp_path.glob(".*.partial.mp4"))
    assert not list(tmp_path.glob(".*.scene-*.mp4"))
    assert not any(thread.name == "ffmpeg-cancel" for thread in threading.enumerate())


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
@pytest.mark.parametrize(
    ("theme_id", "word_count"),
    (
        [(f"theme-{theme}", count) for theme in range(1, 6) for count in (5, 10)]
        if os.environ.get("QB_MINIGAME_FULL_VISUAL") == "1"
        else [("theme-1", 5), ("theme-5", 5)]
    ),
)
def test_pipeline_renders_complete_playable_timeline_without_live_service(
    tmp_path: Path,
    theme_id: str,
    word_count: int,
) -> None:
    runner = FFmpegRunner()
    fixture = tmp_path / "speech-fixture.wav"
    runner.run(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=0.12:sample_rate=48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(fixture),
        ]
    )

    class FixtureSpeech:
        def synthesize(self, _text: str, output: Path, **_kwargs: object) -> None:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fixture, output)

    output_dir = tmp_path / "output"
    project = MinigameProject(
        topic="Hospital",
        theme_id=theme_id,
        output_folder=str(output_dir),
        output_name=f"{word_count}-word-{theme_id}",
        tts_provider="capcut",
        voice="fixture-voice",
        vocabulary=[_item(index) for index in range(1, word_count + 1)],
    )
    progress = []
    result = MinigamePipeline(
        project,
        workspace=tmp_path / "workspace",
        speech_factory=lambda _project: FixtureSpeech(),
        runner=runner,
    ).run(progress.append)

    assert result.word_count == word_count
    assert len(result.outputs) == 1
    assert len(result.covers) == 1
    assert result.covers[0].name == f"{result.outputs[0].stem}_cover.png"
    with Image.open(result.covers[0]) as cover:
        assert cover.size == (1080, 1920)
    artifact = runner.probe.video_artifact(result.outputs[0])
    info = runner.probe.inspect(result.outputs[0])
    assert (artifact.width, artifact.height) == (1080, 1920)
    assert artifact.fps == pytest.approx(30, abs=0.05)
    assert abs(artifact.duration_ms - result.duration_ms) <= 100
    assert {stream["codec_type"] for stream in info["streams"]} >= {"video", "audio"}
    assert progress[-1].fraction == 1.0
    assert not list(tmp_path.rglob("*.partial.*"))


def test_every_theme_ass_font_is_declared_by_the_bundled_manifest() -> None:
    manifest = MinigameThemeService().get("theme-1")
    declared_files = {style.font_file for style in manifest.styles.values()}

    assert declared_files == {
        "BebasNeue-Regular.ttf",
        "Montserrat-Variable.ttf",
        "Montserrat-Italic-Variable.ttf",
    }
    assert all((MinigameTextLayerService().font_dir / name).is_file() for name in declared_files)
