"""Nodes register themselves.

Open/Closed, applied to the pipeline. Adding a node is a decorator on a function;
the graph builder reads this registry and never grows an ``if`` chain.

That matters more here than it sounds. The alternative - a builder that imports
and wires each node by hand - means every new stage edits the one file that
already works, and the blast radius of adding `explain` includes `extract`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.pipeline.state import GraphState

NodeFn = Callable[[GraphState], Awaitable[dict[str, object]]]


@dataclass(frozen=True)
class NodeSpec:
    name: str
    fn: NodeFn
    after: str | None
    """The node this one follows. ``None`` means it is the entry point."""

    fan_out: bool = False
    """True if this node runs once per page rather than once per report."""

    optional: bool = field(default=False)
    """If True, a failure here degrades the report to `partial` rather than
    failing it. Extraction is not optional; an explanation is."""


_REGISTRY: dict[str, NodeSpec] = {}


def register_node(
    name: str, *, after: str | None = None, fan_out: bool = False, optional: bool = False
) -> Callable[[NodeFn], NodeFn]:
    """Declare a pipeline stage.

    Used as::

        @register_node("extract", after="ingest", fan_out=True)
        async def extract(state: GraphState) -> dict[str, object]: ...
    """

    def decorator(fn: NodeFn) -> NodeFn:
        if name in _REGISTRY:
            # Two nodes with one name would make the graph's shape depend on
            # import order, which is the kind of bug that only appears in
            # production under a different entry point.
            raise ValueError(f"node {name!r} is already registered")
        _REGISTRY[name] = NodeSpec(
            name=name, fn=fn, after=after, fan_out=fan_out, optional=optional
        )
        return fn

    return decorator


def registered() -> dict[str, NodeSpec]:
    return dict(_REGISTRY)


def clear_registry() -> None:
    """For tests only. Module-level state needs a reset hook or tests leak into
    each other."""
    _REGISTRY.clear()


def ordered() -> list[NodeSpec]:
    """Nodes in execution order, derived from their ``after`` declarations.

    A topological walk rather than a hardcoded list, so the order is a property of
    the nodes themselves. Reordering the pipeline means changing one decorator,
    not editing a sequence somewhere else that can drift out of agreement.
    """
    specs = registered()
    entry = [s for s in specs.values() if s.after is None]
    if len(entry) != 1:
        raise ValueError(f"expected exactly one entry node, found {len(entry)}")

    chain: list[NodeSpec] = [entry[0]]
    seen = {entry[0].name}

    while True:
        following = [s for s in specs.values() if s.after == chain[-1].name]
        if not following:
            break
        if len(following) > 1:
            raise ValueError(
                f"{len(following)} nodes follow {chain[-1].name!r}; the pipeline is a chain"
            )
        nxt = following[0]
        if nxt.name in seen:
            raise ValueError(f"cycle in pipeline at {nxt.name!r}")
        chain.append(nxt)
        seen.add(nxt.name)

    orphans = set(specs) - seen
    if orphans:
        # A node nobody follows is dead code that looks alive. Far better to fail
        # at startup than to spend a week wondering why `verify` never runs.
        raise ValueError(f"unreachable nodes: {sorted(orphans)}")

    return chain
