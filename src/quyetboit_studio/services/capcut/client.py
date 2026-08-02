from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import httpx

from ...core.models import AudioArtifact, CancellationToken, PipelineError, SrtCue
from ...core.retry import RetryPolicy, retry_call
from .auth import CapCutSession
from .config import (
    ACCOUNT_INFO,
    AID,
    APPVR_AIGC,
    APPVR_ASSET,
    EDIT_API,
    USER_AGENT,
    VOICE_API_VERSION,
    VOICE_CATEGORIES,
    VOICE_REGION,
)


def make_sign(path: str, appvr: str, now: int | None = None) -> dict[str, str]:
    now = int(time.time()) if now is None else now
    pf, tdid = "7", ""
    message = f"9e2c|{path[-7:]}|{pf}|{appvr}|{now}|{tdid}|11ac"
    return {"sign": hashlib.md5(message.encode()).hexdigest(), "device-time": str(now),
            "tdid": tdid, "sign-ver": "1", "pf": pf, "appvr": appvr}


@dataclass(slots=True, frozen=True)
class Voice:
    name: str
    speaker: str
    platform: int = 1
    effect_id: str = ""
    resource_id: str = ""
    lang: str = "vi"
    category: str = "vietnamese"
    gender: str = ""
    categories: tuple[str, ...] = ()


