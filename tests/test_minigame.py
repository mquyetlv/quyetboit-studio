from __future__ import annotations

import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from quyetboit_studio.core.minigame import (
    MinigameProject,
    MinigameScene,
    MinigameTimeline,
    MinigameTimelineBuilder,
    VocabularyItem,
    score_ranges,
)
from quyetboit_studio.core.models import CancellationToken, PipelineError
from quyetboit_studio.minigame import MinigamePage
from quyetboit_studio.minigame_voice import MinigameVoiceDialog
from quyetboit_studio.pipeline.minigame import MinigamePipeline
from quyetboit_studio.services.media import FFmpegRunner
from quyetboit_studio.services.media.minigame import (
    MinigameAudioService,
    MinigameSubtitleBuilder,
    MinigameVideoRenderer,
    MinigameVisualScene,
)
from quyetboit_studio.services.media.minigame_frame import MinigameVisualFrameService
from quyetboit_studio.services.media.minigame_mask import MinigameAlphaMaskService
from quyetboit_studio.services.media.minigame_text import MinigameTextLayerService
from quyetboit_studio.services.media.minigame_theme import MinigameThemeService
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


def test_legacy_minigame_project_migrates_to_default_theme() -> None:
    project = MinigameProject.from_dict(
        {"schema_version": 1, "topic": "Food", "background_video": "legacy.mp4"}
    )

    assert project.schema_version == 2
    assert project.theme_id == "theme-1"
    assert project.background_video == "legacy.mp4"


def test_image_theme_does_not_require_legacy_background_video() -> None:
    project = MinigameProject(
        topic="Food", tts_provider="capcut", voice="voice", vocabulary=[_item()]
    )

    project.validate()


def test_theme_discovery_and_mask_are_complete_for_all_five_themes(tmp_path: Path) -> None:
    manifests = MinigameThemeService().discover()

    assert set(manifests) == {f"theme-{index}" for index in range(1, 6)}
    for manifest in manifests.values():
        assert manifest.start_screen.is_file()
        assert manifest.background_video.is_file()
        assert manifest.review_start_screen.is_file()
        assert manifest.review_screen.is_file()
        result = MinigameAlphaMaskService().build(manifest, tmp_path)
        from PIL import Image

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


def test_alpha_masks_have_expected_coverage_feather_and_detail_for_all_themes(tmp_path: Path) -> None:
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

    first_tts_key = MinigamePipeline(first_project, workspace=tmp_path / "pipeline-1")._speech_cache_key(
        "healthcare"
    )
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
    assert np.count_nonzero(np.any(composite[translucent, :3] != background[translucent, :3], axis=1))
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
    assert "REVEALING ANSWER IN" in content
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
    styles["label"] = replace(styles["label"], max_size=19)
    manifest = replace(manifest, placements=placements, styles=styles)
    project = MinigameProject(topic="Hospital", vocabulary=[_item()])
    timeline = MinigameTimeline(
        [MinigameScene("word", 0, 1_000, 0, "question")], [], 1_000
    )

    content = MinigameSubtitleBuilder().build(
        project, timeline, tmp_path / "manifest-layout.ass", "9:16", manifest
    ).read_text(encoding="utf-8-sig")

    assert "Style: Label,Montserrat,19" in content
    assert r"\pos(540,444)\fs19" in content


def test_question_scene_never_contains_english_or_ipa_before_reveal(tmp_path: Path) -> None:
    project = MinigameProject(topic="Hospital", vocabulary=[_item()])
    timeline = MinigameTimeline(
        [MinigameScene("word", 0, 3_000, 0, "question")], [], 3_000
    )

    content = MinigameSubtitleBuilder().build(
        project,
        timeline,
        tmp_path / "question-only.ass",
        "9:16",
        MinigameThemeService().get("theme-1"),
    ).read_text(encoding="utf-8-sig")

    assert "nghĩa 1" in content
    assert "word1" not in content
    assert "/wɜːd 1/" not in content
    assert "REVEALING ANSWER IN" in content


