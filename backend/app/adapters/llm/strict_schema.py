"""Making a Pydantic schema acceptable to strict structured output.

Pydantic and OpenAI's strict mode disagree about three things, and the error you
get says so only for the first::

    Invalid schema for response_format: 'additionalProperties' is required to be
    supplied and to be false.

The disagreements:

1. **``additionalProperties: false`` on every object.** Pydantic omits it; strict
   mode requires it, everywhere, including nested objects and ``$defs``.

2. **Every property must appear in ``required``.** Pydantic lists only fields
   without defaults. Strict mode expects all of them, and expresses optionality
   through *nullable types* instead — which our schemas already use
   (``str | None``), so nothing is lost.

3. **``default`` is not supported.** Pydantic emits it for any field with one.

Worth doing as a transform rather than by constraining how schemas are written.
Telling every future schema author to remember ``extra="forbid"`` and never use a
default is a rule that gets forgotten; this cannot be.
"""

from typing import Any

_UNSUPPORTED_KEYS = ("default", "$comment")


def to_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a JSON schema that strict structured output accepts."""
    transformed: dict[str, Any] = _walk(schema)
    return transformed


def _walk(node: Any) -> Any:
    if isinstance(node, list):
        return [_walk(item) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _UNSUPPORTED_KEYS:
            # Dropping a default is safe: strict mode always returns every field,
            # so there is nothing for a default to fill in.
            continue
        out[key] = _walk(value)

    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
        properties = out.get("properties")
        if isinstance(properties, dict):
            # ALL of them, not just the ones Pydantic marked required. Optionality
            # is carried by the nullable types already in the schema.
            out["required"] = list(properties.keys())

    return out
