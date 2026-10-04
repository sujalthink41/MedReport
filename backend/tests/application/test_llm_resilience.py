"""Retry, repair, caching and tracing — each tested in isolation.

The point of building these as separate decorators: every one of these tests
exercises one concern without the others in the way. Written as one function,
none of this would be testable.

No API key, no network, no cost. You cannot reliably ask a real provider to
rate-limit you on demand, which is why failure paths can only be tested against
a fake.
"""

from decimal import Decimal

import pytest
from pydantic import BaseModel

from app.adapters.llm.resilience import CachingLLM, RepairingLLM, RetryingLLM
from app.adapters.llm.tracing import TracedLLM
from app.domain.errors import LLMInvalidOutputError, LLMUnavailableError
from app.domain.ports.llm import ImagePart, Prompt, Purpose
from tests.fakes import FakeLLM, RecordingTraceWriter


class Answer(BaseModel):
    value: str


PROMPT = Prompt(system="you read lab reports", user="read this page")


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff is real seconds. Tests should not actually wait them out."""
    import asyncio

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


class TestRetry:
    async def test_a_transient_failure_is_retried(self) -> None:
        inner = FakeLLM().fails(LLMUnavailableError(), times=2).returns(Answer(value="ok"))

        result = await RetryingLLM(inner).complete(
            prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT
        )

        assert result.value.value == "ok"
        assert len(inner.calls) == 3

    async def test_it_gives_up_eventually(self) -> None:
        inner = FakeLLM().fails(LLMUnavailableError(), times=99)

        with pytest.raises(LLMUnavailableError):
            await RetryingLLM(inner, max_attempts=3).complete(
                prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT
            )

        # Bounded. An unbounded retry against a dead provider is how one report
        # occupies a worker forever.
        assert len(inner.calls) == 3

    async def test_a_bad_answer_is_not_retried(self) -> None:
        inner = FakeLLM().fails(LLMInvalidOutputError(), times=99)

        with pytest.raises(LLMInvalidOutputError):
            await RetryingLLM(inner).complete(prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT)

        # The distinction that matters. The same prompt yields the same malformed
        # answer, so retrying is money spent to reach an identical failure.
        assert len(inner.calls) == 1

    async def test_no_failure_means_no_retry(self) -> None:
        inner = FakeLLM().returns(Answer(value="ok"))

        await RetryingLLM(inner).complete(prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT)

        assert len(inner.calls) == 1

    def test_backoff_is_jittered(self) -> None:
        retrying = RetryingLLM(FakeLLM(), base_delay=1.0, max_delay=30.0)

        delays = [retrying._delay_for(3) for _ in range(20)]

        # Not a constant. Without jitter every worker retries at the same instant
        # and rebuilds the thundering herd that caused the outage.
        assert len(set(delays)) > 1
        assert all(0 <= d <= 4.0 for d in delays)

    def test_backoff_is_capped(self) -> None:
        retrying = RetryingLLM(FakeLLM(), base_delay=1.0, max_delay=5.0)

        assert all(retrying._delay_for(20) <= 5.0 for _ in range(20))


class TestRepair:
    async def test_a_bad_answer_gets_one_correction_attempt(self) -> None:
        inner = FakeLLM()
        inner.fails(LLMInvalidOutputError(detail="field 'value' is required"), times=1)
        inner.returns(Answer(value="fixed"))

        result = await RepairingLLM(inner).complete(
            prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT
        )

        assert result.value.value == "fixed"
        assert len(inner.calls) == 2

    async def test_the_repair_prompt_carries_the_validation_error(self) -> None:
        inner = FakeLLM()
        inner.fails(LLMInvalidOutputError(detail="field 'value' is required"), times=1)
        inner.returns(Answer(value="fixed"))

        await RepairingLLM(inner).complete(prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT)

        # The whole reason repair works where retry does not: the model is told
        # what was wrong, rather than asked the same question again.
        _, repaired = inner.calls[1]
        assert "field 'value' is required" in repaired.user
        assert "read this page" in repaired.user

    async def test_only_one_repair_is_attempted(self) -> None:
        inner = FakeLLM().fails(LLMInvalidOutputError(), times=99)

        with pytest.raises(LLMInvalidOutputError):
            await RepairingLLM(inner).complete(
                prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT
            )

        # A model that cannot produce the shape twice will not produce it on the
        # fifth try. The honest outcome is to fail the row and mark it unreadable.
        assert len(inner.calls) == 2

    async def test_images_survive_the_repair(self) -> None:
        inner = FakeLLM()
        inner.fails(LLMInvalidOutputError(detail="bad"), times=1)
        inner.returns(Answer(value="fixed"))
        with_image = Prompt(system="s", user="u", images=[ImagePart(data=b"png-bytes")])

        await RepairingLLM(inner).complete(
            prompt=with_image, schema=Answer, purpose=Purpose.EXTRACT
        )

        # Dropping the page on the retry would ask the model to re-read a document
        # it can no longer see.
        _, repaired = inner.calls[1]
        assert repaired.images[0].data == b"png-bytes"


class TestCaching:
    async def test_an_identical_request_is_served_from_memory(self) -> None:
        inner = FakeLLM().returns(Answer(value="first"))

        caching = CachingLLM(inner)
        await caching.complete(prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT)
        second = await caching.complete(prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT)

        # Protects against a pipeline node retried after a downstream failure
        # re-sending the same page to a vision model.
        assert len(inner.calls) == 1
        assert second.value.value == "first"
        assert second.usage.cached is True
        # Zero, so cost accounting is not inflated by calls that never reached a
        # provider.
        assert second.usage.cost_usd == Decimal(0)

    async def test_a_different_page_is_a_different_request(self) -> None:
        inner = FakeLLM().returns(Answer(value="a"), Answer(value="b"))
        caching = CachingLLM(inner)

        await caching.complete(
            prompt=Prompt(system="s", user="u", images=[ImagePart(data=b"page-1")]),
            schema=Answer,
            purpose=Purpose.EXTRACT,
        )
        await caching.complete(
            prompt=Prompt(system="s", user="u", images=[ImagePart(data=b"page-2")]),
            schema=Answer,
            purpose=Purpose.EXTRACT,
        )

        # Same instructions, different page. The fingerprint must cover the image
        # bytes or page 2 would be served page 1's results.
        assert len(inner.calls) == 2

    async def test_non_deterministic_purposes_are_not_cached(self) -> None:
        inner = FakeLLM().returns(Answer(value="a"), Answer(value="b"))
        caching = CachingLLM(inner)

        await caching.complete(prompt=PROMPT, schema=Answer, purpose=Purpose.EXPLAIN)
        await caching.complete(prompt=PROMPT, schema=Answer, purpose=Purpose.EXPLAIN)

        # Explanations run at a non-zero temperature; freezing one phrasing for
        # the life of the process buys nothing. They are cached properly by
        # (marker, band) in Postgres instead.
        assert len(inner.calls) == 2


class TestTracing:
    async def test_a_successful_call_is_recorded(self) -> None:
        traces = RecordingTraceWriter()
        inner = FakeLLM().returns(Answer(value="ok"))

        await TracedLLM(inner, traces).complete(
            prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT
        )

        assert len(traces.rows) == 1
        assert traces.rows[0]["status"] == "ok"
        assert traces.rows[0]["purpose"] == "extract"
        # The raw reply, because the parsed object tells you what we concluded and
        # only this tells you what the model actually said.
        assert traces.rows[0]["response"] is not None

    async def test_a_failed_call_is_recorded_too(self) -> None:
        traces = RecordingTraceWriter()
        inner = FakeLLM().fails(LLMUnavailableError(provider_error="RateLimitError"))

        with pytest.raises(LLMUnavailableError):
            await TracedLLM(inner, traces).complete(
                prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT
            )

        # Failures matter more than successes. The row explaining a wrong
        # haemoglobin is almost always one that errored or returned nonsense.
        assert len(traces.rows) == 1
        assert traces.rows[0]["status"] == "error"
        assert "RateLimitError" in str(traces.rows[0]["error"])

    async def test_image_bytes_are_not_stored(self) -> None:
        traces = RecordingTraceWriter()
        inner = FakeLLM().returns(Answer(value="ok"))
        with_image = Prompt(system="s", user="u", images=[ImagePart(data=b"x" * 5000)])

        await TracedLLM(inner, traces).complete(
            prompt=with_image, schema=Answer, purpose=Purpose.EXTRACT
        )

        # A 25-page report would put tens of megabytes of base64 into Postgres per
        # run, and the original document is already in object storage.
        recorded = str(traces.rows[0]["prompt"])
        assert "not stored" in recorded
        assert "xxxxx" not in recorded

    async def test_a_trace_failure_does_not_fail_the_call(self) -> None:
        class BrokenWriter:
            async def record(self, **fields: object) -> None:
                raise RuntimeError("disk full")

        inner = FakeLLM().returns(Answer(value="ok"))

        result = await TracedLLM(inner, BrokenWriter()).complete(  # type: ignore[arg-type]
            prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT
        )

        # Losing the audit of one call is bad. Losing a user's extracted results
        # because the trace insert failed is worse.
        assert result.value.value == "ok"


class TestComposition:
    async def test_the_whole_stack_works_together(self) -> None:
        traces = RecordingTraceWriter()
        inner = FakeLLM()
        inner.fails(LLMUnavailableError(), times=1)
        inner.returns(Answer(value="ok"))

        llm = TracedLLM(RetryingLLM(RepairingLLM(CachingLLM(inner))), traces)
        result = await llm.complete(prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT)

        assert result.value.value == "ok"
        # Two provider calls (one failed, one succeeded), one trace of the final
        # outcome. Each layer did exactly its own job.
        assert len(inner.calls) == 2
        assert len(traces.rows) == 1
        assert traces.rows[0]["status"] == "ok"

    async def test_a_layer_can_be_removed(self) -> None:
        # The payoff of composition over configuration: the eval harness runs
        # without caching so repeated runs genuinely re-query, and nothing else
        # changes.
        inner = FakeLLM().returns(Answer(value="a"), Answer(value="b"))

        llm = RetryingLLM(inner)  # no cache, no tracing
        await llm.complete(prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT)
        await llm.complete(prompt=PROMPT, schema=Answer, purpose=Purpose.EXTRACT)

        assert len(inner.calls) == 2
