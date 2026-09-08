"""Isolate blocking provider calls so Stop can terminate their owned process tree."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from ..core.models import CancellationToken, PipelineError, ProgressCallback, ProgressEvent


def run_provider(
    payload: dict, cancel: CancellationToken | None = None, progress: ProgressCallback | None = None
) -> dict:
    if cancel:
        cancel.raise_if_cancelled()
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(Path(p).resolve()) for p in sys.path if p)
    process = subprocess.Popen(
        [sys.executable, "-m", "quyetboit_studio.services.football_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        start_new_session=os.name != "nt",
    )
    finished = threading.Event()

    def terminate_tree() -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def watch() -> None:
        while not finished.wait(0.1):
            if cancel and cancel.is_cancelled:
                terminate_tree()
                return

    watcher = threading.Thread(target=watch, name="football-provider-cancel", daemon=True)
    watcher.start()
    result = None
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(payload, ensure_ascii=False))
        process.stdin.close()
        for line in process.stdout:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if "progress" in item:
                if progress:
                    progress(ProgressEvent(0, float(item["progress"]), "Đang tải video YouTube"))
            else:
                result = item
        process.wait()
        if cancel:
            cancel.raise_if_cancelled()
        if result and "error" in result:
            raise PipelineError(
                result["error"], result["message"], retryable=result.get("retryable", False)
            )
        if process.returncode or not result:
            raise PipelineError(
                "football_provider_failed",
                "Dịch vụ không hoàn tất. Hãy kiểm tra kết nối và thử lại.",
            )
        return result
    except OSError as exc:
        if cancel:
            cancel.raise_if_cancelled()
        raise PipelineError(
            "football_provider_io", "Không thể trao đổi với worker. Hãy thử chạy lại."
        ) from exc
    finally:
        finished.set()
        terminate_tree()
        process.wait()
        watcher.join(timeout=5)
        if process.stdout:
            process.stdout.close()
        if process.stdin and not process.stdin.closed:
            process.stdin.close()


def main() -> None:
    # Only whitelisted operations. Credentials are loaded inside the child from
    # their existing secure stores. Only an optional filename travels through IPC;
    # credential contents never travel through JSON or command lines.
    from ..persistence import redact_text

    try:
        payload = json.load(sys.stdin)
        if payload["operation"] == "download":
            from .youtube.downloader import YouTubeDownloader

            def progress(event: ProgressEvent) -> None:
                print(json.dumps({"progress": event.fraction}), flush=True)

            value = YouTubeDownloader()._download(
                payload["url"], Path(payload["directory"]), progress=progress
            )
            result = {
                "title": redact_text(value.title),
                "source": str(value.source.path),
                "thumbnail": str(value.thumbnail) if value.thumbnail else None,
            }
        elif payload["operation"] == "speech":
            from .minigame_tts import CapCutEnglishSpeechService, GoogleCloudSpeechService

            config = payload["config"]
            if config["provider"] == "google":
                speech = GoogleCloudSpeechService(
                    Path(
                        payload.get("service_account_path") or Path.cwd() / "services-account.json"
                    ),
                    config["voice"],
                    language_code=config["language"],
                    volume=config["volume"],
                    speaking_rate=config["rate"],
                )
            else:
                speech = CapCutEnglishSpeechService(
                    config["voice"],
                    volume=config["volume"],
                    rate=config["rate"],
                    categories=("vietnamese",),
                )
            speech.synthesize(payload["text"], Path(payload["output"]))
            result = {"ok": True}
        else:
            raise ValueError("Unsupported operation")
    except PipelineError as exc:
        result = {
            "error": exc.error_code,
            "message": redact_text(exc.user_message),
            "retryable": exc.retryable,
        }
    except Exception:  # noqa: BLE001 - never expose provider diagnostics across IPC
        result = {
            "error": "football_provider_failed",
            "message": "Dịch vụ không hoàn tất. Hãy kiểm tra kết nối và cấu hình.",
        }
    print(json.dumps(result, ensure_ascii=False), flush=True)


class FootballSpeechAdapter:
    def __init__(self, config: dict, service_account_path: str = "") -> None:
        self.config = dict(config)
        self.service_account_path = service_account_path

    def synthesize(
        self, text: str, output: Path, *, cancel: CancellationToken | None = None
    ) -> Path:
        run_provider(
            {
                "operation": "speech",
                "config": self.config,
                "text": text,
                "output": str(output.resolve()),
                "service_account_path": self.service_account_path,
            },
            cancel,
        )
        return output


if __name__ == "__main__":
    main()
