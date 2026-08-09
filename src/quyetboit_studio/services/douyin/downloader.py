from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ...core.models import (
    CancellationToken,
    PipelineError,
    ProgressCallback,
    ProgressEvent,
    VideoArtifact,
)
from ..media import MediaProbe


def validate_douyin_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not (host == "douyin.com" or host.endswith(".douyin.com")):
        raise PipelineError(
            "invalid_douyin_url", "Chỉ chấp nhận URL HTTPS thuộc douyin.com.",
            provider="douyin",
        )
    if parsed.username or parsed.password:
        raise PipelineError("invalid_douyin_url", "URL Douyin không hợp lệ.", provider="douyin")
    modal_id = (parse_qs(parsed.query).get("modal_id") or [""])[0]
    if modal_id.isdigit():
        return f"https://www.douyin.com/video/{modal_id}"
    return url.strip()


@dataclass(slots=True, frozen=True)
class DownloadRequest:
    url: str
    directory: Path
    browser: str = "chrome"
    profile: str = "Default"
    format: str = "bestvideo*+bestaudio/best"
    cookies_file: Path | None = None


@dataclass(slots=True, frozen=True)
class VideoMetadata:
    id: str
    title: str
    duration_seconds: float
    webpage_url: str
    format_id: str = ""
    width: int = 0
    height: int = 0
    filesize: int | None = None


@dataclass(slots=True, frozen=True)
class SeriesEpisode:
    id: str
    title: str
    duration_seconds: float
    webpage_url: str


@dataclass(slots=True, frozen=True)
class SeriesMetadata:
    id: str
    title: str
    episodes: tuple[SeriesEpisode, ...]


