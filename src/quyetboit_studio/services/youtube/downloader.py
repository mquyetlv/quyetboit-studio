from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...core.models import CancellationToken, PipelineError, VideoArtifact
from ..media.ffmpeg import FFmpegRunner


@dataclass(frozen=True, slots=True)
class YouTubeDownload:
    title: str
    source: VideoArtifact
    thumbnail: Path | None = None


class YouTubeDownloader:
    """yt-dlp Python API wrapper; never parses the CLI's stdout."""

    def __init__(self, runner: FFmpegRunner | None = None) -> None:
        self.runner = runner or FFmpegRunner()

    def download(self, url: str, directory: Path, *, cancel: CancellationToken | None = None) -> YouTubeDownload:
        if cancel:
            cancel.raise_if_cancelled()
        try:
            import yt_dlp
        except ImportError as exc:
            raise PipelineError("ytdlp_missing", "Chưa cài yt-dlp để tải video YouTube.") from exc
        directory.mkdir(parents=True, exist_ok=True)
        # HLS avoids a common mid-download 403 from YouTube's direct media URLs.
        attempts = (
            ("android", "best[height<=1080]/best"),
            ("web_safari", "best[protocol^=m3u8][height<=1080]/best[protocol^=m3u8]"),
            ("web", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"),
        )
        last_error: Exception | None = None
        for attempt, (player_client, format_selector) in enumerate(attempts, 1):
            options = {
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
                    info = client.extract_info(url, download=True)
                    if cancel:
                        cancel.raise_if_cancelled()
                    source = Path(client.prepare_filename(info))
                    mp4 = source.with_suffix(".mp4")
                    if mp4.is_file():
                        source = mp4
                    thumbnail = self._find_thumbnail(directory, source)
                    return YouTubeDownload(str(info.get("title") or "football_highlight"), self.runner.probe.video_artifact(source), thumbnail)
            except PipelineError:
                raise
            except Exception as exc:  # noqa: BLE001 - yt-dlp exposes several download exception types.
                # Try the non-HLS fallback only after a failed HLS download.
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
        candidates = sorted(directory.glob(f"{source.stem}.*")) + sorted(directory.glob("*.jpg")) + sorted(directory.glob("*.webp"))
        return next((path for path in candidates if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and path != source), None)
