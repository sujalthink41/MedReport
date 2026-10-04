"""Tests for the strict-schema transform.

Written after a live call failed with a message that names only the first of three
incompatibilities::

    Invalid schema for response_format: 'additionalProperties' is required to be
    supplied and to be false.

These assert all three, so the next schema added to the project cannot quietly
reintroduce the problem.
"""

from pydantic import BaseModel, Field

from app.adapters.llm.strict_schema import to_strict


class Inner(BaseModel):
    name: str
    note: str | None = None


class Outer(BaseModel):
    rows: list[Inner]
    label: str | None = Field(default=None, description="optional")
    count: int = 0


def _every_object(node: object):  # type: ignore[no-untyped-def]
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            yield node
        for value in node.values():
            yield from _every_object(value)
    elif isinstance(node, list):
        for item in node:
            yield from _every_object(item)


class TestStrictness:
    def test_every_object_forbids_extra_properties(self) -> None:
        strict = to_strict(Outer.model_json_schema())

        objects = list(_every_object(strict))
        # Nested models live under $defs, and strict mode checks those too - the
        # root alone is not enough.
        assert len(objects) >= 2
        assert all(obj.get("additionalProperties") is False for obj in objects)

    def test_every_property_is_required(self) -> None:
        strict = to_strict(Outer.model_json_schema())

        for obj in _every_object(strict):
            assert set(obj.get("required", [])) == set(obj.get("properties", {}))

    def test_optional_fields_become_required_but_stay_nullable(self) -> None:
        strict = to_strict(Outer.model_json_schema())

        assert "label" in strict["required"]
        # Nothing is lost: optionality is carried by the nullable type, which is
        # how strict mode expects it to be expressed.
        any_of = strict["properties"]["label"]["anyOf"]
        assert {"type": "null"} in any_of

    def test_defaults_are_stripped(self) -> None:
        strict = to_strict(Outer.model_json_schema())

        # Strict mode rejects `default`, and it is redundant anyway: every field
        # is always returned, so there is nothing for a default to fill in.
        assert "default" not in strict["properties"]["count"]
        assert "default" not in strict["properties"]["label"]

    def test_nested_definitions_are_transformed(self) -> None:
        strict = to_strict(Outer.model_json_schema())

        inner = strict["$defs"]["Inner"]
        assert inner["additionalProperties"] is False
        assert set(inner["required"]) == {"name", "note"}

    def test_the_input_is_not_mutated(self) -> None:
        original = Outer.model_json_schema()
        before = dict(original)

        to_strict(original)

        # Pydantic caches model_json_schema(); mutating it in place would corrupt
        # the schema for every later call in the process.
        assert original == before
        assert "additionalProperties" not in original


class TestRealSchemas:
    def test_the_extraction_schema_is_acceptable(self) -> None:
        from app.adapters.llm.schemas import ExtractedPage

        strict = to_strict(ExtractedPage.model_json_schema())

        for obj in _every_object(strict):
            assert obj.get("additionalProperties") is False
            assert set(obj.get("required", [])) == set(obj.get("properties", {}))

    def test_the_mapping_schema_is_acceptable(self) -> None:
        from app.adapters.llm.schemas import ProposedMappings

        strict = to_strict(ProposedMappings.model_json_schema())

        for obj in _every_object(strict):
            assert obj.get("additionalProperties") is False
