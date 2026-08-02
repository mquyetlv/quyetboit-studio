from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from .models import CancellationToken, PipelineError

T = TypeVar("T")


@dataclass(slots=True, frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.75
    max_delay: float = 20.0
    full_jitter: bool = True

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(self.max_delay, max(0.0, retry_after))
        ceiling = min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
        return random.uniform(0, ceiling) if self.full_jitter else ceiling


def retry_call(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    cancel: CancellationToken | None = None,
    should_retry: Callable[[BaseException], bool] | None = None,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> T:
    policy = policy or RetryPolicy()
    last: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        if cancel:
            cancel.raise_if_cancelled()
        try:
            return operation()
        except BaseException as exc:
            last = exc
            retryable = should_retry(exc) if should_retry else (
                isinstance(exc, PipelineError) and exc.retryable
            )
            if not retryable or attempt >= policy.max_attempts:
                raise
            delay = policy.delay(attempt, getattr(exc, "retry_after", None))
            if on_retry:
                on_retry(attempt, exc, delay)
            if cancel:
                if cancel.wait(delay):
                    cancel.raise_if_cancelled()
            else:
                time.sleep(delay)
    assert last is not None
    raise last
