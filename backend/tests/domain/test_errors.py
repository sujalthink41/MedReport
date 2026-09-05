"""Tests for the exception hierarchy.

Note what these need: nothing. No database, no app, no network. This is what a pure
domain test looks like, and every one of them runs in microseconds.
"""

import pytest

from app.domain.errors import (
    DomainError,
    DuplicateReportError,
    InfrastructureError,
    LLMInvalidOutputError,
    LLMUnavailableError,
    MedReportError,
    PermissionDeniedError,
    ProfileNotFoundError,
    StorageUnavailableError,
    UnsupportedFileTypeError,
)


class TestErrorContext:
    def test_context_is_kept_as_structured_data(self) -> None:
        error = ProfileNotFoundError(profile_id="abc-123", requested_by="user-9")

        # The point of the whole design: fields survive as data, not as a string
        # someone has to parse back out later.
        assert error.context == {"profile_id": "abc-123", "requested_by": "user-9"}

    def test_message_is_readable_and_deterministic(self) -> None:
        error = ProfileNotFoundError(profile_id="abc-123", requested_by="user-9")

        # Sorted keys: identical errors produce identical messages, so log
        # aggregation groups them instead of showing N variants of one problem.
        assert str(error) == "profile_not_found(profile_id='abc-123', requested_by='user-9')"

    def test_bare_error_degrades_to_its_code(self) -> None:
        assert str(PermissionDeniedError()) == "permission_denied"


class TestFaultAttribution:
    """The split that drives both HTTP status (CP3) and retry policy (CP17)."""

    @pytest.mark.parametrize(
        "error",
        [
            ProfileNotFoundError(),
            DuplicateReportError(),
            UnsupportedFileTypeError(),
            PermissionDeniedError(),
        ],
    )
    def test_caller_faults_are_domain_errors(self, error: MedReportError) -> None:
        assert isinstance(error, DomainError)
        assert not isinstance(error, InfrastructureError)

    @pytest.mark.parametrize("error", [StorageUnavailableError(), LLMUnavailableError()])
    def test_our_faults_are_infrastructure_errors(self, error: MedReportError) -> None:
        assert isinstance(error, InfrastructureError)
        assert not isinstance(error, DomainError)


class TestRetryability:
    @pytest.mark.parametrize(
        "error", [ProfileNotFoundError(), DuplicateReportError(), UnsupportedFileTypeError()]
    )
    def test_domain_errors_are_never_retryable(self, error: MedReportError) -> None:
        # Retrying these burns time and money to reach the identical answer.
        assert error.retryable is False

    @pytest.mark.parametrize("error", [StorageUnavailableError(), LLMUnavailableError()])
    def test_transient_failures_are_retryable(self, error: MedReportError) -> None:
        assert error.retryable is True

    def test_invalid_model_output_is_infrastructure_but_not_retryable(self) -> None:
        error = LLMInvalidOutputError(node="extract", page=3)

        assert isinstance(error, InfrastructureError)
        # The exception that proves the flag is per-class rather than per-category:
        # the same prompt will produce the same malformed answer. Repair, don't retry.
        assert error.retryable is False


class TestHierarchyIsCatchable:
    def test_everything_deliberate_shares_one_root(self) -> None:
        with pytest.raises(MedReportError):
            raise LLMUnavailableError(provider="openai")

    def test_genuine_bugs_are_not_swallowed(self) -> None:
        # Catching MedReportError must NOT catch a TypeError. A bug should crash
        # loudly rather than be quietly handled as "an error we expected".
        with pytest.raises(TypeError):
            try:
                raise TypeError("a real bug")
            except MedReportError:  # pragma: no cover - must not match
                pytest.fail("MedReportError caught a genuine bug")
