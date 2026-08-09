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
    assert settings["layers"][-1]["position"] == "top"
    assert "start" not in settings["layers"][-1]
    assert "duration" not in settings["layers"][-1]
    assert "width_percent" not in settings["layers"][2]


def test_each_layer_only_keeps_its_supported_properties():
    settings = normalize_studio_settings({
        "layers": [
            {"kind": "video", "enabled": False, "height": 999},
            {"kind": "voice", "enabled": False, "opacity": 0},
            new_studio_layer("subtitle", height=400, width_percent=25, start=3),
            {
                "kind": "logo", "path": "logo.png", "width_percent": 30,
                "background_color": "#123456", "margin": 31,
            },
            new_studio_layer("scroll_text", position="bottom", background_color="#000000"),
        ],
    })

    video, voice, subtitle, logo, moving = settings["layers"]
    assert video["enabled"] is True and set(video) == {"id", "kind", "name", "enabled"}
    assert voice["enabled"] is True and set(voice) == {"id", "kind", "name", "enabled"}
    assert "height" not in subtitle and "width_percent" not in subtitle and "start" not in subtitle
    assert logo["margin_x"] == 31 and logo["margin_y"] == 31
    assert "width_percent" not in logo and "background_color" not in logo
    assert moving["speed"] == 1.0 and moving["bold"] is True
    assert "position" not in moving and "background_color" not in moving


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
                "logo", path=str(logo), margin_x=30, margin_y=18, height=70,
            ),
            new_studio_layer(
                "intro_text", text="Xin chào: 100%", margin=20,
                font_size=48, background_color="#112233",
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
    assert "drawbox=x=0:y=20:w=iw:h=72" in graph.script
    assert "scale=-1:70" in graph.script
    assert "overlay=x=W-w-30:y=18" in graph.script
    assert "enable=" not in graph.script
    assert "rubberband=pitch=" in graph.script
    assert "atempo=1.1" in graph.script
    assert graph.video_output and graph.audio_output == "aout"


def test_studio_dialog_edits_real_layer_properties(qtbot):
    layer = new_studio_layer("intro_text", text="Cũ")
    dialog = StudioDialog({"layers": ["Video", "Voice", "Subtitle", layer]})
    qtbot.addWidget(dialog)
    dialog.layers.setCurrentRow(3)

    dialog.text.setText("Nội dung mới")
    dialog.text.editingFinished.emit()
    dialog.margin.setValue(36)
    dialog.font_size.setValue(50)
    dialog.position.setCurrentText("bottom")
    dialog.background_color.setText("#123456")
    dialog.background_color.editingFinished.emit()
    settings = dialog.current_settings()

    edited = settings["layers"][3]
    assert edited["kind"] == "intro_text"
    assert edited["text"] == "Nội dung mới"
    assert edited["margin"] == 36
    assert edited["font_size"] == 50
    assert edited["position"] == "bottom"
    assert edited["background_color"] == "#123456"


def test_subtitle_line_edits_are_committed_before_selecting_another_layer(qtbot):
    dialog = StudioDialog({"layers": ["Video", "Voice", "Subtitle"]})
    qtbot.addWidget(dialog)
    dialog.layers.setCurrentRow(2)

    dialog.font_color.setText("#123456")
    dialog.background_color.setText("#654321")
    dialog.layers.setCurrentRow(0)

    subtitle = next(
        layer for layer in dialog.current_settings()["layers"]
        if layer["kind"] == "subtitle"
    )
    assert subtitle["font_color"] == "#123456"
    assert subtitle["background_color"] == "#654321"


def test_studio_apply_flushes_pending_subtitle_line_edits(qtbot):
    dialog = StudioDialog({"layers": ["Video", "Voice", "Subtitle"]})
    qtbot.addWidget(dialog)
    dialog.layers.setCurrentRow(2)
    captured = []
    dialog.settings_applied.connect(captured.append)

    dialog.font_color.setText("#ABCDEF")
    dialog.apply()

    subtitle = next(
        layer for layer in captured[0]["layers"] if layer["kind"] == "subtitle"
    )
    assert subtitle["font_color"] == "#ABCDEF"


def test_scroll_text_controls_disable_without_losing_values(qtbot):
    moving = new_studio_layer("scroll_text", text="Watermark", speed=1.7, bold=False)
    dialog = StudioDialog({"layers": ["Video", "Voice", "Subtitle", moving]})
    qtbot.addWidget(dialog)
    dialog.layers.setCurrentRow(3)

    assert dialog.scroll_speed.value() == 1.7
    dialog.layer_enabled.setChecked(False)
    assert not dialog.text.isEnabled()
    assert not dialog.scroll_speed.isEnabled()
    dialog.layer_enabled.setChecked(True)
    assert dialog.text.isEnabled()
    assert dialog.scroll_speed.isEnabled()
    assert dialog.current_settings()["layers"][3]["speed"] == 1.7


def test_layer_form_only_shows_properties_allowed_for_selected_kind(qtbot):
    logo = new_studio_layer("logo", path="logo.png")
    dialog = StudioDialog({"layers": ["Video", "Voice", "Subtitle", logo]})
    qtbot.addWidget(dialog)

    dialog.layers.setCurrentRow(0)
    assert all(label.isHidden() for label, _ in dialog._layer_rows.values())

    dialog.layers.setCurrentRow(2)
    subtitle_fields = {
        key for key, (label, _) in dialog._layer_rows.items() if not label.isHidden()
    }
    assert subtitle_fields == {
        "enabled", "position", "margin", "font_size", "font_color",
        "background_color", "background_opacity",
    }

    dialog.layers.setCurrentRow(3)
    logo_fields = {
        key for key, (label, _) in dialog._layer_rows.items() if not label.isHidden()
    }
    assert logo_fields == {
        "path", "position", "margin_x", "margin_y", "height", "opacity",
    }


def test_studio_single_line_inputs_have_enough_height_for_values(qtbot):
    dialog = StudioDialog({"layers": ["Video", "Voice", "Subtitle"]})
    dialog.setStyleSheet(LIGHT_STYLESHEET)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)

    controls = (
        dialog.asset_path, dialog.text, dialog.position, dialog.margin,
        dialog.margin_x, dialog.margin_y, dialog.layer_height, dialog.opacity,
        dialog.font_size, dialog.font_color, dialog.background_color,
        dialog.background_opacity, dialog.scroll_speed,
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


def test_subtitle_and_moving_text_follow_layer_rules(tmp_path):
    subtitle = tmp_path / "translated.vi.srt"
    settings = normalize_studio_settings({
        "layers": [
            "Video", "Voice",
            new_studio_layer(
                "subtitle", position="top", margin=12, font_size=40,
                background_opacity=55,
            ),
            new_studio_layer(
                "scroll_text", text="Watermark", font_size=48, opacity=60,
                speed=1.25, bold=True,
            ),
            new_studio_layer("scroll_text", text="   "),
        ],
    })

    graph = FilterGraphBuilder().build(
        settings, subtitle=subtitle, frame_width=640, frame_height=360,
    )

    assert "drawbox=x=0:y=12:w=iw:h=60" in graph.script
    assert "Alignment=8" in graph.script
    assert "FontName=Arial,FontSize=32" in graph.script
    assert "MarginV=17.6" in graph.script
    assert "fontcolor=0xFFFFFF@0.6" in graph.script
    assert "t*150" in graph.script
    assert "t*103.75" in graph.script
    assert "max(1\\,w-text_w)" in graph.script
    assert "max(1\\,h-text_h)" in graph.script
    assert graph.script.count("text='Watermark'") == 1


def test_scroll_text_speed_does_not_override_video_and_audio_speed(tmp_path):
    settings = normalize_studio_settings({
        "speed": 1.1,
        "zoom": 112,
        "layers": [
            "Video", "Voice",
            new_studio_layer("subtitle", position="bottom", margin=0, font_size=28),
            new_studio_layer("scroll_text", text="Watermark", speed=0.7),
        ],
    })

    graph = FilterGraphBuilder().build(settings, subtitle=tmp_path / "translated.vi.srt")

    assert "t*84" in graph.script
    assert "t*58.1" in graph.script
    assert "setpts=PTS/1.1" in graph.script
    assert "atempo=1.1" in graph.script
    assert "setpts=PTS/0.7" not in graph.script
    assert "atempo=0.7" not in graph.script
    assert graph.script.index("scale=iw*1.12") < graph.script.index("subtitles=")
    assert graph.script.index("subtitles=") < graph.script.index("setpts=PTS/1.1")


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
