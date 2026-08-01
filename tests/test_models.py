import pytest

from quyetboit_studio.models import ProjectModel, VideoSource, detect_video_source


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.douyin.com/video/123", VideoSource.DOUYIN),
        ("https://v.douyin.com/a", VideoSource.DOUYIN),
        ("https://www.xiaohongshu.com/explore/1", VideoSource.XIAOHONGSHU),
        ("https://m.bilibili.com/video/BV1", VideoSource.BILIBILI),
    ],
)
def test_detect_video_source(url, expected):
    assert detect_video_source(url) == expected


@pytest.mark.parametrize("url", ["", "not-a-url", "ftp://douyin.com/x", "https://evil-douyin.com/x"])
def test_detect_video_source_rejects_invalid(url):
    with pytest.raises(ValueError):
        detect_video_source(url)


def test_project_schema_validation():
    data = ProjectModel().to_dict()
    data["schema_version"] = 99
    with pytest.raises(ValueError, match="schema_version"):
        ProjectModel.from_dict(data)


def test_project_requires_eight_steps():
    data = ProjectModel().to_dict()
    data["step_statuses"] = ["ready"]
    with pytest.raises(ValueError, match="8 bước"):
        ProjectModel.from_dict(data)

