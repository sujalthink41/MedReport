"""Tests for structured logging and PHI redaction.

The redaction tests matter more than they look. This is a health product: a lab value
in an application log is a data leak that survives in log storage, gets shipped to an
aggregator, and is visible to anyone with read access to observability tooling.

So redaction is tested like a security control, not like a formatting preference.
"""

import io
import json
import logging

import structlog

from app.core.config import Environment, Settings
from app.core.logging import (
    REDACTED,
    bind_context,
    clear_context,
    configure_logging,
    redact_sensitive,
)


def _redact(**fields: object) -> dict[str, object]:
    return dict(redact_sensitive(None, "info", dict(fields)))  # type: ignore[arg-type]


class TestRedaction:
    def test_lab_values_never_survive(self) -> None:
        result = _redact(event="observation_classified", test="hba1c", value=6.1)

        assert result["value"] == REDACTED
        # Identifiers must pass through, or the log becomes useless for debugging.
        assert result["test"] == "hba1c"
        assert result["event"] == "observation_classified"

    def test_identity_fields_never_survive(self) -> None:
        result = _redact(email="a@b.com", display_name="Amma", date_of_birth="1962-04-11")

        assert result["email"] == REDACTED
        assert result["display_name"] == REDACTED
        assert result["date_of_birth"] == REDACTED

    def test_model_traffic_never_survives(self) -> None:
        result = _redact(prompt="Read this report...", response="{...}")

        assert result["prompt"] == REDACTED
        assert result["response"] == REDACTED

    def test_secrets_never_survive(self) -> None:
        result = _redact(api_key="sk-live-123", authorization="Bearer x")

        assert result["api_key"] == REDACTED
        assert result["authorization"] == REDACTED

    def test_phi_cannot_hide_one_level_down(self) -> None:
        # The obvious bypass: nest it. A shallow implementation would leak here.
        result = _redact(observation_row={"test": "ferritin", "value": 8.2})

        # Note it scrubs *into* the structure rather than dropping the whole thing.
        # Redacting the container would be safe but blind — you would lose the test
        # name too, and with it any ability to debug. Precision beats a blunt hammer.
        assert result["observation_row"] == {"test": "ferritin", "value": REDACTED}

    def test_phi_cannot_hide_inside_a_list_of_dicts(self) -> None:
        result = _redact(rows=[{"test": "alt", "value": 55}])

        assert result["rows"] == [{"test": "alt", "value": REDACTED}]
        assert "55" not in str(result)

    def test_redaction_is_case_insensitive(self) -> None:
        assert _redact(Email="a@b.com")["Email"] == REDACTED

    def test_deeply_nested_structure_terminates(self) -> None:
        # Logging must never be the thing that hangs a request.
        deep: dict[str, object] = {"a": {"b": {"c": {"d": {"e": "x"}}}}}

        assert _redact(payload=deep) is not None

    def test_identifiers_are_deliberately_not_redacted(self) -> None:
        # We log ids and read actual content from `llm_traces` (CP16) instead.
        result = _redact(report_id="r-1", profile_id="p-1", node="extract", band="normal")

        assert result == {
            "report_id": "r-1",
            "profile_id": "p-1",
            "node": "extract",
            "band": "normal",
        }


class TestLogOutput:
    def _capture(self, settings: Settings) -> io.StringIO:
        configure_logging(settings)
        stream = io.StringIO()
        logging.getLogger().handlers[0].setStream(stream)  # type: ignore[attr-defined]
        return stream

    def test_json_output_is_machine_readable(self) -> None:
        stream = self._capture(Settings(environment=Environment.PRODUCTION))

        structlog.get_logger("test").info("report_queued", report_id="r-1")

        record = json.loads(stream.getvalue())
        assert record["event"] == "report_queued"
        assert record["report_id"] == "r-1"
        assert record["level"] == "info"
        assert "timestamp" in record

    def test_redaction_applies_to_real_output_not_just_the_processor(self) -> None:
        stream = self._capture(Settings(environment=Environment.PRODUCTION))

        structlog.get_logger("test").info("observation", test="hba1c", value=6.1)

        record = json.loads(stream.getvalue())
        assert record["value"] == REDACTED
        assert "6.1" not in stream.getvalue()

    def test_bound_context_reaches_every_line(self) -> None:
        stream = self._capture(Settings(environment=Environment.PRODUCTION))
        clear_context()
        bind_context(request_id="req-42")

        # Note: this call never mentions request_id. That is the whole point —
        # code deep in the stack does not need to know a request exists.
        structlog.get_logger("test").info("something_happened")

        assert json.loads(stream.getvalue())["request_id"] == "req-42"
        clear_context()

    def test_context_can_be_cleared_between_requests(self) -> None:
        stream = self._capture(Settings(environment=Environment.PRODUCTION))
        bind_context(request_id="req-1")
        clear_context()

        structlog.get_logger("test").info("next_request")

        # Workers reuse event loops; leaked context means one report's id appears
        # in another report's logs.
        assert "request_id" not in json.loads(stream.getvalue())


class TestLogFormatSelection:
    def test_local_uses_human_readable_output(self) -> None:
        assert Settings(environment=Environment.LOCAL).use_json_logs is False

    def test_deployed_environments_use_json(self) -> None:
        assert Settings(environment=Environment.PRODUCTION).use_json_logs is True
        assert Settings(environment=Environment.STAGING).use_json_logs is True

    def test_explicit_override_wins(self) -> None:
        settings = Settings(environment=Environment.LOCAL, log_json=True)

        assert settings.use_json_logs is True