class _YdlLogger:
    def debug(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None


class DouyinDownloader:
    def __init__(self, probe: MediaProbe | None = None, ydl_factory: Any = None) -> None:
        self.media_probe = probe or MediaProbe()
        self._ydl_factory = ydl_factory

    def _factory(self):
        if self._ydl_factory:
            return self._ydl_factory
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise PipelineError(
                "yt_dlp_missing", "Chưa cài yt-dlp. Hãy cài dependency của ứng dụng.",
                provider="douyin",
            ) from exc
        return YoutubeDL

    @staticmethod
    def _metadata(info: dict[str, Any]) -> VideoMetadata:
        final = str(info.get("webpage_url") or info.get("original_url") or "")
        validate_douyin_url(final)
        title = re.sub(r"[\x00-\x1f]+", " ", str(info.get("title") or "Không có tiêu đề")).strip()
        return VideoMetadata(
            id=str(info.get("id") or hashlib.sha256(final.encode()).hexdigest()[:16]),
            title=title[:300], duration_seconds=float(info.get("duration") or 0),
            webpage_url=final, format_id=str(info.get("format_id") or ""),
            width=int(info.get("width") or 0), height=int(info.get("height") or 0),
            filesize=info.get("filesize") or info.get("filesize_approx"),
        )

    def probe(
        self, url: str, auth: tuple[str, str] | None = None,
        cookies_file: str | Path | None = None,
    ) -> VideoMetadata:
        url = validate_douyin_url(url)
        options: dict[str, Any] = {"quiet": True, "no_warnings": True, "noplaylist": True, "logger": _YdlLogger()}
        temporary_cookie = self._apply_auth(options, auth, cookies_file)
        try:
            with self._factory()(options) as ydl:
                return self._metadata(ydl.extract_info(url, download=False))
        except PipelineError:
            raise
        except Exception as exc:
            raise self._download_error(exc) from exc
        finally:
            self._remove_temporary_cookie(temporary_cookie)

    def resolve_series(
        self, url: str, auth: tuple[str, str] | None = None,
        cookies_file: str | Path | None = None,
        cancel: CancellationToken | None = None,
    ) -> SeriesMetadata:
        """Resolve a collection URL or one episode into its ordered collection entries."""
        url = validate_douyin_url(url)
        options: dict[str, Any] = {
            "quiet": True, "no_warnings": True, "noplaylist": True, "logger": _YdlLogger(),
        }
        temporary_cookie = self._apply_auth(options, auth, cookies_file)
        try:
            with self._factory()(options) as ydl:
                if cancel:
                    cancel.raise_if_cancelled()
                mix_id = _mix_id_from_url(url)
                detail: dict[str, Any] = {}
                if not mix_id:
                    info = ydl.extract_info(url, download=False)
                    metadata = self._metadata(info)
                    video_id = metadata.id
                    extractor = ydl.get_info_extractor("Douyin")
                    response = extractor._download_json(
                        "https://www.douyin.com/aweme/v1/web/aweme/detail/", video_id,
                        "Đang lấy thông tin series", query={"aweme_id": video_id},
                    )
                    detail = _mapping(response.get("aweme_detail") or response.get("aweme"))
                    mix = _mapping(detail.get("mix_info") or detail.get("series_info"))
                    mix_id = str(
                        mix.get("mix_id") or mix.get("series_id")
                        or detail.get("mix_id") or detail.get("series_id") or ""
                    )
                if not mix_id:
                    raise PipelineError(
                        "douyin_series_not_found",
                        "Video này không thuộc Series/合集 trên Douyin.", provider="douyin",
                    )

                extractor = ydl.get_info_extractor("Douyin")
                episodes: list[SeriesEpisode] = []
                seen: set[str] = set()
                cursor = 0
                title = _series_title(detail)
                received_list_payload = False
                for _page in range(100):
                    if cancel:
                        cancel.raise_if_cancelled()
                    response = extractor._download_json(
                        "https://www.douyin.com/aweme/v1/web/mix/aweme/", mix_id,
                        "Đang lấy danh sách tập",
                        query=_series_query(mix_id, cursor),
                    )
                    raw_items = response.get("aweme_list")
                    if raw_items is None:
                        raw_items = response.get("items")
                    if isinstance(raw_items, list):
                        received_list_payload = True
                        items = raw_items
                    else:
                        items = []
                    for item in items:
                        episode = _series_episode(item)
                        if episode and episode.id not in seen:
                            seen.add(episode.id)
                            episodes.append(episode)
                            if not title:
                                title = _series_title(_mapping(item))
                    if not response.get("has_more"):
                        break
                    next_cursor = int(response.get("cursor") or response.get("max_cursor") or 0)
                    if next_cursor == cursor:
                        break
                    cursor = next_cursor
                if not episodes:
                    if not received_list_payload:
                        raise PipelineError(
                            "douyin_series_list_unavailable",
                            "Douyin không trả danh sách tập cho Series này. Phiên hoặc cơ chế "
                            "chống bot có thể đang chặn request; hãy thử lại sau.",
                            provider="douyin", retryable=True,
                        )
                    raise PipelineError(
                        "douyin_series_empty", "Không tìm thấy tập nào trong Series Douyin này.",
                        provider="douyin", retryable=True,
                    )
                return SeriesMetadata(str(mix_id), title or f"Series {mix_id}", tuple(episodes))
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                "douyin_series_failed",
                "Không thể tải danh sách Series. Hãy kiểm tra cookie hoặc thử lại sau.",
                provider="douyin", retryable=True, debug_detail=str(exc),
            ) from exc
        finally:
            self._remove_temporary_cookie(temporary_cookie)

    def download(
        self, request: DownloadRequest, progress: ProgressCallback | None = None,
        cancel: CancellationToken | None = None,
    ) -> VideoArtifact:
        url = validate_douyin_url(request.url)
        request.directory.mkdir(parents=True, exist_ok=True)

        def hook(data: dict[str, Any]) -> None:
            if cancel:
                cancel.raise_if_cancelled()
            if progress and data.get("status") == "downloading":
                downloaded = int(data.get("downloaded_bytes") or 0)
                total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
                progress(ProgressEvent(
                    0, downloaded / total if total else 0.0, "Đang tải video",
                    current=downloaded, total=total or None, unit="bytes",
                ))

        template = str(request.directory / "%(id)s.%(ext)s")
        options: dict[str, Any] = {
            "format": request.format, "outtmpl": template, "merge_output_format": "mp4",
            "noplaylist": True, "continuedl": True, "quiet": True, "no_warnings": True,
            "logger": _YdlLogger(), "progress_hooks": [hook],
        }
        temporary_cookie = self._apply_auth(
            options, (request.browser, request.profile), request.cookies_file
        )
        try:
            with self._factory()(options) as ydl:
                info = ydl.extract_info(url, download=True)
                metadata = self._metadata(info)
                prepared = Path(ydl.prepare_filename(info))
            candidates = [prepared.with_suffix(".mp4"), prepared]
            path = next((item for item in candidates if item.is_file()), None)
            if path is None:
                raise PipelineError("download_missing", "yt-dlp không tạo file đầu ra.", provider="douyin")
            artifact = self.media_probe.video_artifact(path)
            artifact.metadata.update({"video_id": metadata.id, "title": metadata.title, "format_id": metadata.format_id})
            if progress:
                progress(ProgressEvent(0, 1.0, "Đã tải video"))
            return artifact
        except PipelineError:
            raise
        except Exception as exc:
            for partial in request.directory.glob("*.part"):
                try:
                    partial.unlink()
                except OSError:
                    pass
            raise self._download_error(exc) from exc
        finally:
            self._remove_temporary_cookie(temporary_cookie)

    @staticmethod
    def _download_error(exc: BaseException) -> PipelineError:
        message = str(exc).lower()
        if "unsupported url" in message:
            user = "yt-dlp không hỗ trợ dạng URL Douyin này. Hãy mở trực tiếp video rồi sao chép URL."
            code = "douyin_url_unsupported"
        elif "cookie" in message or "database is locked" in message:
            user = "Không đọc được cookie trình duyệt. Hãy đóng Chrome/Edge hoặc thử profile khác."
            code = "browser_cookie_unavailable"
        else:
            user = "Không thể tải video Douyin. Hãy kiểm tra URL, mạng và phiên đăng nhập trình duyệt."
            code = "douyin_download_failed"
        return PipelineError(code, user, provider="douyin", retryable=True, debug_detail=str(exc))

    @staticmethod
    def _apply_auth(
        options: dict[str, Any], auth: tuple[str, str] | None,
        cookies_file: str | Path | None,
    ) -> Path | None:
        if cookies_file:
            path = Path(cookies_file).expanduser().resolve()
            if not path.is_file():
                raise PipelineError(
                    "cookies_file_missing", "Không tìm thấy file cookie đã chọn.",
                    provider="douyin",
                )
            if path.suffix.lower() == ".json":
                descriptor, name = tempfile.mkstemp(prefix="qbstudio-cookies-", suffix=".txt")
                temporary = Path(name)
                os.close(descriptor)
                try:
                    os.chmod(temporary, 0o600)
                    convert_cookie_json_to_netscape(path, temporary)
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
                options["cookiefile"] = str(temporary)
                return temporary
            options["cookiefile"] = str(path)
            return None
        if auth:
            browser, profile = auth
            options["cookiesfrombrowser"] = (browser, resolve_browser_profile(browser, profile))
        return None

    @staticmethod
    def _remove_temporary_cookie(path: Path | None) -> None:
        if path:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def resolve_browser_profile(
    browser: str, profile: str, local_app_data: str | Path | None = None,
) -> str:
    """Map a Chromium display name to the real directory accepted by yt-dlp."""
    profile = profile.strip() or "Default"
    expanded = Path(profile).expanduser()
    if expanded.is_absolute() and expanded.is_dir():
        return str(expanded)
    roots = {
        "chrome": Path("Google/Chrome/User Data"),
        "edge": Path("Microsoft/Edge/User Data"),
    }
    relative = roots.get(browser.strip().lower())
    if relative is None:
        return profile
    base = Path(local_app_data or os.environ.get("LOCALAPPDATA", "")) / relative
    local_state = base / "Local State"
    try:
        payload = json.loads(local_state.read_text(encoding="utf-8"))
        profiles = payload.get("profile", {}).get("info_cache", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return profile
    wanted = profile.casefold()
    for directory, metadata in profiles.items():
        display_name = str((metadata or {}).get("name") or "")
        if wanted in {str(directory).casefold(), display_name.casefold()}:
            return str(directory)
    return profile


def convert_cookie_json_to_netscape(source: str | Path, destination: str | Path) -> int:
    """Convert common browser-extension JSON exports without exposing cookie values."""
    try:
        payload = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(
            "cookie_json_invalid", "File cookie JSON không đọc được hoặc không hợp lệ.",
            provider="douyin", debug_detail=type(exc).__name__,
        ) from exc
    cookies = payload.get("cookies") if isinstance(payload, dict) else payload
    if not isinstance(cookies, list):
        raise PipelineError(
            "cookie_json_schema", "File JSON không có danh sách cookies được hỗ trợ.",
            provider="douyin",
        )
    lines = ["# Netscape HTTP Cookie File", "# Converted temporarily by QuyetBoIT Studio", ""]
    count = 0
    for item in cookies:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "").strip().lower()
        host = domain.lstrip(".")
        if not (host == "douyin.com" or host.endswith(".douyin.com")):
            continue
        name = _cookie_field(item.get("name"))
        value = _cookie_field(item.get("value"))
        if not name:
            continue
        include_subdomains = not bool(item.get("hostOnly", not domain.startswith(".")))
        path = _cookie_field(item.get("path") or "/") or "/"
        secure = bool(item.get("secure"))
        try:
            expires = int(float(item.get("expirationDate") or item.get("expires") or 0))
        except (TypeError, ValueError):
            expires = 0
        if item.get("httpOnly"):
            domain = f"#HttpOnly_{domain}"
        lines.append(
            "\t".join((domain, "TRUE" if include_subdomains else "FALSE", path,
                        "TRUE" if secure else "FALSE", str(expires), name, value))
        )
        count += 1
    if not count:
        raise PipelineError(
            "cookie_json_empty", "File JSON không chứa cookie thuộc douyin.com.",
            provider="douyin",
        )
    Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return count


