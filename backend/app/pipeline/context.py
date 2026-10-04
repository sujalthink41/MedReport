"""The dependencies a node needs, carried outside the graph state.

LangGraph serialises state to a checkpoint between every node. A storage client,
a database session or an LLM client cannot be serialised, and putting them in
state would break checkpointing immediately.

So they travel in a ``ContextVar`` instead: set once when a run starts, read by
any node, never written to a checkpoint. The same mechanism the request id uses,
for the same reason — passing them through every node signature would make each
node's shape depend on what every other node happens to need.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from app.domain.ports.dictionary import DictionaryRepository
from app.domain.ports.llm import LLMClient
from app.domain.ports.services import Clock, FileStorage, IdGenerator


@dataclass(frozen=True)
class PipelineContext:
    storage: FileStorage
    llm: LLMClient
    dictionary: DictionaryRepository
    clock: Clock
    ids: IdGenerator
    report_id: UUID

    @staticmethod
    def current() -> "PipelineContext":
        context = _CURRENT.get()
        if context is None:
            # A node running outside a configured run is a wiring bug, and failing
            # loudly here beats a confusing AttributeError three frames deeper.
            raise RuntimeError("no PipelineContext is active for this run")
        return context

    def activate(self) -> object:
        return _CURRENT.set(self)

    @staticmethod
    def deactivate(token: object) -> None:
        _CURRENT.reset(token)  # type: ignore[arg-type]


_CURRENT: ContextVar["PipelineContext | None"] = ContextVar("pipeline_context", default=None)
