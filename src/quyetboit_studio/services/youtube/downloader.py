from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ...core.football import FootballHighlightInput
from ...core.models import (
    CancellationToken,
    PipelineError,
    ProgressCallback,
    ProgressEvent,
    VideoArtifact,
    stable_hash,
)
from ...persistence import redact_text
from ..football_worker import run_provider
from ..media.ffmpeg import FFmpegRunner
from ..media.football_cache import cache_record, cache_valid


@dataclass(frozen=True, slots=True)
class YouTubeDownload:
    title: str
    source: VideoArtifact
    thumbnail: Path | None = None


class YouTubeDownloader:
    """yt-dlp Python API wrapper; never parses the CLI's stdout."""

    def __init__(self, runner: FFmpegRunner | None = None) -> None:
        self.runner = runner or FFmpegRunner()

    def download(
        self,
        url: str,
        directory: Path,
        *,
        cancel: CancellationToken | None = None,
        progress: ProgressCallback | None = None,
    ) -> YouTubeDownload:
        url = FootballHighlightInput.validate_youtube_url(url)
        if cancel:
            cancel.raise_if_cancelled()
        key = stable_hash({"url": url, "format": "1080p", "version": 3})
        root = directory.resolve() / key[:24]
        root.mkdir(parents=True, exist_ok=True)
        metadata = root / "metadata.json"
        try:
            info = json.loads(metadata.read_text(encoding="utf-8"))
            source = root / Path(info["source"]).name
            if cache_valid(source, key, cancel):
                thumb = root / Path(info["thumbnail"]).name if info.get("thumbnail") else None
                if thumb and not cache_valid(thumb, key, cancel):
                    thumb = None
                return YouTubeDownload(
                    info["title"], self.runner.probe.video_artifact(source), thumb
                )
        except (OSError, ValueError, KeyError, TypeError):
            pass
        # TemporaryDirectory is created under this resolved cache root; cleanup
        # is confined to this invocation's staging directory, never user output.
        with tempfile.TemporaryDirectory(prefix="download-", dir=root) as staging:
            staging_path = Path(staging).resolve()
            if not staging_path.is_relative_to(root):
                raise ValueError("Invalid download workspace")
            value = run_provider(
                {"operation": "download", "url": url, "directory": staging}, cancel, progress
            )
            downloaded = Path(value["source"]).resolve()
            if not downloaded.is_relative_to(staging_path):
                raise PipelineError("youtube_invalid_path", "Đường dẫn video tải về không hợp lệ.")
            artifact = self.runner.probe.video_artifact(downloaded)
            source = root / f"source{artifact.path.suffix}"
            os.replace(downloaded, source)
            cache_record(source, key, cancel)
            thumb = None
            if value.get("thumbnail"):
                candidate = Path(value["thumbnail"]).resolve()
                if candidate.is_relative_to(staging_path) and candidate.is_file():
                    thumb = root / f"thumbnail{candidate.suffix}"
                    os.replace(candidate, thumb)
                    cache_record(thumb, key, cancel)
            partial = metadata.with_suffix(".partial")
            try:
                partial.write_text(
                    json.dumps(
                        {
                            "source": source.name,
                            "title": redact_text(value["title"]),
                            "thumbnail": thumb.name if thumb else None,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                os.replace(partial, metadata)
            finally:
                partial.unlink(missing_ok=True)
            return YouTubeDownload(
                redact_text(value["title"]), self.runner.probe.video_artifact(source), thumb
            )

    def _download(
        self,
        url: str,
        directory: Path,
        *,
        cancel: CancellationToken | None = None,
        progress: ProgressCallback | None = None,
    ) -> YouTubeDownload:
        if cancel:
            cancel.raise_if_cancelled()
        try:
            import yt_dlp
        except ImportError as exc:
            raise PipelineError("ytdlp_missing", "Chưa cài yt-dlp để tải video YouTube.") from exc
        FootballHighlightInput.validate_youtube_url(url)
        directory.mkdir(parents=True, exist_ok=True)

        class QuietLogger:
            def debug(self, _message):
                pass

            def warning(self, _message):
                pass

            def error(self, _message):
                pass

        def hook(data):
            if cancel:
                cancel.raise_if_cancelled()
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            if progress and total:
                progress(
                    ProgressEvent(
                        0, min(0.99, data.get("downloaded_bytes", 0) / total), "Đang tải YouTube"
                    )
                )

        # HLS avoids a common mid-download 403 from YouTube's direct media URLs.
        attempts = (
            ("android", "best[height<=1080]/best"),
            ("web_safari", "best[protocol^=m3u8][height<=1080]/best[protocol^=m3u8]"),
            ("web", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"),
        )
        last_error: Exception | None = None
        for attempt, (player_client, format_selector) in enumerate(attempts, 1):
            if cancel:
                cancel.raise_if_cancelled()
            options = {
                "socket_timeout": 10,
                "retries": 2,
                "fragment_retries": 2,
                "logger": QuietLogger(),
                "progress_hooks": [hook],
                "postprocessor_hooks": [hook],
                "format": format_selector,
                "outtmpl": str(directory / f"source-{attempt}.%(ext)s"),
                "noplaylist": True,
                "merge_output_format": "mp4",
                "quiet": True,
                "no_warnings": True,
                "writethumbnail": True,
                "extractor_args": {"youtube": {"player_client": [player_client]}},
            }
            try:
                with yt_dlp.YoutubeDL(options) as client:
                    info = client.extract_info(url, download=False)
                    if cancel:
                        cancel.raise_if_cancelled()
                    try:
                        FootballHighlightInput.validate_youtube_url(
                            str(info.get("webpage_url") or url)
                        )
                    except ValueError as exc:
                        raise PipelineError(
                            "youtube_invalid_redirect",
                            "URL cuối không còn là YouTube HTTPS. Hãy kiểm tra liên kết nguồn.",
                        ) from exc
                    info = client.process_ie_result(info, download=True)
                    source = Path(client.prepare_filename(info))
                    mp4 = source.with_suffix(".mp4")
                    if mp4.is_file():
                        source = mp4
                    thumbnail = self._find_thumbnail(directory, source)
                    return YouTubeDownload(
                        str(info.get("title") or "football_highlight"),
                        self.runner.probe.video_artifact(source),
                        thumbnail,
                    )
            except PipelineError:
                raise
            except Exception as exc:  # noqa: BLE001 - yt-dlp exposes several download exception types.
                # Try the non-HLS fallback only after a failed HLS download.
                if cancel:
                    cancel.raise_if_cancelled()
                last_error = exc
        raise PipelineError(
            "youtube_download_failed",
            "Không thể tải video YouTube. Hãy thử lại sau hoặc dùng cookie YouTube hợp lệ.",
            provider="youtube",
            debug_detail=type(last_error).__name__ if last_error else "unknown",
        ) from last_error

    @staticmethod
    def _find_thumbnail(directory: Path, source: Path) -> Path | None:
        """yt-dlp writes a thumbnail beside the source; accept image formats only."""
        candidates = (
            sorted(directory.glob(f"{source.stem}.*"))
            + sorted(directory.glob("*.jpg"))
            + sorted(directory.glob("*.webp"))
        )
        return next(
            (
                path
                for path in candidates
                if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and path != source
            ),
            None,
        )
