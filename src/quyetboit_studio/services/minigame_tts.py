from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..core.models import CancellationToken, PipelineError
from .capcut import CapCutEditClient, CapCutSessionStore, CapCutTtsService, Voice


@dataclass(slots=True, frozen=True)
class CatalogVoice:
    provider: str
    voice_id: str
    name: str
    language_codes: tuple[str, ...]
    gender: str
    family: str
    categories: tuple[str, ...] = ()
    sample_rate_hz: int = 0

    @property
    def primary_language(self) -> str:
        return self.language_codes[0] if self.language_codes else ""


class VoiceCatalogService(Protocol):
    def list_voices(self, refresh: bool = False) -> list[CatalogVoice]: ...
    def preview(self, voice: CatalogVoice, text: str | None = None) -> str | Path: ...


def google_voice_family(name: str) -> str:
    normalized = name.casefold()
    chirp3_names = {
        "achernar", "achird", "algenib", "algieba", "alnilam", "aoede", "autonoe",
        "callirrhoe", "charon", "despina", "enceladus", "erinome", "fenrir", "gacrux",
        "iapetus", "kore", "laomedeia", "leda", "orus", "puck", "pulcherrima",
        "rasalgethi", "sadachbia", "sadaltager", "schedar", "sulafat", "umbriel",
        "vindemiatrix", "zephyr", "zubenelgenubi",
    }
    if normalized in chirp3_names:
        return "Chirp 3 HD"
    families = (
        ("chirp3-hd", "Chirp 3 HD"),
        ("chirp-hd", "Chirp HD"),
        ("studio-multispeaker", "Studio Multispeaker"),
        ("studio", "Studio"),
        ("neural2", "Neural2"),
        ("wavenet", "WaveNet"),
        ("standard", "Standard"),
        ("journey", "Journey"),
        ("casual", "Casual"),
        ("news", "News"),
        ("polyglot", "Polyglot"),
    )
    return next((label for token, label in families if token in normalized), "Other")


