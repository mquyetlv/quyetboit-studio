from __future__ import annotations

import shutil
import subprocess

import pytest

from quyetboit_studio.core.studio import new_studio_layer, normalize_studio_settings
from quyetboit_studio.services.media import FFmpegRunner, FilterGraphBuilder, VideoRenderer
from quyetboit_studio.studio import StudioDialog
from quyetboit_studio.theme import LIGHT_STYLESHEET


def test_legacy_studio_layers_are_normalized():
    settings = normalize_studio_settings({
        "zoom": 50,
        "layers": ["Video", "Voice", "Subtitle", "Text đầu video"],
        "layer_start": 2,
        "layer_duration": 4,
    })

    assert settings["zoom"] == 100
    assert [layer["kind"] for layer in settings["layers"]] == [
        "video", "voice", "subtitle", "intro_text",
    ]
    assert settings["layers"][-1]["start"] == 2
    assert settings["layers"][-1]["duration"] == 4
    assert settings["layers"][-1]["full_duration"] is True
    assert settings["layers"][2]["width_percent"] == 90


def test_filter_graph_covers_pitch_text_overlay_and_escaping(tmp_path):
    logo = tmp_path / "logo image.png"
    logo.write_bytes(b"fixture")
    settings = normalize_studio_settings({
        "speed": 1.1,
        "pitch": 2,
        "zoom": 105,
        "layers": [
            "Video", "Voice", "Subtitle",
            new_studio_layer(
                "logo", path=str(logo), start=1, duration=3, full_duration=False,
                margin=30, width_percent=25, height=70,
            ),
            new_studio_layer(
                "intro_text", text="Xin chào: 100%", margin=20, width_percent=80,
                height=72, background_color="#112233",
            ),
        ],
    })

    graph = FilterGraphBuilder().build(
        settings, subtitle=tmp_path / "phụ đề.srt", rubberband=True
    )

    assert graph.image_inputs == [logo.resolve()]
    assert "subtitles=" in graph.script
    assert "drawtext=" in graph.script
    assert "drawbox=" in graph.script
    assert "force_style=" in graph.script
    assert "Xin chào\\: 100\\%" in graph.script
    assert "overlay=" in graph.script
    assert "between(t,1,4)" in graph.script
    assert "rubberband=pitch=" in graph.script
    assert "atempo=1.1" in graph.script
    assert graph.video_output and graph.audio_output == "aout"


def test_studio_dialog_edits_real_layer_properties(qtbot):
    layer = new_studio_layer("intro_text", text="Cũ", start=1, duration=2)
    dialog = StudioDialog({"layers": ["Video", "Voice", "Subtitle", layer]})
    qtbot.addWidget(dialog)
    dialog.layers.setCurrentRow(3)

    dialog.text.setText("Nội dung mới")
    dialog.text.editingFinished.emit()
    dialog.full_duration.setChecked(False)
    dialog.layer_start.setValue(3.5)
    dialog.layer_duration.setValue(6)
    dialog.margin.setValue(36)
    dialog.width_percent.setValue(84)
    dialog.layer_height.setValue(70)
    dialog.background_color.setText("#123456")
    dialog.background_color.editingFinished.emit()
    settings = dialog.current_settings()

    edited = settings["layers"][3]
    assert edited["kind"] == "intro_text"
    assert edited["text"] == "Nội dung mới"
    assert edited["start"] == 3.5
    assert edited["duration"] == 6
    assert edited["full_duration"] is False
    assert edited["margin"] == 36
    assert edited["width_percent"] == 84
    assert edited["height"] == 70
    assert edited["background_color"] == "#123456"


def test_studio_single_line_inputs_have_enough_height_for_values(qtbot):
    dialog = StudioDialog({"layers": ["Video", "Voice", "Subtitle"]})
    dialog.setStyleSheet(LIGHT_STYLESHEET)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)

    controls = (
        dialog.asset_path, dialog.text, dialog.position, dialog.layer_start,
        dialog.layer_duration, dialog.margin, dialog.width_percent,
        dialog.layer_height, dialog.opacity, dialog.font_size,
        dialog.font_color, dialog.background_color, dialog.background_opacity,
    )
    for control in controls:
        control.ensurePolished()
        assert control.minimumHeight() >= 26
        assert control.height() >= 26


def test_renderer_skips_encode_only_when_every_effect_and_subtitle_are_disabled():
    settings = normalize_studio_settings({"layers": ["Video", "Voice", "Subtitle"]})
    next(layer for layer in settings["layers"] if layer["kind"] == "subtitle")["enabled"] = False
    assert VideoRenderer.requires_render(settings) is False
    assert VideoRenderer.requires_render(settings, subtitle="translated.vi.srt") is False
    settings["zoom"] = 105
    assert VideoRenderer.requires_render(settings) is True


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/ffprobe không có trong PATH",
)
def test_studio_integration_render_has_valid_video_audio_and_duration(tmp_path):
    source = tmp_path / "source.mp4"
    logo = tmp_path / "logo.png"
    subtitle = tmp_path / "translated.vi.srt"
    output = tmp_path / "studio.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2:r=25",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=red:s=80x40", "-frames:v", "1", "-threads", "1", str(logo),
    ], check=True)
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\nPhụ đề tiếng Việt\n",
        encoding="utf-8",
    )
    runner = FFmpegRunner()
    video = runner.probe.video_artifact(source)
    settings = normalize_studio_settings({
        "speed": 1.1,
        "pitch": 2,
        "zoom": 105,
        "color": 110,
        "layers": [
            "Video", "Voice", "Subtitle",
            new_studio_layer("logo", path=str(logo), position="top-left", scale=20, start=0, duration=2),
            new_studio_layer("intro_text", text="Xin chào Việt Nam", start=0, duration=1),
            new_studio_layer("scroll_text", text="Quyết Bồ IT Studio", start=0, duration=2),
        ],
    })

    rendered = VideoRenderer(runner).render(video, output, settings, {}, subtitle=subtitle)
    info = runner.probe.inspect(rendered.path)
    stream_types = {stream.get("codec_type") for stream in info["streams"]}

    assert stream_types >= {"video", "audio"}
    assert rendered.width == 320 and rendered.height == 240
    assert 1500 <= rendered.duration_ms <= 2100
    assert rendered.path.stat().st_size > 0
    assert not list(tmp_path.glob("*.partial*"))
    assert not list(tmp_path.glob("*.filters.txt"))
