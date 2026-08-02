import asyncio

from quyetboit_studio.core import SrtCue
from quyetboit_studio.services.gemini import (
    AdaptiveLimiter,
    GeminiTranslationService,
    TranslationConfig,
)
from quyetboit_studio.services.gemini.client import TranslatedBatch, TranslatedItem


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