def _cookie_field(value: Any) -> str:
    return str(value or "").replace("\t", "").replace("\r", "").replace("\n", "")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mix_id_from_url(url: str) -> str:
    match = re.search(r"/(?:collection|mix)/(\d+)(?:/|$)", urlparse(url).path)
    return match.group(1) if match else ""


def _series_title(value: dict[str, Any]) -> str:
    mix = _mapping(value.get("mix_info") or value.get("series_info"))
    return str(
        mix.get("mix_name") or mix.get("series_name") or mix.get("title")
        or value.get("mix_name") or value.get("series_name") or ""
    ).strip()[:300]


def _series_episode(value: Any) -> SeriesEpisode | None:
    item = _mapping(value)
    episode_id = str(item.get("aweme_id") or item.get("id") or "").strip()
    if not episode_id.isdigit():
        return None
    raw_duration = float(item.get("duration") or _mapping(item.get("video")).get("duration") or 0)
    duration = raw_duration / 1000 if raw_duration > 1000 else raw_duration
    title = re.sub(
        r"[\x00-\x1f]+", " ", str(item.get("desc") or item.get("title") or f"Tập {episode_id}"),
    ).strip()[:300]
    return SeriesEpisode(
        episode_id, title, duration, f"https://www.douyin.com/video/{episode_id}",
    )


def _series_query(mix_id: str, cursor: int) -> dict[str, Any]:
    """Keep volatile Douyin web constants centralized for contract updates."""
    return {
        "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "pc_client_type": "1", "version_code": "290100", "version_name": "29.1.0",
        "cookie_enabled": "true", "browser_platform": "Win32", "platform": "PC",
        "mix_id": mix_id, "cursor": cursor, "count": 50,
    }
