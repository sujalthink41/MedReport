"""The model port.

Phrased in our vocabulary, naming no provider: "complete this prompt into this
shape". Swapping GPT for Claude or Gemini then touches one adapter and one config
value.

Note what the schema type is *not* bound to. The port says ``type[T]``, and the
concrete schemas are Pydantic models living in ``adapters/llm/schemas.py`` —
because an LLM's structured output is the wire format of an external service, not
a domain concept. That keeps the domain free of Pydantic while still giving the
adapter everything it needs.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class Purpose(StrEnum):
    """What a call is for. Drives which model answers it.

    Different jobs want genuinely different models: reading a scanned page needs
    vision and accuracy, writing a marker explanation needs neither and is served
    from cache anyway, and reasoning across a whole panel wants the strongest
    model available. One model for all three would be either wasteful or weak.
    """

    EXTRACT = "extract"
    """Vision, high accuracy, strict structure. The expensive one."""

    VERIFY = "verify"
    """Self-checking an extraction against the page it came from."""

    EXPLAIN = "explain"
    """Per-marker copy. Cheap, and cached across every user."""

    REASON = "reason"
    """Cross-marker reasoning over a whole panel. Low volume, high value."""

    PREP_SHEET = "prep_sheet"
    """The one page a user carries to their doctor."""


@dataclass(frozen=True)
class ImagePart:
    """A page image for a vision call."""

    data: bytes
    media_type: str = "image/png"


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    images: list[ImagePart] = field(default_factory=list)

    def fingerprint(self) -> str:
        """A stable hash of everything that determines the answer.

        Used as a cache key, so it must cover the images too - two pages with the
        same instructions are not the same request.
        """
        import hashlib

        digest = hashlib.sha256()
        digest.update(self.system.encode())
        digest.update(b"\x00")
        digest.update(self.user.encode())
        for image in self.images:
            digest.update(b"\x00")
            digest.update(image.data)
        return digest.hexdigest()


@dataclass(frozen=True)
class Usage:
    """What a call cost, in tokens and money.

    Tracked from the first call rather than added later, because cost per report
    is the number that decides whether the business works. A product that spends
    more per upload than a user will ever pay does not have a pricing problem, it
    has an architecture problem - and you want to know that in week three.
    """

    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int
    cached: bool = False


@dataclass(frozen=True)
class LLMResult[T]:
    value: T
    usage: Usage
    raw: str
    """The unparsed response.

    Kept because when a user says "it showed my haemoglobin wrong", the parsed
    object tells you what we concluded and only the raw text tells you what the
    model actually said.
    """


class LLMClient(Protocol):
    async def complete[T](
        self,
        *,
        prompt: Prompt,
        schema: type[T],
        purpose: Purpose,
    ) -> LLMResult[T]:
        """Run a prompt and parse the reply into ``schema``.

        Raises:
            LLMUnavailableError: rate limited, timed out, provider down.
                Transient and retryable.
            LLMInvalidOutputError: the reply does not match the schema. NOT
                retryable - the same prompt produces the same malformed answer.
                The correct response is repair, not repetition.
        """
        ...
