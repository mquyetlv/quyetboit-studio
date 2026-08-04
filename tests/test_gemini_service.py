import asyncio

from quyetboit_studio.core import PipelineError, RetryPolicy, SrtCue
from quyetboit_studio.services.gemini import (
    AdaptiveLimiter,
    GeminiTranslationService,
    TranslationConfig,
)
from quyetboit_studio.services.gemini.client import GeminiClient, TranslatedBatch, TranslatedItem


class FakeGemini:
    async def generate(self, model, prompt):
        import json
        items = json.loads(prompt.split("Items: ", 1)[1])
        return TranslatedBatch(items=[TranslatedItem(id=item["id"], translated_text=f"Việt {item['id']}") for item in items])


def test_structured_translation_keeps_ids_and_timestamps():
    cues = [SrtCue(index, index * 1000, index * 1000 + 900, f"中文 {index}") for index in range(1, 13)]
    result = GeminiTranslationService(FakeGemini()).translate_sync(
        cues, TranslationConfig(batch_size=10, concurrency=2)
    )
    assert [cue.translated_text for cue in result.cues] == [f"Việt {index}" for index in range(1, 13)]
    assert result.cues[-1].start_ms == 12_000


def test_adaptive_limiter_decreases_and_recovers():
    async def scenario():
        limiter = AdaptiveLimiter(3)
        await limiter.on_rate_limit()
        assert limiter.limit == 2
        for _ in range(4):
            await limiter.on_success()
        assert limiter.limit == 3

    asyncio.run(scenario())


def test_gemini_http_error_extracts_safe_provider_diagnostics():
    class ApiError(Exception):
        code = 429
        status = "RESOURCE_EXHAUSTED"
        message = "Quota exceeded for user@example.com api_key=secret-value"
        response = None

        def __init__(self):
            self.details = {
                "error": {
                    "details": [
                        {
                            "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                            "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                        },
                        {"retryDelay": "12s"},
                    ],
                },
            }

    error = GeminiClient._map_error(ApiError())

    assert error.error_code == "gemini_http_429"
    assert error.retry_after == 12
    assert "reason=RESOURCE_EXHAUSTED" in error.log_detail
    assert "quota=generativelanguage.googleapis.com" in error.log_detail
    assert "quota_id=GenerateRequestsPerMinutePerProjectPerModel-FreeTier" in error.log_detail
    assert "retry_after=12s" in error.log_detail
    assert "secret-value" not in error.log_detail
    assert "u***@example.com" in error.log_detail


def test_gemini_retry_emits_detailed_progress_and_reduces_concurrency():
    class RateLimitedOnce:
        def __init__(self):
            self.calls = 0

        async def generate(self, model, prompt):
            self.calls += 1
            if self.calls == 1:
                error = PipelineError(
                    "gemini_http_429",
                    "Gemini đang giới hạn tốc độ.",
                    provider="gemini",
                    retryable=True,
                    log_detail="HTTP 429; reason=RESOURCE_EXHAUSTED; retry_after=0s",
                )
                error.retry_after = 0
                raise error
            import json
            items = json.loads(prompt.split("Items: ", 1)[1])
            return TranslatedBatch(items=[
                TranslatedItem(id=item["id"], translated_text=f"Việt {item['id']}")
                for item in items
            ])

    events = []
    service = GeminiTranslationService(
        RateLimitedOnce(),
        RetryPolicy(max_attempts=4, base_delay=0, full_jitter=False),
    )
    result = service.translate_sync(
        [SrtCue(1, 0, 900, "中文")],
        TranslationConfig(batch_size=10, concurrency=3),
        progress=events.append,
    )

    retry = next(event for event in events if event.state == "retrying")
    assert result.cues[0].translated_text == "Việt 1"
    assert "Gemini retry 2/4" in retry.message
    assert "reason=RESOURCE_EXHAUSTED" in retry.message
    assert "concurrency=3→2" in retry.message