class GoogleVoiceCatalogService:
    CACHE_SCHEMA_VERSION = 1

    def __init__(
        self,
        service_account_path: str | Path,
        *,
        cache_path: str | Path | None = None,
    ) -> None:
        self.service_account_path = Path(service_account_path).expanduser().resolve()
        cache_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.cache_path = (
            Path(cache_path).expanduser()
            if cache_path is not None
            else cache_root / "QuyetBoITStudio" / "cache" / "google-voices-v1.json"
        )

    def _module_and_client(self):
        if not self.service_account_path.is_file():
            raise PipelineError(
                "google_service_account_missing",
                "Không tìm thấy service-account JSON của Google TTS.",
                provider="google_tts",
            )
        try:
            from google.cloud import texttospeech
        except ImportError as exc:
            raise PipelineError(
                "google_tts_missing",
                "Chưa cài Google Cloud Text-to-Speech. Hãy cài lại dependencies của ứng dụng.",
                provider="google_tts",
            ) from exc
        try:
            client = texttospeech.TextToSpeechClient.from_service_account_file(
                str(self.service_account_path)
            )
            return texttospeech, client
        except Exception as exc:
            raise PipelineError(
                "google_tts_auth_failed",
                "Không thể xác thực service account Google TTS.",
                provider="google_tts",
                debug_detail=type(exc).__name__,
            ) from exc

    def list_voices(self, refresh: bool = False) -> list[CatalogVoice]:
        if not refresh:
            cached = self._load_cached_voices()
            if cached:
                return cached

        texttospeech, client = self._module_and_client()
        try:
            response = client.list_voices(timeout=60)
        except Exception as exc:
            raise PipelineError(
                "google_voice_catalog_failed",
                "Không thể tải danh sách voice Google TTS.",
                provider="google_tts",
                debug_detail=type(exc).__name__,
            ) from exc
        voices = []
        for voice in response.voices:
            gender = texttospeech.SsmlVoiceGender(voice.ssml_gender).name
            languages = tuple(str(code) for code in voice.language_codes)
            family = google_voice_family(str(voice.name))
            language_groups = tuple(dict.fromkeys(code.split("-", 1)[0] for code in languages))
            voices.append(
                CatalogVoice(
                    "google",
                    str(voice.name),
                    str(voice.name),
                    languages,
                    gender,
                    family,
                    (family, *language_groups),
                    int(voice.natural_sample_rate_hertz or 0),
                )
            )
        voices = self._sort_voices(voices)
        self._save_cached_voices(voices)
        return voices

    @staticmethod
    def _sort_voices(voices: list[CatalogVoice]) -> list[CatalogVoice]:
        return sorted(voices, key=lambda item: (item.primary_language, item.family, item.name))

    def _load_cached_voices(self) -> list[CatalogVoice]:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != self.CACHE_SCHEMA_VERSION:
                return []
            rows = payload.get("voices")
            if not isinstance(rows, list):
                return []
            voices = []
            for row in rows:
                if not isinstance(row, dict) or row.get("provider") != "google":
                    return []
                language_codes = row.get("language_codes")
                categories = row.get("categories", [])
                if not isinstance(language_codes, list) or not all(
                    isinstance(value, str) for value in language_codes
                ):
                    return []
                if not isinstance(categories, list) or not all(
                    isinstance(value, str) for value in categories
                ):
                    return []
                voices.append(
                    CatalogVoice(
                        "google",
                        str(row["voice_id"]),
                        str(row["name"]),
                        tuple(language_codes),
                        str(row["gender"]),
                        str(row["family"]),
                        tuple(categories),
                        int(row.get("sample_rate_hz", 0)),
                    )
                )
            return self._sort_voices(voices)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return []

    def _save_cached_voices(self, voices: list[CatalogVoice]) -> None:
        payload = {
            "schema_version": self.CACHE_SCHEMA_VERSION,
            "voices": [
                {
                    "provider": voice.provider,
                    "voice_id": voice.voice_id,
                    "name": voice.name,
                    "language_codes": list(voice.language_codes),
                    "gender": voice.gender,
                    "family": voice.family,
                    "categories": list(voice.categories),
                    "sample_rate_hz": voice.sample_rate_hz,
                }
                for voice in voices
            ],
        }
        partial = self.cache_path.with_suffix(f"{self.cache_path.suffix}.partial")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            partial.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(partial, self.cache_path)
        except OSError:
            partial.unlink(missing_ok=True)

    def preview(self, voice: CatalogVoice, text: str | None = None) -> Path:
        cache_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        output = cache_root / "QuyetBoITStudio" / "cache" / "voice-preview" / "google-preview.wav"
        GoogleCloudSpeechService(
            self.service_account_path,
            voice.voice_id,
            language_code=voice.primary_language or "en-US",
        ).synthesize(text or "Can you guess this English word?", output)
        return output


class CapCutVoiceCatalogService:
    def __init__(self) -> None:
        store = CapCutSessionStore()
        session = store.load()
        if not session:
            raise PipelineError(
                "capcut_login_required",
                "Chưa có phiên CapCut. Hãy đăng nhập CapCut tại phần VietSub trước.",
                provider="capcut",
            )
        client = CapCutEditClient(session, session_store=store)
        client.ensure_session()
        self.service = CapCutTtsService(client)
        self._voices: dict[str, Voice] = {}

    def list_voices(self, refresh: bool = False) -> list[CatalogVoice]:
        raw_voices = self.service.list_voices(refresh=refresh)
        self._voices = {voice.speaker: voice for voice in raw_voices}
        output = []
        for voice in raw_voices:
            categories = voice.categories or (voice.category,)
            output.append(
                CatalogVoice(
                    "capcut",
                    voice.speaker,
                    voice.name,
                    (voice.lang,) if voice.lang else (),
                    voice.gender or "UNKNOWN",
                    voice.category or (categories[0] if categories else "Other"),
                    categories,
                )
            )
        return sorted(output, key=lambda item: (item.primary_language, item.family, item.name.casefold()))

    def preview(self, voice: CatalogVoice, text: str | None = None) -> str:
        del text  # CapCut returns its provider-managed audition clip.
        raw = self._voices.get(voice.voice_id)
        if raw is None:
            raise PipelineError(
                "capcut_voice_missing",
                "Voice CapCut không còn trong catalog hiện tại.",
                provider="capcut",
            )
        return self.service.preview_url(raw)


