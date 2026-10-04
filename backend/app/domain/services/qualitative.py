"""Judging results that are words rather than numbers.

Found by running the pipeline on a real report: urine glucose came back
``DETECTED +++`` against a printed expectation of ``Negative``, and landed in the
"shown but not judged" pile alongside the colour of the sample.

That is a finding being silently dropped. And judging it needs **no medical
knowledge at all** — the lab printed what it expected in the reference column.
Comparing the result against the report's own stated expectation is the same move
the numeric path already makes.

The only knowledge here is **linguistic**: that "Not Detected", "Nil", "Absent"
and "Non Reactive" all mean the same thing as "Negative". That is vocabulary, not
clinical judgement, and getting it wrong is visible rather than silent.
"""

import re
from enum import StrEnum

# Ways a lab writes "nothing found". Linguistic synonyms, not a clinical claim -
# no entry here says what any of them means for a patient.
_NEGATIVE = frozenset(
    {
        "negative",
        "not detected",
        "notdetected",
        "nd",
        "nil",
        "none",
        "absent",
        "non reactive",
        "nonreactive",
        "not seen",
        "no growth",
        "normal",
    }
)

# Ways a lab writes "found". The +/++/+++ magnitude markers are stripped before
# matching, so "DETECTED +++" and "Detected" land in the same family - the
# magnitude is preserved separately rather than used for matching.
_POSITIVE = frozenset(
    {
        "positive",
        "detected",
        "present",
        "reactive",
        "seen",
        "growth",
    }
)

_MAGNITUDE = re.compile(r"[+]+$")
_PUNCTUATION = re.compile(r"[^a-z0-9 ]+")


class QualitativeVerdict(StrEnum):
    MATCHES = "matches"
    """The result is what the report said to expect."""

    DEVIATES = "deviates"
    """The result differs from the report's own stated expectation.

    Not a diagnosis - a deviation from what this laboratory printed as normal
    for this test. That is exactly the claim the numeric path makes too.
    """

    UNDECIDABLE = "undecidable"
    """No expectation printed, or neither side is a recognisable term.

    Shown to the user with its text, never guessed at.
    """


def normalize_term(text: str | None) -> str:
    """Reduce a printed term to a comparable form."""
    if not text:
        return ""
    lowered = text.strip().lower()
    lowered = _MAGNITUDE.sub("", lowered).strip()
    lowered = _PUNCTUATION.sub(" ", lowered)
    return " ".join(lowered.split())


def magnitude_of(text: str | None) -> int:
    """How many plus signs the lab printed.

    ``DETECTED +++`` is a stronger finding than ``DETECTED +``. Kept so the
    explanation can say so, and deliberately NOT used for matching - a result is
    either what was expected or it is not.
    """
    if not text:
        return 0
    found = _MAGNITUDE.search(text.strip())
    return len(found.group()) if found else 0


def _family(term: str) -> str | None:
    if term in _NEGATIVE:
        return "negative"
    if term in _POSITIVE:
        return "positive"
    # "no growth in culture after 2 days of aerobic incubation at 37c" - labs
    # write sentences. Match on the opening phrase rather than demanding an
    # exact term.
    for candidate in _NEGATIVE:
        if term.startswith(candidate):
            return "negative"
    for candidate in _POSITIVE:
        if term.startswith(candidate):
            return "positive"
    return None


def compare(result: str | None, expectation: str | None) -> QualitativeVerdict:
    """Judge a word result against the report's printed expectation."""
    expected_term = normalize_term(expectation)
    actual_term = normalize_term(result)

    if not expected_term or not actual_term:
        return QualitativeVerdict.UNDECIDABLE

    if expected_term == actual_term:
        return QualitativeVerdict.MATCHES

    expected_family = _family(expected_term)
    actual_family = _family(actual_term)

    if expected_family is None or actual_family is None:
        # One side is a term we do not recognise - a colour, a descriptive
        # phrase. Undecidable beats a guess: showing "CLEAR" as a deviation from
        # "Clear-slightly hazy" would be noise, and the opposite would be a
        # missed finding.
        return QualitativeVerdict.UNDECIDABLE

    return (
        QualitativeVerdict.MATCHES
        if expected_family == actual_family
        else QualitativeVerdict.DEVIATES
    )
