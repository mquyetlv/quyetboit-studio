import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from quyetboit_studio.core.football import FootballHighlightInput, FootballRenderConfig
from quyetboit_studio.core.models import CancellationToken, PipelineError
from quyetboit_studio.pipeline.football import FootballHighlightPipeline
from quyetboit_studio.services.media.ffmpeg import FFmpegRunner
from quyetboit_studio.services.youtube import YouTubeDownload


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg/ffprobe không có trong PATH"
)
def test_football_pipeline_renders_playable_video_with_smooth_frame_fade(tmp_path: Path):
    runner = FFmpegRunner()
    source = tmp_path / "source.mp4"; speech = tmp_path / "speech.wav"
    runner.run(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=2", "-c:v", "libx264", str(source)])
    runner.run(["-f", "lavfi", "-i", "sine=frequency=550:duration=1.5:sample_rate=48000", "-c:a", "pcm_s16le", str(speech)])

    thumbnail = tmp_path / "cover.jpg"; Image.new("RGB", (320, 180), "blue").save(thumbnail)
    class Downloader:
        def download(self, _url, _directory, *, cancel=None):
            return YouTubeDownload("Fixture match", runner.probe.video_artifact(source), thumbnail)

    class Speech:
        def synthesize(self, _text, output, *, cancel=None):
            output.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(speech, output); return output

    content = FootballHighlightInput.from_json(
        "https://youtu.be/fixture",
        '''[
          {"match_minute":"01:10","situation_type":"Bàn thắng","frames":{"setup":"00:00","finish":"00:01"},"content":"Manchester United ghi bàn đẹp mắt.","tts_text":"Man-chét-tơ Yu-nai-tịt ghi bàn đẹp mắt."},
          {"match_minute":"02:10","situation_type":"Cơ hội","frames":{"setup":"00:01","finish":"00:00"},"content":"Bruno Fernandes tạo một cơ hội khác.","tts_text":"Bru-nô Phéc-nan-đét tạo một cơ hội khác."}
        ]''',
    )
    config = FootballRenderConfig(1.00, 1.15, "Bàn thắng", 48, 24, "#FFFFFF", "#000000", 60, "bottom", 44, 56, "#FFFFFF", "#000000", 60)
    pipeline = FootballHighlightPipeline(content, output_folder=tmp_path / "output", workspace=tmp_path / "work", speech_factory=Speech, runner=runner, downloader=Downloader(), burn_subtitles=True, render_config=config)
    assert "d=1:s=3840x2160:fps=30,scale=1920:1080:flags=lanczos" in pipeline._ken_burns_filter(0, 1.5)
    assert "1.00000000+0.15000000*on/44" in pipeline._ken_burns_filter(0, 1.5)
    result = pipeline.run()

    artifact = runner.probe.video_artifact(result.output)
    assert result.segments == 2
    assert result.output.parent.name == "Fixture match"
    assert (result.output.parent / "thumbnail.jpg").is_file()
    rendered_ass = (tmp_path / "work" / "subtitles" / "segment-001.ass").read_text(encoding="utf-8-sig")
    assert "Manchester United" in rendered_ass
    assert "Man-chét-tơ" not in rendered_ass
    assert (artifact.width, artifact.height) == (1920, 1080)
    assert artifact.duration_ms > 1_000
    stream = next(item for item in runner.probe.inspect(result.output)["streams"] if item["codec_type"] == "video")
    assert stream["pix_fmt"] == "yuv420p"
    assert stream["profile"] == "High"
    first, later = tmp_path / "zoom-first.jpg", tmp_path / "zoom-later.jpg"
    runner.run(["-ss", "0.10", "-i", str(result.output), "-frames:v", "1", str(first)])
    runner.run(["-ss", "0.55", "-i", str(result.output), "-frames:v", "1", str(later)])
    with Image.open(first) as image, Image.open(later) as later_image:
        difference = np.abs(np.asarray(image.convert("RGB"), dtype=np.int16) - np.asarray(later_image.convert("RGB"), dtype=np.int16))
    assert difference.mean() > 2
    assert not list((tmp_path / "work").rglob("*.partial.*"))


def test_football_pipeline_stops_before_any_external_work(tmp_path: Path):
    cancel = CancellationToken(); cancel.cancel()
    content = FootballHighlightInput.from_json("https://youtu.be/fixture", FootballHighlightInput.example_json())
    pipeline = FootballHighlightPipeline(content, output_folder=tmp_path / "output", workspace=tmp_path / "work", speech_factory=lambda: None, cancel=cancel)  # type: ignore[arg-type]
    with pytest.raises(PipelineError, match="hủy"):
        pipeline.run()
    assert not (tmp_path / "output").exists()


def test_football_tts_retries_only_temporary_provider_errors(tmp_path: Path):
    content = FootballHighlightInput.from_json("https://youtu.be/fixture", FootballHighlightInput.example_json())
    pipeline = FootballHighlightPipeline(content, output_folder=tmp_path / "output", workspace=tmp_path / "work", speech_factory=lambda: None)  # type: ignore[arg-type]

    class RetrySpeech:
        calls = 0
        def synthesize(self, _text, output, *, cancel=None):
            self.calls += 1
            if self.calls < 3:
                raise PipelineError("temporary", "Lỗi tạm thời", retryable=True)
            output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(b"audio")
            return output

    speech = RetrySpeech(); target = tmp_path / "voice.wav"
    pipeline._synthesize_with_retry(speech, "test", target)
    assert speech.calls == 3
    assert target.is_file()


def test_football_ass_uses_timed_single_line_dialogues(tmp_path: Path):
    content = FootballHighlightInput.from_json("https://youtu.be/fixture", FootballHighlightInput.example_json())
    pipeline = FootballHighlightPipeline(content, output_folder=tmp_path / "output", workspace=tmp_path / "work", speech_factory=lambda: None)  # type: ignore[arg-type]
    ass = pipeline._write_ass(
        "Manchester United mở tỷ số. Bruno Fernandes thực hiện đường kiến tạo.",
        1,
        5_000,
        timing_text="Man-chét-tơ Yu-nai-tịt mở tỷ số. Bru-nô Phéc-nan-đét thực hiện đường kiến tạo.",
    )
    dialogues = [line for line in ass.read_text(encoding="utf-8-sig").splitlines() if line.startswith("Dialogue:")]
    assert len(dialogues) >= 2
    assert all("\\N" not in line for line in dialogues)
    assert all(len(line.rsplit("}", 1)[-1]) <= 42 for line in dialogues)
    assert dialogues[0].split(",")[1] == "0:00:00.00"
    assert r"{\an5\pos(960,991)}" in dialogues[0]
    rendered_text = "\n".join(dialogues)
    assert "Manchester United" in rendered_text
    assert "Bruno Fernandes" in rendered_text
    assert "Man-chét-tơ" not in rendered_text
    assert "Bru-nô" not in rendered_text
    style = next(line for line in ass.read_text(encoding="utf-8-sig").splitlines() if line.startswith("Style: Default,"))
    assert ",-0.5," in style
