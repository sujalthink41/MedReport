"""Ports: what the domain needs from the outside world.

A port is a description of a *need*, written by the consumer, naming no supplier.
Read each one as a sentence: "I need something that can store bytes at a key."

Two things to notice throughout:

**They speak our language, not a vendor's.** ``FileStorage`` says ``key`` and
``bytes``, never ``Bucket`` or ``ACL``. A port phrased in S3's vocabulary is not an
abstraction - it is S3 wearing a hat, and swapping providers would still break
everything.

**They are ``Protocol``, not ``ABC``.** An adapter does not inherit from these; it
just has to have the right shape. So ``adapters/`` never imports ``domain/``, and
the dependency arrow stays pointing inward.
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID


class Clock(Protocol):
    """The current time, as a dependency.

    This looks like over-engineering until you try to test the trend rule: "flag a
    value that moved 25% over six months". With ``datetime.now()`` hardcoded you
    either wait six months or monkey-patch the standard library - so in practice the
    rule never gets tested, and the product's most differentiating feature becomes
    its least verified code.

    With a port you inject a fake clock, jump to any date, and assert. Milliseconds.

    The general rule: **anything non-deterministic - time, randomness, ids, the
    network - enters through a port.** Untestable code is a design problem showing
    up late, not a testing problem.
    """

    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    """New identifiers, as a dependency.

    Same reasoning as ``Clock``. A test that can predict the id it is about to create
    is far easier to write than one that has to fish the value back out afterwards.
    """

    def new_id(self) -> UUID: ...


class FileStorage(Protocol):
    """Somewhere to keep the original uploaded document.

    Satisfied by Cloudflare R2 in production and by local disk in development and
    tests (CP10). Both must behave *identically* - same errors, same semantics for a
    missing key. An adapter that only mostly matches is worse than none, because the
    difference will surface in production rather than in the test suite.
    """

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def signed_url(self, key: str, ttl_seconds: int) -> str:
        """A short-lived URL for the client to fetch the file directly.

        Short-lived and signed because these are medical documents: a permanent or
        guessable URL is a data breach with extra steps.
        """
        ...
