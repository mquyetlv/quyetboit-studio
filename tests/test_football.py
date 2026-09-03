import pytest

from quyetboit_studio.core.football import FootballHighlightInput, FootballRenderConfig


def test_football_json_accepts_frames_and_preserves_order():
    content = FootballHighlightInput.from_json(
        "https://www.youtube.com/watch?v=test",
        '''[{"match_minute":"12:34","situation_type":"Bàn thắng","frames":{"setup":"12:28","finish":["12:32","12:34"]},"content":"Manchester United ghi bàn.","tts_text":"Man-chét-tơ Yu-nai-tịt ghi bàn."}]''',
    )
    assert content.segments[0].timestamps() == ["12:28", "12:32", "12:34"]
    assert content.segments[0].content == "Manchester United ghi bàn."


def test_football_json_requires_non_empty_content():
    value = FootballHighlightInput.example_json().replace(
        '"content": "Một tình huống phối hợp đẹp mắt của Manchester United và bàn thắng được ghi.",\n',
        "",
    )
    with pytest.raises(ValueError, match="content"):
        FootballHighlightInput.from_json("https://youtu.be/fixture", value)


@pytest.mark.parametrize("url", ["http://youtube.com/watch?v=x", "https://example.com/watch?v=x"])
def test_football_json_rejects_non_https_youtube(url):
    with pytest.raises(ValueError, match="YouTube"):
        FootballHighlightInput.from_json(url, FootballHighlightInput.example_json())


def test_football_ken_burns_config_defaults_to_requested_smooth_range():
    config = FootballRenderConfig()
    assert (config.ken_burns_start_zoom, config.ken_burns_end_zoom) == (1.00, 1.15)
    with pytest.raises(ValueError, match="Zoom Ken Burns"):
        FootballRenderConfig(1.15, 1.00)
    with pytest.raises(ValueError, match="Vị trí phụ đề"):
        FootballRenderConfig(subtitle_position="center")
