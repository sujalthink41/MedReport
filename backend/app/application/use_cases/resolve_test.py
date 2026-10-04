"""Turn a printed test name into a stable marker id.

The sequence that makes the dictionary self-populating:

    look up what we already know
      hit  -> use it                               (stable, cheap, no model call)
      miss -> was a proposal supplied by the model?
                yes -> store it, use it            (the dictionary just grew)
                no  -> record the gap, stay unmapped

An unmapped row is **not** an error. It is shown to the user with its value and
printed range; we simply cannot trend or judge it. Dropping it would hide the
product's own blind spots from everyone including us.
"""

from dataclasses import dataclass

from app.core.logging import get_logger
from app.domain.models.clinical import ProposedTest, Resolution
from app.domain.ports.dictionary import DictionaryRepository
from app.domain.ports.services import Clock
from app.domain.services.test_names import alias_keys

log = get_logger(__name__)


@dataclass(frozen=True)
class ResolveOutcome:
    resolution: Resolution
    was_learned: bool = False
    """True when this call added the marker to the dictionary."""


class ResolveTest:
    def __init__(self, *, dictionary: DictionaryRepository, clock: Clock) -> None:
        self._dictionary = dictionary
        self._clock = clock

    async def execute(
        self, raw_name: str, *, proposal: ProposedTest | None = None
    ) -> ResolveOutcome:
        keys = alias_keys(raw_name)
        if not keys:
            return ResolveOutcome(Resolution(canonical_test_id=None))

        existing = await self._dictionary.resolve(keys)
        if existing is not None:
            # The whole reason the dictionary exists: whatever was decided the
            # first time is what gets decided every time, so a trend stays one line.
            canonical_id, matched = existing
            return ResolveOutcome(Resolution(canonical_test_id=canonical_id, matched_key=matched))

        now = self._clock.now()

        if proposal is None:
            await self._dictionary.record_unmapped(key=keys[0], raw_name=raw_name, at=now)
            log.info("test_unmapped", key=keys[0])
            return ResolveOutcome(Resolution(canonical_test_id=None))

        canonical_id = await self._dictionary.record_proposal(
            proposal, raw_name=raw_name, keys=keys, at=now
        )
        log.info("test_learned", canonical_id=canonical_id, key=keys[0])
        return ResolveOutcome(
            Resolution(canonical_test_id=canonical_id, matched_key=keys[0]),
            was_learned=True,
        )