class MinigameSpeechService(Protocol):
    def synthesize(
        self, text: str, output: Path, *, cancel: CancellationToken | None = None
    ) -> Path: ...


class GoogleCloudSpeechService:
    def __init__(
        self,
        service_account_path: str | Path,
        voice_name: str,
        *,
        language_code: str = "en-US",
        volume: int = 100,
    ) -> None:
        self.service_account_path = Path(service_account_path).expanduser().resolve()
        self.voice_name = voice_name.strip()
        self.language_code = language_code
        self.volume = max(0, min(150, int(volume)))

    def synthesize(
        self, text: str, output: Path, *, cancel: CancellationToken | None = None
    ) -> Path:
        if cancel:
            cancel.raise_if_cancelled()
        try:
            from google.cloud import texttospeech
        except ImportError as exc:
            raise PipelineError(
                "google_tts_missing",
                "Chưa cài Google Cloud Text-to-Speech. Hãy cài lại dependencies của ứng dụng.",
                provider="google_tts",
            ) from exc
        try:
            client = texttospeech.TextToSpeechClient.from_service_account_file(
                str(self.service_account_path)
            )
            response = client.synthesize_speech(
                request={
                    "input": texttospeech.SynthesisInput(text=text),
                    "voice": texttospeech.VoiceSelectionParams(
                        language_code=self.language_code,
                        name=self.voice_name,
                    ),
                    "audio_config": texttospeech.AudioConfig(
                        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                        volume_gain_db=self._volume_db(),
                    ),
                },
                timeout=60,
            )
        except Exception as exc:
            raise PipelineError(
                "google_tts_failed",
                "Google TTS không thể tạo voice. Hãy kiểm tra service account, API và voice.",
                provider="google_tts",
                retryable=False,
                debug_detail=type(exc).__name__,
            ) from exc
        if cancel:
            cancel.raise_if_cancelled()
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        partial.write_bytes(response.audio_content)
        os.replace(partial, output)
        return output

    def _volume_db(self) -> float:
        if self.volume <= 0:
            return -96.0
        import math

        return max(-96.0, min(16.0, 20 * math.log10(self.volume / 100)))


class CapCutEnglishSpeechService:
    def __init__(self, voice_name: str, *, volume: int = 100) -> None:
        store = CapCutSessionStore()
        session = store.load()
        if not session:
            raise PipelineError(
                "capcut_login_required",
                "Chưa có phiên CapCut. Hãy đăng nhập CapCut tại phần VietSub trước.",
                provider="capcut",
            )
        client = CapCutEditClient(session, session_store=store)
        client.ensure_session()
        self.service = CapCutTtsService(client, categories=("english",))
        voices = self.service.list_voices()
        self.voice = self._select_voice(voices, voice_name)
        self.volume = max(0, min(150, int(volume)))

    @staticmethod
    def _select_voice(voices: list[Voice], requested: str) -> Voice:
        query = requested.strip().casefold()
        selected = next(
            (
                voice
                for voice in voices
                if voice.speaker.casefold() == query or voice.name.casefold() == query
            ),
            None,
        )
        if selected:
            return selected
        if not query and voices:
            return voices[0]
        raise PipelineError(
            "capcut_english_voice_missing",
            "Không tìm thấy English voice đã chọn trong CapCut. Hãy nhập đúng tên hoặc speaker ID.",
            provider="capcut",
        )

    def synthesize(
        self, text: str, output: Path, *, cancel: CancellationToken | None = None
    ) -> Path:
        self.service.synthesize(
            text, self.voice, output, rate=1.0, volume=self.volume, cancel=cancel
        )
        return output