def test_voice_phase_text_uses_exact_scene_timestamp(tmp_path: Path) -> None:
    project = MinigameProject(topic="Hospital", vocabulary=[_item()])
    timeline = MinigameTimeline(
        [MinigameScene("voice", 2_100, 2_500, 0, "listen")], [], 2_500
    )

    content = MinigameSubtitleBuilder().build(
        project,
        timeline,
        tmp_path / "voice-time.ass",
        "9:16",
        MinigameThemeService().get("theme-1"),
    ).read_text(encoding="utf-8-sig")

    assert "Dialogue: 2,0:00:02.10,0:00:02.50,Header" in content
    assert "1 • LISTEN" in content


def test_review_example_wraps_by_bundled_font_pixel_width(tmp_path: Path) -> None:
    item = VocabularyItem(
        "chăm sóc sức khỏe",
        "healthcare",
        "/ˈhelθ.keər/",
        "This deliberately long example sentence must wrap inside the review panel safely.",
    )
    project = MinigameProject(topic="Hospital", vocabulary=[item])
    timeline = MinigameTimeline([MinigameScene("review", 0, 2_000, 0)], [], 2_000)

    content = MinigameSubtitleBuilder().build(
        project,
        timeline,
        tmp_path / "review-wrap.ass",
        "9:16",
        MinigameThemeService().get("theme-1"),
    ).read_text(encoding="utf-8-sig")

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

    content = MinigameSubtitleBuilder().build(
        project, timeline, tmp_path / "new-review.ass", "9:16", manifest
    ).read_text(encoding="utf-8-sig")

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


def test_theme_review_score_and_cta_have_dedicated_content(tmp_path: Path) -> None:
    project = MinigameProject(topic="Hospital", vocabulary=[_item()])
    timeline = MinigameTimeline(
        [
            MinigameScene("review", 0, 1_000, 0),
            MinigameScene("score", 1_000, 2_000),
            MinigameScene("cta", 2_000, 3_000),
        ],
        [],
        3_000,
    )
    content = MinigameSubtitleBuilder().build(
        project, timeline, tmp_path / "ending.ass", "9:16", MinigameThemeService().get("theme-1")
    ).read_text(encoding="utf-8-sig")

    assert "nghĩa 1" in content
    assert "word1" in content
    assert "/wɜːd 1/" in content
    assert "This is example 1." in content
    assert "WORD TYPE" not in content
    assert "CHALLENGE COMPLETE" in content
    assert "PERFECT SCORE" in content
    assert "Save this video" in content


def test_hook_title_and_topic_are_pre_rendered_to_rgba(tmp_path: Path) -> None:
    project = MinigameProject(topic="At the Hospital", vocabulary=[_item()])
    result = MinigameTextLayerService().build_hook(project, MinigameThemeService().get("theme-1"), tmp_path)

    from PIL import Image

    with Image.open(result) as image:
        assert image.mode == "RGBA"
        assert image.getbbox() is not None


def test_hook_title_and_long_vietnamese_topic_stay_inside_manifest_slots(tmp_path: Path) -> None:
    manifest = MinigameThemeService().get("theme-1")
    project = MinigameProject(
        topic="Chăm sóc sức khỏe và giao tiếp tại bệnh viện", vocabulary=[_item()] * 5
    )
    result = MinigameTextLayerService().build_hook(project, manifest, tmp_path)

    with Image.open(result) as image:
        alpha = np.asarray(image.getchannel("A"))
    allowed = np.zeros_like(alpha, dtype=bool)
    for name in ("start_title", "start_topic"):
        x1, y1, x2, y2 = manifest.slots[name].bounds
        allowed[y1 : y2 + 1, x1 : x2 + 1] = True

    assert np.any(alpha > 0)
    assert not np.any((alpha > 0) & ~allowed)
    for name in ("start_title", "start_topic"):
        x1, y1, x2, y2 = manifest.slots[name].bounds
        ys, xs = np.nonzero(alpha[y1 : y2 + 1, x1 : x2 + 1])
        assert abs((xs.min() + xs.max()) / 2 + x1 - (x1 + x2) / 2) <= 2
        assert abs((ys.min() + ys.max()) / 2 + y1 - (y1 + y2) / 2) <= 2


