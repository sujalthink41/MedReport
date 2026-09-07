"""Tests for GoogleIdentity and the auth error mapping.

Written after two bugs got through that either of these would have caught in one
second. Worth remembering which test caught which.
"""

import pytest

from app.api.error_handlers import status_for
from app.domain.errors import DomainError, InvalidInputError, InvalidTokenError
from app.domain.ports.auth import GoogleIdentity


class TestGoogleIdentity:
    def test_a_valid_identity_is_accepted(self) -> None:
        # THE test people skip, and the one that mattered here.
        #
        # The first implementation had `if self.subject.strip():` - the condition
        # inverted - so it rejected every valid identity and accepted every empty
        # one. A "rejects bad input" test alone passes against that code. Only
        # asserting that the GOOD path works reveals it.
        identity = GoogleIdentity(subject="1092847", email="sujal@example.com", name="Sujal")

        assert identity.subject == "1092847"
        assert identity.email == "sujal@example.com"

    def test_a_name_is_optional(self) -> None:
        # Google does not always return one; a missing name must not block sign-in.
        assert GoogleIdentity(subject="1", email="a@b.com", name=None).name is None

    @pytest.mark.parametrize("subject", ["", "   ", "\t"])
    def test_an_empty_subject_is_refused(self, subject: str) -> None:
        # The dangerous case. subject is the identity key: two identities with ""
        # would match each other on lookup, and in this product that means one
        # person seeing another person's medical history.
        with pytest.raises(InvalidInputError):
            GoogleIdentity(subject=subject, email="a@b.com", name=None)

    @pytest.mark.parametrize("email", ["", "   "])
    def test_an_empty_email_is_refused(self, email: str) -> None:
        with pytest.raises(InvalidInputError):
            GoogleIdentity(subject="1", email=email, name=None)

    def test_errors_carry_fields_not_sentences(self) -> None:
        with pytest.raises(InvalidInputError) as caught:
            GoogleIdentity(subject="", email="a@b.com", name=None)

        # The second bug this file exists for: the first version called
        # InvalidInputError("subject cannot be None") positionally, and our base
        # class takes **context only - so it raised TypeError instead. Asserting on
        # the structured fields pins the calling convention down.
        assert caught.value.context == {"field": "subject", "reason": "empty"}

    def test_identities_are_immutable(self) -> None:
        identity = GoogleIdentity(subject="1", email="a@b.com", name=None)

        with pytest.raises(Exception):  # noqa: B017 - frozen dataclass
            identity.subject = "2"  # type: ignore[misc]


class TestInvalidTokenError:
    def test_a_bad_token_is_the_callers_fault(self) -> None:
        error = InvalidTokenError(reason="expired")

        # DomainError, so it maps to 4xx and is never retried. Classifying it as
        # infrastructure would make the retry decorator in CP17 keep re-sending an
        # expired token to reach the same answer.
        assert isinstance(error, DomainError)
        assert error.retryable is False

    def test_it_becomes_a_client_error_at_the_api_boundary(self) -> None:
        # Proves the CP2 hierarchy and the CP3 status map still line up: a new
        # error added today is routed correctly without touching STATUS_MAP,
        # because status_for() walks the MRO.
        assert status_for(InvalidTokenError()) < 500
