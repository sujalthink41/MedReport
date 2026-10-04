"""LiteLLM adapter.

LiteLLM gives one call shape across providers, so switching GPT to Claude to
Gemini is a model string. That matters more than it sounds for a product that
will outlive several provider line-ups.
"""

import base64
import json
import time
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError

from app.adapters.llm.router import ModelRouter
from app.core.logging import get_logger
from app.domain.errors import LLMInvalidOutputError, LLMUnavailableError
from app.domain.ports.llm import LLMResult, Prompt, Purpose, Usage

log = get_logger(__name__)


class LiteLLMClient:
    def __init__(self, router: ModelRouter, api_key: str) -> None:
        self._router = router
        self._api_key = api_key

    async def complete[T](
        self, *, prompt: Prompt, schema: type[T], purpose: Purpose
    ) -> LLMResult[T]:
        if not issubclass(schema, BaseModel):
            raise TypeError("schema must be a pydantic BaseModel")

        config = self._router.for_purpose(purpose)
        if prompt.images and not config.supports_vision:
            # Catching this here rather than letting the provider return a
            # confusing error about message format.
            raise LLMInvalidOutputError(purpose=purpose.value, reason="model_lacks_vision")

        started = time.perf_counter()
        response = await self._call(prompt, schema, config, purpose)
        latency_ms = int((time.perf_counter() - started) * 1000)

        raw = response.choices[0].message.content or ""
        value = self._parse(raw, schema, purpose)

        return LLMResult(
            value=value,
            raw=raw,
            usage=Usage(
                model=config.model,
                prompt_tokens=getattr(response.usage, "prompt_tokens", 0),
                completion_tokens=getattr(response.usage, "completion_tokens", 0),
                cost_usd=_cost_of(response),
                latency_ms=latency_ms,
            ),
        )

    async def _call(self, prompt: Prompt, schema: type[Any], config: Any, purpose: Purpose) -> Any:
        import litellm

        try:
            return await litellm.acompletion(
                model=config.model,
                api_key=self._api_key,
                messages=_messages(prompt),
                temperature=config.temperature,
                max_tokens=config.max_output_tokens,
                timeout=config.timeout_seconds,
                # Schema enforced by the provider, not by asking politely in the
                # prompt. "Respond with JSON" in English is a request; this is a
                # constraint, and it removes an entire category of parse failure.
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": schema.model_json_schema(),
                        "strict": True,
                    },
                },
            )
        except Exception as exc:
            name = type(exc).__name__
            log.warning("llm_call_failed", purpose=purpose.value, error_type=name)
            # Everything the provider can throw becomes one retryable error.
            # A litellm or openai exception reaching the domain would break the
            # dependency rule and would not map to a sensible status.
            raise LLMUnavailableError(purpose=purpose.value, provider_error=name) from exc

    def _parse[T](self, raw: str, schema: type[T], purpose: Purpose) -> T:
        try:
            return schema.model_validate_json(raw)  # type: ignore[attr-defined,no-any-return]
        except (ValidationError, json.JSONDecodeError) as exc:
            # NOT retryable, which is why it is a distinct error. The same prompt
            # produces the same malformed answer; the correct response is repair -
            # feed the validation error back and ask for a correction (CP17).
            log.warning("llm_output_invalid", purpose=purpose.value)
            raise LLMInvalidOutputError(
                purpose=purpose.value,
                # The error text, never the content. A validation message is safe;
                # the model's reply could contain a page of someone's lab results.
                detail=str(exc)[:500],
            ) from exc


def _messages(prompt: Prompt) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt.user}]
    for image in prompt.images:
        encoded = base64.b64encode(image.data).decode()
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image.media_type};base64,{encoded}"},
            }
        )
    return [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": content},
    ]


def _cost_of(response: Any) -> Decimal:
    """What this call cost, from LiteLLM's own pricing tables.

    Best-effort: an unknown model yields zero rather than failing the call. A
    missing cost figure is an accounting gap, not a reason to lose a user's
    extracted report.
    """
    try:
        import litellm

        return Decimal(str(litellm.completion_cost(completion_response=response)))
    except Exception:  # noqa: BLE001
        return Decimal(0)