def test_hook_title_uses_approved_single_line_format() -> None:
    title = MinigameTextLayerService.hook_title(5)

    assert title == "CAN YOU GET 5/5?"
    assert "\n" not in title


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
            SimpleNamespace(duration_ms=100, path=audio), subtitles, output, background, overlay,
            start_background=None, hook_text_overlay=None, hook_duration_ms=0,
            progress=None, cancel=None,
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
    assert [scene.kind for scene in timeline.scenes].count("review_start") == 1
    review_start = next(scene for scene in timeline.scenes if scene.kind == "review_start")
    last_word = max(scene.end_ms for scene in timeline.scenes if scene.kind == "word")
    first_review = min(scene.start_ms for scene in timeline.scenes if scene.kind == "review")
    assert review_start.start_ms == last_word
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


def test_review_visual_scene_sequence_matches_timeline_boundaries(tmp_path: Path) -> None:
    normal = [tmp_path / "normal.wav"]
    slow = [tmp_path / "slow.wav"]
    example = [tmp_path / "example.wav"]
    durations = {normal[0]: 500, slow[0]: 800, example[0]: 900}
    timeline = MinigameTimelineBuilder().build([_item()], normal, slow, example, durations)
    paths = [tmp_path / name for name in ("hook.png", "body.png", "review-start.png", "review.png")]

    scenes = MinigamePipeline._visual_scenes(timeline, *paths)

    assert [scene.background for scene in scenes] == [
        paths[0],
        paths[1],
        paths[2],
        paths[3],
        paths[1],
    ]
    assert sum(scene.duration_ms for scene in scenes) == timeline.duration_ms
    assert scenes[2].duration_ms == MinigameTimelineBuilder.REVIEW_START_MS


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
    assert page.theme_id.count() == 5
    assert len(page.theme_cards) == 5
    assert all("9:16" in card.text() for card in page.theme_cards)
    assert page.voice_config_button.text() == "Cấu hình Voice…"
    assert "Google Cloud TTS" in page.voice_summary.text()
    page.word_count.setValue(7)
    assert page.table.rowCount() == 7
    page.table.item(0, 0).setText("giữ nguyên")
    page.theme_id.setCurrentIndex(4)
    assert page._collect_project().theme_id == "theme-5"
    assert page.theme_cards[4].isChecked()
    assert page.table.item(0, 0).text() == "giữ nguyên"


def test_minigame_preview_runs_debounced_and_safe_boxes_use_manifest(qtbot) -> None:
    page = MinigamePage()
    qtbot.addWidget(page)
    page.show()
    page.topic.setText("Chăm sóc sức khỏe bằng tiếng Việt")
    page.theme_cards[2].click()

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
    source.topic.setText("Hospital")
    source.word_count.setValue(1)
    values = ("bệnh viện", "hospital", "/ˈhɒs.pɪ.təl/", "She works at a hospital.")
    for column, value in enumerate(values):
        source.table.item(0, column).setText(value)
    source.service_account_path = "C:/secret/service-account.json"

    payload = source._collect_project().to_safe_dict()
    restored.set_project(MinigameProject.from_dict(payload))
    result = restored._collect_project()

    assert result.theme_id == "theme-5"
    assert result.topic == "Hospital"
    assert result.vocabulary[0] == VocabularyItem(*values)
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
    timeline = MinigameTimeline([MinigameScene("hook", 0, audio.duration_ms)], [], audio.duration_ms)
    subtitles = MinigameSubtitleBuilder().build(project, timeline, tmp_path / "theme.ass", "9:16", manifest)
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
@pytest.mark.parametrize("theme_id", ["theme-1", "theme-5"])
def test_five_word_pipeline_renders_complete_playable_timeline_without_live_service(
    tmp_path: Path, theme_id: str,
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
        output_name=f"five-word-{theme_id}",
        tts_provider="capcut",
        voice="fixture-voice",
        vocabulary=[_item(index) for index in range(1, 6)],
    )
    progress = []
    result = MinigamePipeline(
        project,
        workspace=tmp_path / "workspace",
        speech_factory=lambda _project: FixtureSpeech(),
        runner=runner,
    ).run(progress.append)

    assert result.word_count == 5
    assert len(result.outputs) == 1
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
