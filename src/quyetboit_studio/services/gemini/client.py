from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Self

from pydantic import BaseModel, Field, ValidationError

from ...core.models import CancellationToken, PipelineError, ProgressCallback, ProgressEvent, SrtCue
from ...core.retry import RetryPolicy
from ..subtitles import batch_cues, find_untranslated


class TranslatedItem(BaseModel):
    id: int
    translated_text: str = Field(min_length=1)


class TranslatedBatch(BaseModel):
    items: list[TranslatedItem]


@dataclass(slots=True, frozen=True)
class TranslationConfig:
    model: str = "gemini-3.6-flash"
    batch_size: int = 50
    concurrency: int = 3
    context: str = "Giọng điệu Gen Z"


@dataclass(slots=True)
class TranslationResult:
    cues: list[SrtCue]
    invalid_cues: list[SrtCue] = field(default_factory=list)
    retries: int = 0


class AdaptiveLimiter:
    def __init__(self, maximum: int = 3) -> None:
        self.maximum = max(1, maximum)
        self.limit = self.maximum
        self._active = 0
        self._condition = asyncio.Condition()
        self._successes = 0

    async def __aenter__(self) -> Self:
        async with self._condition:
            await self._condition.wait_for(lambda: self._active < self.limit)
            self._active += 1
        return self

    async def __aexit__(self, *_: object) -> None:
        async with self._condition:
            self._active -= 1
            self._condition.notify_all()

    async def on_rate_limit(self) -> None:
        async with self._condition:
            self.limit = max(1, self.limit - 1)
            self._successes = 0

    async def on_success(self) -> None:
        async with self._condition:
            self._successes += 1
            if self.limit < self.maximum and self._successes >= max(3, self.limit * 2):
                self.limit += 1
                self._successes = 0
                self._condition.notify_all()


class GeminiClient:
    def __init__(self, api_key: str | None = None, client: Any = None) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if client is not None:
            self.client = client
        elif self.api_key:
            try:
                from google import genai
            except ImportError as exc:
                raise PipelineError("gemini_sdk_missing", "Chưa cài google-genai.", provider="gemini") from exc
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None

    def require_client(self) -> Any:
        if not self.client:
            raise PipelineError(
                "gemini_key_missing", "Thiếu GEMINI_API_KEY. Hãy nhập key trong cấu hình Gemini.",
                provider="gemini",
            )
        return self.client

    def list_models(self) -> list[str]:
        client = self.require_client()
        result: list[str] = []
        for model in client.models.list():
            actions = getattr(model, "supported_actions", None) or []
            name = str(getattr(model, "name", "")).removeprefix("models/")
            if name and (not actions or "generateContent" in actions):
                result.append(name)
        return sorted(set(result))

    def test(self, model: str) -> None:
        client = self.require_client()
        client.models.generate_content(model=model, contents="Reply with OK only.")

    async def generate(self, model: str, prompt: str) -> TranslatedBatch:
        client = self.require_client()
        try:
            from google.genai import types
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=TranslatedBatch,
                ),
            )
            if getattr(response, "parsed", None) is not None:
                parsed = response.parsed
                return parsed if isinstance(parsed, TranslatedBatch) else TranslatedBatch.model_validate(parsed)
            return TranslatedBatch.model_validate_json(response.text)
        except (ImportError, ValidationError, json.JSONDecodeError) as exc:
            raise PipelineError(
                "gemini_schema_invalid", "Gemini trả kết quả không đúng schema.", provider="gemini",
                retryable=True, debug_detail=str(exc),
            ) from exc
        except Exception as exc:
            raise self._map_error(exc) from exc

    @staticmethod
    def _map_error(exc: BaseException) -> PipelineError:
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        status = int(status) if str(status).isdigit() else 0
        retryable = status in {408, 429} or status >= 500 or isinstance(exc, TimeoutError)
        if status in {400, 401, 403, 404}:
            message = "API key hoặc model Gemini không hợp lệ."
        elif status == 429:
            message = "Gemini đang giới hạn tốc độ; tác vụ sẽ giảm concurrency và thử lại."
        else:
            message = "Không thể gọi Gemini. Hãy kiểm tra mạng và quota."
        error = PipelineError(
            f"gemini_http_{status or 'error'}", message, provider="gemini",
            retryable=retryable, debug_detail=str(exc),
        )
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            error.retry_after = float(retry_after)
        return error


