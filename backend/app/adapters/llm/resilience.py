"""Retry and repair, as decorators over ``LLMClient``.

Two different failures that look similar and need opposite responses.

**The provider failed** — rate limit, timeout, 503. Transient. Back off and send
the identical request again.

**The model answered badly** — the reply does not match the schema. Sending the
identical request again produces the identical bad answer, so retrying burns money
to reach the same place. The fix is to *repair*: show the model what was wrong and
ask for a correction.

Collapsing these into one "retry on error" is the common mistake, and it is why
systems quietly spend a fortune re-asking a question that was never going to work.
"""

import asyncio
import random
from decimal import Decimal
from typing import Any, cast

from app.core.logging import get_logger
from app.domain.errors import LLMInvalidOutputError, LLMUnavailableError
from app.domain.ports.llm import LLMClient, LLMResult, Prompt, Purpose

log = get_logger(__name__)


class RetryingLLM:
    """Exponential backoff with jitter, for transient provider failures only."""

    def __init__(
        self,
        inner: LLMClient,
        *,
        max_attempts: int = 4,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
    ) -> None:
        self._inner = inner
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay

    async def complete[T](
        self, *, prompt: Prompt, schema: type[T], purpose: Purpose
    ) -> LLMResult[T]:
        last: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self._inner.complete(prompt=prompt, schema=schema, purpose=purpose)
            except LLMUnavailableError as exc:
                last = exc
                if attempt == self._max_attempts:
                    break
                delay = self._delay_for(attempt)
                log.warning(
                    "llm_retry",
                    purpose=purpose.value,
                    attempt=attempt,
                    delay_seconds=round(delay, 2),
                )
                await asyncio.sleep(delay)
            except LLMInvalidOutputError:
                # Explicitly NOT retried. The same prompt produces the same
                # malformed answer, so this would be money spent to reach an
                # identical failure. RepairingLLM handles it instead.
                raise

        if last is None:  # pragma: no cover - the loop always sets it
            raise LLMUnavailableError(purpose=purpose.value, reason="exhausted")
        raise last

    def _delay_for(self, attempt: int) -> float:
        """Exponential, capped, with jitter.

        Jitter is not optional. Without it, a provider blip makes every worker
        retry at exactly t+1s, then t+2s, then t+4s - rebuilding the thundering
        herd that caused the outage. Full jitter spreads them across the window.
        """
        ceiling = min(self._base_delay * (2 ** (attempt - 1)), self._max_delay)
        return random.uniform(0, ceiling)  # noqa: S311 - jitter, not cryptography


class RepairingLLM:
    """One correction attempt when the reply does not match the schema.

    The repair prompt includes the validation error, which is the whole point: a
    model told "that was not valid JSON matching the schema, here is what failed"
    usually fixes it, while the same model asked the same question again does not.

    Exactly one attempt. A model that cannot produce the shape twice is not going
    to produce it on the fifth try, and the honest outcome is to fail the row and
    mark it unreadable - "we could not read this" beats a confident wrong number.
    """

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner

    async def complete[T](
        self, *, prompt: Prompt, schema: type[T], purpose: Purpose
    ) -> LLMResult[T]:
        try:
            return await self._inner.complete(prompt=prompt, schema=schema, purpose=purpose)
        except LLMInvalidOutputError as exc:
            detail = str(exc.context.get("detail", ""))
            log.warning("llm_repair_attempt", purpose=purpose.value)

            repaired = Prompt(
                system=prompt.system,
                user=(
                    f"{prompt.user}\n\n"
                    "---\n"
                    "Your previous reply could not be parsed. The validation error was:\n"
                    f"{detail}\n\n"
                    "Reply again with valid JSON matching the required schema exactly. "
                    "Do not include any commentary or markdown fences."
                ),
                images=prompt.images,
            )
            # If the repair also fails, the error propagates untouched. No third
            # attempt, no fallback to a different model, no guessing.
            return await self._inner.complete(prompt=repaired, schema=schema, purpose=purpose)


class CachingLLM:
    """Serves a repeated identical request from memory.

    Scoped to one process and one run, which is the right scope for what it
    actually protects against: a pipeline node retried after a downstream failure
    re-sending the same page to a vision model.

    Per-marker explanations are cached differently and far more valuably - by
    (marker, band) in Postgres, shared across every user, because that text
    contains no PHI. That cache belongs to CP22, not here.
    """

    def __init__(self, inner: LLMClient, *, cacheable: set[Purpose] | None = None) -> None:
        self._inner = inner
        # Heterogeneous by nature: one dict holds results for many schemas. The
        # key includes the schema name, so a hit can only ever be the type the
        # caller asked for - which is what makes the cast below sound.
        self._cache: dict[tuple[str, str, str], LLMResult[Any]] = {}
        # Only deterministic purposes. Caching a temperature-0.3 explanation would
        # freeze one phrasing for the life of the process for no benefit.
        self._cacheable = cacheable or {Purpose.EXTRACT, Purpose.VERIFY}

    async def complete[T](
        self, *, prompt: Prompt, schema: type[T], purpose: Purpose
    ) -> LLMResult[T]:
        if purpose not in self._cacheable:
            return await self._inner.complete(prompt=prompt, schema=schema, purpose=purpose)

        key = (purpose.value, schema.__name__, prompt.fingerprint())
        hit = self._cache.get(key)
        if hit is not None:
            log.info("llm_cache_hit", purpose=purpose.value)
            from dataclasses import replace

            # Marked cached and zero-cost, so cost accounting is not inflated by
            # calls that never reached a provider.
            return LLMResult(
                value=cast("T", hit.value),
                raw=hit.raw,
                usage=replace(hit.usage, cached=True, cost_usd=Decimal(0)),
            )

        result = await self._inner.complete(prompt=prompt, schema=schema, purpose=purpose)
        self._cache[key] = result
        return result