class CapCutEditClient:
    def __init__(
        self, session: CapCutSession, client: httpx.Client | None = None,
        session_store: Any = None,
    ) -> None:
        self.session = session
        self.session_store = session_store
        self.http = client or httpx.Client(
            timeout=httpx.Timeout(45), follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        for name, value in session.cookies.items():
            self.http.cookies.set(name, value, domain=".capcut.com")

    def _headers(self, path: str, appvr: str, signed: bool = True) -> dict[str, str]:
        headers = {"Accept": "application/json, text/plain, */*", "Content-Type": "application/json",
                   "appid": AID, "did": self.session.did, "store-country-code": "vn",
                   "store-country-code-src": "uid"}
        if signed:
            headers.update(make_sign(path, appvr))
        return headers

    def request(self, method: str, path: str, *, appvr: str = APPVR_AIGC, signed: bool = True, **kwargs: Any) -> dict:
        try:
            extra_headers = kwargs.pop("headers", {})
            headers = {**self._headers(path, appvr, signed), **extra_headers}
            response = self.http.request(method, f"{EDIT_API}{path}", headers=headers, **kwargs)
            if response.status_code in {401, 403}:
                raise PipelineError("capcut_session_expired", "Phiên CapCut đã hết hạn. Hãy đăng nhập lại.", provider="capcut")
            response.raise_for_status()
            payload = response.json()
            code = payload.get("ret", payload.get("code", 0))
            if str(code) not in {"0", "200", "2000", "None"}:
                operation = path.rstrip("/").rsplit("/", 1)[-1]
                raise PipelineError(
                    "capcut_business_error",
                    f"CapCut từ chối yêu cầu {operation} (mã {code}).",
                    provider="capcut", debug_detail=f"operation={operation}; code={code}",
                )
            return payload
        except PipelineError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            raise PipelineError(
                f"capcut_http_{status or 'error'}", "Không thể kết nối CapCut.", provider="capcut",
                retryable=status in {408, 429} or status >= 500 or isinstance(exc, httpx.TimeoutException),
                debug_detail=str(exc),
            ) from exc

    def validate_session(self) -> CapCutSession:
        try:
            response = self.http.get(
                ACCOUNT_INFO, params={"aid": AID, "account_sdk_source": "web", "sdk_version": "2.1.10-tiktok", "language": "en"},
                headers={"Accept": "application/json", "appid": AID, "did": self.session.did, "Referer": "https://www.capcut.com/"},
            )
            response.raise_for_status(); payload = response.json()
            data = payload.get("data") or {}
            uid = str(data.get("user_id_str") or data.get("user_id") or "")
            if not uid:
                raise PipelineError("capcut_session_invalid", "Phiên CapCut không còn hợp lệ.", provider="capcut")
            self.session.uid = uid
            self.session.workspace_id = self.get_workspace()
            if self.session_store is not None:
                self.session_store.save(self.session)
            return self.session
        except PipelineError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise PipelineError("capcut_session_invalid", "Không thể xác thực phiên CapCut.", provider="capcut", debug_detail=str(exc)) from exc

    def ensure_session(self) -> CapCutSession:
        if not self.session.uid or not self.session.workspace_id:
            return self.validate_session()
        return self.session

    def get_workspace(self) -> str:
        payload = self.request("POST", "/cc/v1/workspace/get_user_workspaces", appvr=APPVR_ASSET,
                               json={"cursor": "0", "count": 100, "need_convert_workspace": True})
        data = payload.get("data") or {}
        workspaces = data.get("workspace_list") or data.get("workspaces") or data.get("workspace_infos") or []
        if not workspaces:
            raise PipelineError("capcut_workspace_missing", "Tài khoản CapCut không có workspace.", provider="capcut")
        selected = next((item for item in workspaces if str(item.get("role", "")).lower() == "owner"), workspaces[0])
        workspace = str(selected.get("workspace_id") or selected.get("id") or "")
        if not workspace:
            raise PipelineError("capcut_workspace_schema", "Không đọc được workspace ID từ CapCut.", provider="capcut")
        return workspace

    @staticmethod
    def _voice(item: dict[str, Any], category: str) -> Voice | None:
        common = item.get("common_attr") or item
        extra = common.get("extra") or {}
        if isinstance(extra, str):
            try: extra = json.loads(extra)
            except json.JSONDecodeError: extra = {}
        tone = extra.get("tonetype") or {}
        if isinstance(tone, str):
            try: tone = json.loads(tone)
            except json.JSONDecodeError: tone = {}
        speaker = str(tone.get("voice_type") or tone.get("tts_voice") or common.get("speaker") or "")
        if not speaker:
            return None
        platform_name = str(tone.get("platform") or "").lower()
        platform = 2 if "11labs" in platform_name else 1
        gender = str(extra.get("gender") or "")
        if not gender and category == "female_voice":
            gender = "female"
        elif not gender and category == "male_voice":
            gender = "male"
        languages = {
            "vietnamese": "vi", "english": "en", "zhongwen": "zh",
            "indonesian": "id", "taiyu": "th",
        }
        return Voice(str(common.get("title") or speaker), speaker, platform,
                     str(common.get("effect_id") or ""), str(common.get("third_resource_id_str") or ""),
                     lang=languages.get(category, ""), category=category, gender=gender,
                     categories=(category,))

    def list_voices(self, category: str = "vietnamese") -> list[Voice]:
        path = "/artist/v1/effect/get_resources_by_category_id"
        params = {"aid": AID, "version_name": VOICE_API_VERSION, "version_code": "11.0.0", "sdk_version": VOICE_API_VERSION,
                  "effect_sdk_version": VOICE_API_VERSION, "device_platform": "web", "region": VOICE_REGION, "language": "en",
                  "device_type": "web", "channel": "online"}
        body = {"panel": "tone", "category_id": None, "category_key": category, "panel_source": "heycan",
                "pack_optional": {"need_tag": True, "need_thumb": True, "thumb_opt": '{"is_support_webp":1}',
                                  "image_pack_param": {"icon_limit": {"static_format": "webp", "dynamic_format": "awebp", "width": 100, "height": 100}}},
                "offset": 0, "count": 500}
        payload = self.request("POST", path, signed=False, params=params, json=body)
        voices = [self._voice(item, category) for item in (payload.get("data") or {}).get("effect_item_list", [])]
        unique: dict[str, Voice] = {}
        for voice in voices:
            if voice:
                unique[voice.speaker] = voice
        return list(unique.values())

    @staticmethod
    def _audio_url(payload: dict[str, Any]) -> str:
        data = payload.get("data") or {}
        collections = ("tts_materials", "auditions", "audition_list", "tts_list", "list", "speakers", "results")
        direct = ("audio_url", "url", "play_url", "tts_url")
        nested = ("audio_info", "meta_data", "audio", "tts_material")
        candidates = [data] if isinstance(data, dict) else []
        for collection in collections:
            candidates.extend(data.get(collection) or [])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            for key in direct:
                if item.get(key):
                    return str(item[key])
            for container in nested:
                child = item.get(container) or {}
                for key in direct:
                    if child.get(key):
                        return str(child[key])
        raise PipelineError("capcut_tts_schema", "CapCut không trả URL audio TTS.", provider="capcut")

    def tts_url(self, text: str, voice: Voice, rate: float = 1.0, volume: int = 100) -> str:
        path = "/storyboard/v1/tts/multi_platform"
        payload = self.request("POST", path, json={"texts": [text], "tts_conf": {
            "speaker": voice.speaker, "rate": round(rate, 2), "volume": volume, "name": voice.name,
            "platform": "sami" if voice.platform == 1 else "11labs", "effect_id": voice.effect_id,
            "resource_id": voice.resource_id, "is_clone": False}, "need_url": True})
        return self._audio_url(payload)

    def audition_url(self, voice: Voice, text: str = "Xin chào, đây là giọng đọc thử.") -> str:
        path = "/lv/v2/intelligence/tts/mget_audition"
        payload = self.request("POST", path, json={"speakers": [{
            "speaker": voice.speaker, "platform": voice.platform, "text": text,
            "lan": voice.lang or "vi",
        }]})
        return self._audio_url(payload)


class CapCutCaptionService:
    def __init__(self, client: CapCutEditClient, uploader: Any = None) -> None:
        self.client = client; self.uploader = uploader

    @staticmethod
    def utterances(payload: dict[str, Any]) -> tuple[str, list[SrtCue]]:
        data = payload.get("data") or {}
        status = data.get("status")
        captions = data.get("caption") or []
        utterances = captions[0].get("utterances", []) if captions else []
        cues = [SrtCue(i, int(item.get("start_time", 0)), int(item.get("end_time", 0)), str(item.get("text", "")).strip()).normalized()
                for i, item in enumerate(utterances, 1) if str(item.get("text", "")).strip()]
        if status == 1:
            return "running", []
        if status == 0:
            return ("completed" if cues else "completed-empty"), cues
        return "failed", []

    def transcribe_resource(self, resource_id: str, language: str = "zh-CN", *, cancel: CancellationToken | None = None, timeout: float = 1800) -> list[SrtCue]:
        self.client.ensure_session()
        if language not in {"zh-CN", "vi-VN", "en-US"}:
            raise ValueError("Ngôn ngữ ASR không được hỗ trợ.")
        query = {"aid": AID, "device_platform": "web", "region": "VN", "web_id": self.client.session.did,
                 "babi_param": json.dumps({"scenario": "video_editor", "feature_key": "subtitle_recognition", "feature_entrance": "editor", "feature_entrance_detail": "editor-elements-subtitle-subtitle_recognition"}, separators=(",", ":"))}
        body = {"resources": [{"workspace_id": self.client.session.workspace_id, "resource_id": resource_id,
                               "url_from_type": 1, "language": language, "caption_type": 0,
                               "file_type": 2, "words_per_line": 55}]}
        self.client.request("POST", "/lv/v1/caption/submit", params=query, json=body)
        deadline, delay = time.monotonic() + timeout, 1.5
        while time.monotonic() < deadline:
            if cancel and cancel.wait(delay + random.uniform(0, min(0.3, delay / 10))):
                cancel.raise_if_cancelled()
            elif not cancel:
                time.sleep(delay + random.uniform(0, min(0.3, delay / 10)))
            payload = self.client.request("POST", "/lv/v1/caption/query", params=query, json=body)
            status, cues = self.utterances(payload)
            if status == "completed": return cues
            if status == "completed-empty": return []
            if status == "failed":
                raise PipelineError("capcut_caption_failed", "CapCut ASR thất bại.", provider="capcut")
            delay = min(5.0, delay * 1.25)
        raise PipelineError("capcut_caption_timeout", "CapCut ASR quá thời gian 30 phút.", provider="capcut", retryable=True)


class CapCutTtsService:
    def __init__(
        self, client: CapCutEditClient, retry_policy: RetryPolicy | None = None,
        *, cache_path: str | Path | None = None, categories: tuple[str, ...] = VOICE_CATEGORIES,
        catalog_concurrency: int = 4,
    ) -> None:
        self.client = client
        self.retry_policy = retry_policy or RetryPolicy(max_attempts=3)
        cache_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.cache_path = Path(cache_path) if cache_path else cache_root / "QuyetBoITStudio" / "cache" / "capcut_voices_cache.json"
        self.categories = categories
        self.catalog_concurrency = max(1, min(4, catalog_concurrency))
        self.catalog_fetched_at = 0.0

    def list_voices(self, refresh: bool = False) -> list[Voice]:
        if not refresh:
            cached = self._load_voice_cache()
            if cached is not None:
                return cached

        def fetch(category: str) -> list[Voice]:
            return retry_call(
                lambda: self.client.list_voices(category),
                policy=self.retry_policy,
            )

        by_category: dict[str, list[Voice]] = {}
        with ThreadPoolExecutor(max_workers=self.catalog_concurrency, thread_name_prefix="capcut-voice") as executor:
            futures = {executor.submit(fetch, category): category for category in self.categories}
            for future in as_completed(futures):
                by_category[futures[future]] = future.result()
        by_speaker: dict[str, Voice] = {}
        for category in self.categories:
            for voice in by_category.get(category, []):
                current = by_speaker.get(voice.speaker)
                if current is None:
                    by_speaker[voice.speaker] = voice
                    continue
                categories = tuple(dict.fromkeys((*current.categories, *voice.categories)))
                by_speaker[voice.speaker] = replace(
                    current,
                    lang=current.lang or voice.lang,
                    gender=current.gender or voice.gender,
                    categories=categories,
                )
        voices = sorted(by_speaker.values(), key=lambda item: item.name.casefold())
        self._save_voice_cache(voices)
        return voices

    def _load_voice_cache(self) -> list[Voice] | None:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            valid = (
                payload.get("schema_version") in {1, 2}
                and payload.get("app_version") == VOICE_API_VERSION
                and payload.get("region") == VOICE_REGION
            )
            if not valid:
                return None
            self.catalog_fetched_at = float(
                payload.get("fetched_at", payload.get("created_at", 0))
            )
            return [
                Voice(**{**item, "categories": tuple(item.get("categories") or ())})
                for item in payload.get("voices", [])
                if isinstance(item, dict) and item.get("speaker")
            ]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _save_voice_cache(self, voices: list[Voice]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 2,
            "app_version": VOICE_API_VERSION,
            "region": VOICE_REGION,
            "fetched_at": time.time(),
            "voices": [asdict(voice) for voice in voices],
        }
        self.catalog_fetched_at = payload["fetched_at"]
        fd, temporary = tempfile.mkstemp(prefix=".capcut-voices.", suffix=".tmp", dir=self.cache_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.cache_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def preview_url(self, voice: Voice, *, cancel: CancellationToken | None = None) -> str:
        return retry_call(
            lambda: self.client.audition_url(voice),
            policy=self.retry_policy,
            cancel=cancel,
        )

    def synthesize_batch(
        self,
        requests: list[tuple[str, Voice, Path, float, int]],
        *,
        concurrency: int = 25,
        cancel: CancellationToken | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[AudioArtifact]:
        results: list[AudioArtifact | None] = [None] * len(requests)
        completed = 0

        def execute(index: int) -> tuple[int, AudioArtifact]:
            text, voice, output, rate, volume = requests[index]
            return index, self.synthesize(
                text, voice, output, rate=rate, volume=volume, cancel=cancel
            )

        with ThreadPoolExecutor(
            max_workers=max(1, min(50, concurrency)), thread_name_prefix="capcut-tts"
        ) as executor:
            futures = [executor.submit(execute, index) for index in range(len(requests))]
            for future in as_completed(futures):
                if cancel:
                    cancel.raise_if_cancelled()
                index, artifact = future.result()
                results[index] = artifact
                completed += 1
                if progress:
                    progress(completed, len(requests))
        return [item for item in results if item is not None]

    def synthesize(self, text: str, voice: Voice, output: str | Path, *, rate: float = 1.0, volume: int = 100, cancel: CancellationToken | None = None) -> AudioArtifact:
        def operation() -> bytes:
            url = self.client.tts_url(text, voice, rate, volume)
            try:
                response = self.client.http.get(url)
                response.raise_for_status()
                return response.content
            except httpx.HTTPError as exc:
                status = getattr(exc.response, "status_code", 0) if isinstance(exc, httpx.HTTPStatusError) else 0
                raise PipelineError("capcut_tts_download", "Không tải được audio TTS.", provider="capcut",
                                    retryable=status in {429} or status >= 500 or isinstance(exc, httpx.TimeoutException),
                                    debug_detail=str(exc)) from exc
        audio = retry_call(operation, policy=self.retry_policy, cancel=cancel)
        target = Path(output); target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".partial")
        partial.write_bytes(audio); partial.replace(target)
        return AudioArtifact(target).refresh()
