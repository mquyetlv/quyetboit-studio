from datetime import UTC, datetime

import pytest

from quyetboit_studio.core import Artifact, ArtifactCache, PipelineError, SrtCue
from quyetboit_studio.models import ProjectModel
from quyetboit_studio.services.capcut import (
    AwsV4Signer,
    StsCredentials,
    crc32_hex,
    make_sign,
    xor5_hex,
)
from quyetboit_studio.services.douyin import (
    convert_cookie_json_to_netscape,
    resolve_browser_profile,
    validate_douyin_url,
)
from quyetboit_studio.services.media import AudioProcessor, atempo_chain
from quyetboit_studio.services.media.ffmpeg import SplitConfig
from quyetboit_studio.services.subtitles import (
    find_untranslated,
    merge_chunk_cues,
    parse_srt,
    serialize_srt,
)


def test_douyin_url_is_https_and_domain_bounded():
    assert validate_douyin_url("https://v.douyin.com/abc") == "https://v.douyin.com/abc"
    for invalid in ("http://douyin.com/x", "https://evil-douyin.com/x", "https://douyin.com@evil.test/x"):
        with pytest.raises(PipelineError):
            validate_douyin_url(invalid)
    assert validate_douyin_url(
        "https://www.douyin.com/jingxuan?modal_id=7660401665056558388"
    ) == "https://www.douyin.com/video/7660401665056558388"


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
        '{"domain":"evil.test","name":"other","value":"leak"}]}' ,
        encoding="utf-8",
    )
    output = tmp_path / "cookies.txt"
    assert convert_cookie_json_to_netscape(source, output) == 1
    converted = output.read_text(encoding="utf-8")
    assert "#HttpOnly_.douyin.com" in converted
    assert "sessionid\tsecret" in converted
    assert "evil.test" not in converted


def test_srt_roundtrip_merge_overlap_and_validation():
    cues = parse_srt("\ufeff1\n00:00:00,000 --> 00:00:01,000\n你好\n")
    assert serialize_srt(cues, bom=True).startswith("\ufeff1")
    merged = merge_chunk_cues([
        (0, cues),
        (500, [SrtCue(1, 0, 600, "你好")]),
        (1000, [SrtCue(1, 0, 500, "不同")]),
    ])
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
    first = tmp_path / "first.txt"; first.write_text("first", encoding="utf-8")
    second = tmp_path / "second.txt"; second.write_text("second", encoding="utf-8")
    cache.put(1, Artifact(first, input_hash="a", config_hash="b"))
    cache.put(2, Artifact(second, input_hash="c", config_hash="d"))
    assert cache.get(1, "a", "b") is not None
    first.write_text("tampered", encoding="utf-8")
    assert cache.get(1, "a", "b") is None
    assert cache.invalidate(2) == [second.resolve()]


def test_capcut_sign_crc_obfuscation_and_sigv4_are_deterministic():
    assert xor5_hex("A") == "44"
    assert crc32_hex(b"abc") == "352441c2"
    assert make_sign("/lv/v1/caption/query", "8.4.0", 1)["sign"] == make_sign(
        "/lv/v1/caption/query", "8.4.0", 1
    )["sign"]
    signer = AwsV4Signer()
    headers = signer.sign(
        "GET", "https://example.com/?Version=1&Action=Apply", StsCredentials("AK", "SK", "TOKEN"),
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
