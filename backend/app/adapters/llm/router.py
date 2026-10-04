"""Which model answers which kind of call.

One model for everything is either wasteful or weak. Reading a scanned page needs
vision and accuracy; writing a marker explanation needs neither and is served from
cache anyway; reasoning across a whole panel wants the strongest model available.

All of it is configuration, so changing a model is an environment variable rather
than a deploy — which matters because provider line-ups change every few months
and this product will outlive several of them.
"""

from dataclasses import dataclass

from app.core.config import Settings
from app.domain.ports.llm import Purpose


@dataclass(frozen=True)
class ModelConfig:
    model: str
    max_output_tokens: int
    temperature: float
    timeout_seconds: int

    supports_vision: bool = False


class ModelRouter:
    def __init__(self, settings: Settings) -> None:
        self._configs: dict[Purpose, ModelConfig] = {
            Purpose.EXTRACT: ModelConfig(
                model=settings.model_vision,
                max_output_tokens=8000,
                # Zero, deliberately. Reading a number off a page has one correct
                # answer, and sampling variety is only ever a chance to misread a
                # digit. Creativity is the wrong property here.
                temperature=0.0,
                # Generous: a dense 25-page report page can take a while, and a
                # timeout mid-extraction costs the whole page.
                timeout_seconds=settings.llm_timeout_seconds,
                supports_vision=True,
            ),
            Purpose.VERIFY: ModelConfig(
                model=settings.model_vision,
                max_output_tokens=4000,
                temperature=0.0,
                timeout_seconds=settings.llm_timeout_seconds,
                supports_vision=True,
            ),
            Purpose.EXPLAIN: ModelConfig(
                model=settings.model_cheap,
                max_output_tokens=1200,
                # A little variation is fine in prose, and these are cached by
                # (marker, band) so one phrasing is reused by every user anyway.
                temperature=0.3,
                timeout_seconds=60,
            ),
            Purpose.REASON: ModelConfig(
                model=settings.model_strong,
                max_output_tokens=3000,
                temperature=0.2,
                timeout_seconds=120,
            ),
            Purpose.PREP_SHEET: ModelConfig(
                model=settings.model_strong,
                max_output_tokens=2500,
                temperature=0.2,
                timeout_seconds=120,
            ),
        }

    def for_purpose(self, purpose: Purpose) -> ModelConfig:
        return self._configs[purpose]
