"""The background work port.

A use case needs to say "process this report later" without knowing that later
means Celery. Swapping to RQ, to SQS, or to an in-process executor for tests then
touches one adapter.
"""

from typing import Protocol


class TaskQueue(Protocol):
    async def enqueue(self, task: str, **kwargs: str) -> None:
        """Hand work to a background worker.

        Arguments are **strings only**, deliberately. A queue message is serialised
        and may sit for minutes, so it must carry identifiers rather than objects:

        * a domain object would need serialising, and would be stale by the time it
          ran
        * a database session or file handle cannot cross a process boundary at all

        The worker re-reads whatever it needs from the identifier, which also means
        a replayed message picks up the current state instead of resurrecting old
        data.
        """
        ...
