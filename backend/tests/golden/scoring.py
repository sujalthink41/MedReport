"""Scoring extraction against hand-labelled truth.

Scored, never pass/fail. A single accuracy number hides which *kind* of mistake
got worse, and the kinds are not equally bad:

* a row we never found is invisible to everyone, including us
* a row we read wrongly but flagged as uncertain costs a human glance
* a row we read wrongly and reported confidently is the one that harms someone

So the metrics are reported separately, and ``false_confidence`` is the one to
watch. A prompt change that lifts overall accuracy while raising false confidence
has made the product worse.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

CONFIDENT = 0.7
"""At or above this, we present a value without hedging. The threshold that makes
false confidence meaningful."""


@dataclass(frozen=True)
class TruthRow:
    """One hand-typed row of ground truth."""

    raw_test_name: str
    value_text: str | None
    unit: str | None
    ref_low: str | None = None
    ref_high: str | None = None


@dataclass
class Scorecard:
    expected_rows: int = 0
    found_rows: int = 0
    matched_rows: int = 0

    value_correct: int = 0
    unit_correct: int = 0
    range_correct: int = 0

    confidently_wrong: int = 0
    uncertainly_wrong: int = 0

    missed: list[str] = field(default_factory=list)
    spurious: list[str] = field(default_factory=list)
    wrong_values: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def row_recall(self) -> float:
        """Did we find every row? The metric that matters most.

        A value we read wrongly is at least visible and reviewable. A row we never
        emitted is gone - nobody knows to look for it, and the user's report is
        silently incomplete.
        """
        return self.matched_rows / self.expected_rows if self.expected_rows else 0.0

    @property
    def value_accuracy(self) -> float:
        return self.value_correct / self.matched_rows if self.matched_rows else 0.0

    @property
    def unit_accuracy(self) -> float:
        return self.unit_correct / self.matched_rows if self.matched_rows else 0.0

    @property
    def range_accuracy(self) -> float:
        return self.range_correct / self.matched_rows if self.matched_rows else 0.0

    @property
    def false_confidence(self) -> float:
        """Rows we got wrong while claiming to be sure. **The dangerous number.**

        Everything else is a quality metric. This one is a safety metric: it counts
        the cases where a user is shown a wrong value about their own body with no
        indication that anything is uncertain.

        A prompt change that improves accuracy and raises this has made the
        product worse, not better.
        """
        wrong = self.confidently_wrong + self.uncertainly_wrong
        return self.confidently_wrong / wrong if wrong else 0.0

    @property
    def spurious_rate(self) -> float:
        """Rows we invented that are not on the page."""
        return len(self.spurious) / self.found_rows if self.found_rows else 0.0


def normalise_name(name: str) -> str:
    """Match rows by name, loosely.

    Deliberately forgiving: we are measuring whether the right ROW was found, not
    whether its name was transcribed character-perfectly. A stricter match would
    report a recall failure for a trailing space, which would hide the real
    misses behind noise.
    """
    from app.domain.services.test_names import normalize

    return normalize(name)


def values_match(expected: str | None, actual: str | None) -> bool:
    """Compare two printed values.

    Numeric where both parse - so "11.20" equals "11.2" - and exact-ignoring-case
    otherwise, because "Negative" and "negative" are the same result while
    "Negative" and "Not Detected" are not ours to equate.
    """
    if expected is None or actual is None:
        return expected == actual

    expected, actual = expected.strip(), actual.strip()
    try:
        return Decimal(expected) == Decimal(actual)
    except (InvalidOperation, ValueError):
        return expected.casefold() == actual.casefold()


def score_page(truth: list[TruthRow], extracted: list[object]) -> Scorecard:
    """Compare one page of extraction against its hand-labelled truth."""
    card = Scorecard(expected_rows=len(truth), found_rows=len(extracted))

    by_name: dict[str, object] = {}
    for row in extracted:
        by_name.setdefault(normalise_name(getattr(row, "raw_test_name", "")), row)

    seen: set[str] = set()

    for want in truth:
        key = normalise_name(want.raw_test_name)
        got = by_name.get(key)
        if got is None:
            card.missed.append(want.raw_test_name)
            continue

        seen.add(key)
        card.matched_rows += 1

        actual_value = getattr(got, "value_text", None)
        confidence = float(getattr(got, "confidence", 0.0))

        if values_match(want.value_text, actual_value):
            card.value_correct += 1
        else:
            card.wrong_values.append((want.raw_test_name, str(want.value_text), str(actual_value)))
            # The split that makes false_confidence meaningful.
            if confidence >= CONFIDENT:
                card.confidently_wrong += 1
            else:
                card.uncertainly_wrong += 1

        if _same(want.unit, getattr(got, "unit", None)):
            card.unit_correct += 1

        if _range_matches(want, got):
            card.range_correct += 1

    for key, row in by_name.items():
        if key not in seen:
            # A row on nobody's page. Either the truth file is incomplete or the
            # model invented something - both worth seeing, and the difference is
            # obvious once you look.
            card.spurious.append(str(getattr(row, "raw_test_name", "?")))

    return card


def _same(expected: str | None, actual: str | None) -> bool:
    if expected is None or actual is None:
        return expected == actual
    return expected.strip().casefold() == actual.strip().casefold()


def _range_matches(want: TruthRow, got: object) -> bool:
    """Both bounds, including when the truth is 'no range printed'.

    Getting this right matters as much as getting a bound right: a model that
    invents a range where the lab printed none is fabricating the thing we judge
    people against.
    """
    for expected, attr in ((want.ref_low, "ref_low"), (want.ref_high, "ref_high")):
        actual = getattr(got, attr, None)
        if expected is None:
            if actual is not None:
                return False
            continue
        if actual is None:
            return False
        try:
            if Decimal(str(expected)) != Decimal(str(actual)):
                return False
        except (InvalidOperation, ValueError):
            return False
    return True


def combine(cards: list[Scorecard]) -> Scorecard:
    """Totals across the whole golden set."""
    total = Scorecard()
    for card in cards:
        total.expected_rows += card.expected_rows
        total.found_rows += card.found_rows
        total.matched_rows += card.matched_rows
        total.value_correct += card.value_correct
        total.unit_correct += card.unit_correct
        total.range_correct += card.range_correct
        total.confidently_wrong += card.confidently_wrong
        total.uncertainly_wrong += card.uncertainly_wrong
        total.missed.extend(card.missed)
        total.spurious.extend(card.spurious)
        total.wrong_values.extend(card.wrong_values)
    return total


def report(card: Scorecard) -> str:
    lines = [
        "",
        "  EXTRACTION SCORECARD",
        "  " + "-" * 52,
        f"  rows expected        {card.expected_rows}",
        f"  rows found           {card.found_rows}",
        "",
        f"  row recall           {card.row_recall:>7.1%}   <- matters most",
        f"  value accuracy       {card.value_accuracy:>7.1%}",
        f"  unit accuracy        {card.unit_accuracy:>7.1%}",
        f"  range accuracy       {card.range_accuracy:>7.1%}",
        f"  spurious rows        {card.spurious_rate:>7.1%}",
        "",
        f"  FALSE CONFIDENCE     {card.false_confidence:>7.1%}   <- the dangerous one",
        f"    wrong + confident  {card.confidently_wrong}",
        f"    wrong + uncertain  {card.uncertainly_wrong}",
    ]
    if card.missed:
        lines += ["", "  MISSED ROWS (invisible to everyone):"]
        lines += [f"    - {name}" for name in card.missed[:15]]
    if card.wrong_values:
        lines += ["", "  WRONG VALUES (expected -> got):"]
        lines += [f"    - {n}: {e} -> {a}" for n, e, a in card.wrong_values[:15]]
    if card.spurious:
        lines += ["", "  SPURIOUS ROWS (not on the page):"]
        lines += [f"    - {name}" for name in card.spurious[:10]]
    return "\n".join(lines) + "\n"