class GeminiTranslationService:
    def __init__(self, client: GeminiClient, retry_policy: RetryPolicy | None = None) -> None:
        self.client = client
        self.retry_policy = retry_policy or RetryPolicy(max_attempts=4)

    @staticmethod
    def _prompt(items: list[SrtCue], before: list[SrtCue], after: list[SrtCue], context: str) -> str:
        payload = [
            {"id": cue.id, "timestamp_ms": [cue.start_ms, cue.end_ms], "duration_ms": cue.duration_ms,
             "source_text": cue.text, "max_vietnamese_chars": max(8, round(cue.duration_ms / 1000 * 14))}
            for cue in items
        ]
        surroundings = [{"id": cue.id, "source_text": cue.text} for cue in [*before, *after]]
        return (
            "Dịch các item từ tiếng Trung sang tiếng Việt. Giữ nguyên ý nghĩa và tên riêng; "
            "không thêm giải thích; ưu tiên câu đủ ngắn để đọc trong timestamp. "
            f"Phong cách: {context}. Chỉ trả đúng ID trong items theo schema đã yêu cầu.\n"
            f"Context lân cận (không trả về): {json.dumps(surroundings, ensure_ascii=False)}\n"
            f"Items: {json.dumps(payload, ensure_ascii=False)}"
        )

    async def translate(
        self, cues: list[SrtCue], config: TranslationConfig | None = None,
        *, progress: ProgressCallback | None = None, cancel: CancellationToken | None = None,
    ) -> TranslationResult:
        config = config or TranslationConfig()
        if not 1 <= config.concurrency <= 8:
            raise ValueError("Concurrency Gemini phải trong khoảng 1–8.")
        limiter = AdaptiveLimiter(config.concurrency)
        batches = batch_cues(cues, config.batch_size)
        retries = 0

        async def execute(batch: dict, repair: bool = False) -> None:
            nonlocal retries
            expected = {cue.id: cue for cue in batch["items"]}
            prompt = self._prompt(batch["items"], batch.get("context_before", []), batch.get("context_after", []), config.context)
            last: PipelineError | None = None
            for attempt in range(1, self.retry_policy.max_attempts + 1):
                if cancel:
                    cancel.raise_if_cancelled()
                try:
                    async with limiter:
                        response = await self.client.generate(config.model, prompt)
                    returned = set()
                    for item in response.items:
                        if item.id in expected:
                            expected[item.id].translated_text = item.translated_text.strip()
                            returned.add(item.id)
                    if returned != set(expected):
                        raise PipelineError(
                            "gemini_missing_ids", "Gemini trả thiếu cue.", provider="gemini", retryable=True,
                        )
                    await limiter.on_success()
                    return
                except PipelineError as exc:
                    last = exc
                    if "429" in exc.error_code:
                        await limiter.on_rate_limit()
                    if not exc.retryable or attempt >= self.retry_policy.max_attempts:
                        break
                    retries += 1
                    delay = self.retry_policy.delay(attempt, getattr(exc, "retry_after", None))
                    if cancel:
                        if cancel.wait(delay):
                            cancel.raise_if_cancelled()
                    else:
                        await asyncio.sleep(delay)
            if not repair and last:
                # Validation/repair below gets one isolated chance without discarding valid batches.
                return
            if last:
                raise last

        completed = 0

        async def tracked(batch: dict) -> None:
            nonlocal completed
            await execute(batch)
            completed += 1
            if progress:
                progress(ProgressEvent(3, completed / max(1, len(batches)), "Dịch Gemini"))

        await asyncio.gather(*(tracked(batch) for batch in batches))
        missing = find_untranslated(cues)
        for start in range(0, len(missing), 50):
            await execute({"items": missing[start:start + 50]}, repair=True)
        invalid = find_untranslated(cues)
        if invalid:
            raise PipelineError(
                "translation_incomplete", f"Còn {len(invalid)} cue chưa dịch hợp lệ.",
                provider="gemini", retryable=True,
            )
        return TranslationResult(cues, invalid, retries)

    def translate_sync(self, *args: Any, **kwargs: Any) -> TranslationResult:
        return asyncio.run(self.translate(*args, **kwargs))
