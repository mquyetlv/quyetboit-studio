"""Isolate blocking provider calls so Stop can terminate their owned process tree."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path

from ..core.models import CancellationToken, PipelineError, ProgressCallback, ProgressEvent
from ..persistence import redact_text


def _safe_stderr_detail(lines: list[str]) -> str:
    """Summarize child stderr without retaining provider responses or credentials."""
    value = "\n".join(lines)[-4_096:]
    missing = re.search(r"(?:ModuleNotFoundError|ImportError): No module named ['\"]([^'\"]+)", value)
    if missing:
        module = re.sub(r"[^A-Za-z0-9_.-]", "", missing.group(1))[:120]
        return f"missing_module={module}" if module else "missing_module"
    if "DLL load failed" in value or "cannot open shared object file" in value:
        return "native_dependency_load_failed"
    if "FileNotFoundError" in value:
        return "missing_file_or_executable"
    return "stderr_present=true" if value else "stderr_present=false"


def run_provider(
    payload: dict, cancel: CancellationToken | None = None, progress: ProgressCallback | None = None
) -> dict:
    if cancel:
        cancel.raise_if_cancelled()
    env = dict(os.environ)
    # Do not rely on the GUI launcher's current directory or sys.path.  The
    # child imports this module from <repo>/src/quyetboit_studio/services, so
    # its grandparent is the import root needed by ``python -m``.
    source_root = Path(__file__).resolve().parents[2]
    inherited_paths = [str(Path(p).resolve()) for p in sys.path if p]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([str(source_root), *inherited_paths]))
    process = subprocess.Popen(
        [sys.executable, "-m", "quyetboit_studio.services.football_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        start_new_session=os.name != "nt",
    )
    finished = threading.Event()
    stderr_lines: list[str] = []

    def drain_stderr() -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            # Keep a bounded in-memory tail only.  Raw stderr is never shown
            # or persisted because it can contain provider URLs/tokens.
            stderr_lines.append(line)
            if len(stderr_lines) > 32:
                del stderr_lines[:-32]

    stderr_reader = threading.Thread(
        target=drain_stderr, name="football-provider-stderr", daemon=True
    )
    stderr_reader.start()

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
        stderr_reader.join(timeout=5)
        if cancel:
            cancel.raise_if_cancelled()
        if result and "error" in result:
            raise PipelineError(
                result["error"], result["message"], retryable=result.get("retryable", False),
                debug_detail=result.get("detail", ""), log_detail=result.get("detail", ""),
            )
        if process.returncode or not result:
            detail = f"exit_code={process.returncode}; {_safe_stderr_detail(stderr_lines)}"
            raise PipelineError(
                "football_provider_process_failed",
                "Tiến trình tải nguồn hoặc tạo voice đã dừng bất thường. Xem nhật ký an toàn bên dưới.",
                debug_detail=detail,
                log_detail=detail,
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
        if process.stderr:
            process.stderr.close()
        stderr_reader.join(timeout=5)
        if process.stdin and not process.stdin.closed:
            process.stdin.close()


def main() -> None:
    # Only whitelisted operations. Credentials are loaded inside the child from
    # their existing secure stores. Only an optional filename travels through IPC;
    # credential contents never travel through JSON or command lines.
    # A Windows process started outside an UTF-8 terminal otherwise inherits
    # cp1252.  Both IPC directions carry Vietnamese title/TTS text.
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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
            "detail": redact_text(exc.log_detail or f"code={exc.error_code}"),
        }
    except Exception:  # noqa: BLE001 - never expose provider diagnostics across IPC
        result = {
            "error": "football_provider_failed",
            "message": "Dịch vụ không hoàn tất. Hãy kiểm tra kết nối và cấu hình.",
            "detail": "unexpected_provider_exception",
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
