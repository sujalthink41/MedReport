"""Checking extraction against arithmetic the report provides about itself.

From ADR 0005: the model's self-reported confidence is not evidence — it came back
at 0.99-1.00 on every row of a scanned report, and rewriting the prompt moved it
to 0.98-0.99. Models are poorly calibrated at rating their own certainty.

This is what replaces it, and it is strictly better: **a lab report is full of
derived values, and a misread digit breaks the arithmetic.**

    differential % sums to 100        81.0 + 11.0 + 7.0 + 1.0 + 0.0 = 100.0
    absolute count = % x WBC          81.0% x 15.24 = 12.34
    MCHC = MCH / MCV x 100            30.1 / 88.6 x 100 = 34.0
    PCV  = RBC x MCV / 10             4.45 x 88.6 / 10  = 39.4

Real evidence, free, deterministic, and impossible for a model to fake. Eight
independent checks on one haematology page of the first real report tested, every
one reconciling.

Nothing clinical is hardcoded here. These are **arithmetic identities between
numbers printed on the same page** — the relationship between a percentage and a
count is not medical knowledge, it is multiplication.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation


@dataclass(frozen=True)
class CheckOutcome:
    name: str
    passed: bool
    expected: Decimal | None = None
    actual: Decimal | None = None
    involved: tuple[str, ...] = ()
    """Which rows this check read.

    A failure implicates all of them, and that is the point: the check says
    "something among these four is wrong", which is a far more useful starting
    place than a per-row confidence score that was never calibrated.
    """


@dataclass(frozen=True)
class Relationship:
    """One arithmetic identity between values on a page."""

    name: str
    needs: tuple[str, ...]
    compute: Callable[[dict[str, Decimal]], Decimal]
    target: str
    tolerance: Decimal
    """Absolute tolerance, because labs print rounded values.

    81.0% of 15.24 is 12.3444, printed as 12.34. A tolerance tight enough to
    catch a misread digit and loose enough to accept rounding is the whole
    design problem here.
    """


RELATIONSHIPS: list[Relationship] = [
    Relationship(
        name="differential sums to 100",
        needs=(
            "neutrophils_pct",
            "lymphocytes_pct",
            "monocytes_pct",
            "eosinophils_pct",
            "basophils_pct",
        ),
        compute=lambda v: sum(v.values(), Decimal(0)),
        target="__constant_100__",
        # Labs round each percentage independently, so five of them can drift a
        # little without anything being wrong.
        tolerance=Decimal("1.5"),
    ),
    *[
        Relationship(
            name=f"absolute {cell} = pct x WBC",
            needs=(f"{cell}_pct", "wbc"),
            compute=lambda v, c=cell: v[f"{c}_pct"] * v["wbc"] / 100,  # type: ignore[misc]
            target=f"{cell}_absolute",
            tolerance=Decimal("0.05"),
        )
        for cell in ("neutrophils", "lymphocytes", "monocytes", "eosinophils", "basophils")
    ],
    Relationship(
        name="MCHC = MCH / MCV x 100",
        needs=("mch", "mcv"),
        compute=lambda v: v["mch"] / v["mcv"] * 100,
        target="mchc",
        tolerance=Decimal("0.5"),
    ),
    Relationship(
        name="PCV = RBC x MCV / 10",
        needs=("rbc", "mcv"),
        compute=lambda v: v["rbc"] * v["mcv"] / 10,
        target="pcv",
        tolerance=Decimal("0.5"),
    ),
    Relationship(
        name="globulin = total protein - albumin",
        needs=("total_protein", "albumin"),
        compute=lambda v: v["total_protein"] - v["albumin"],
        target="globulin",
        tolerance=Decimal("0.1"),
    ),
    Relationship(
        name="A/G ratio = albumin / globulin",
        needs=("albumin", "globulin"),
        compute=lambda v: v["albumin"] / v["globulin"],
        target="ag_ratio",
        tolerance=Decimal("0.1"),
    ),
    Relationship(
        name="total bilirubin = direct + indirect",
        needs=("bilirubin_direct", "bilirubin_indirect"),
        compute=lambda v: v["bilirubin_direct"] + v["bilirubin_indirect"],
        target="bilirubin_total",
        tolerance=Decimal("0.05"),
    ),
]


def run_checks(values: dict[str, Decimal]) -> list[CheckOutcome]:
    """Apply every relationship whose inputs are present.

    A check with a missing input is skipped, not failed. A report that does not
    print MCHC cannot be wrong about it, and treating absence as failure would
    make the signal useless on the many reports that carry only a few panels.
    """
    outcomes: list[CheckOutcome] = []

    for relationship in RELATIONSHIPS:
        inputs = {key: values[key] for key in relationship.needs if key in values}
        if len(inputs) != len(relationship.needs):
            continue

        if relationship.target != "__constant_100__" and relationship.target not in values:
            continue

        # compute() inside the try, not outside it. A report with a globulin of
        # zero makes the A/G ratio check divide by zero, and the first version of
        # this caught only the comparison - so one odd row crashed verification
        # for the entire report.
        try:
            if relationship.target == "__constant_100__":
                expected, actual = Decimal(100), relationship.compute(inputs)
            else:
                expected = relationship.compute(inputs)
                actual = values[relationship.target]
            passed = abs(expected - actual) <= relationship.tolerance
        except (InvalidOperation, DivisionByZero, ZeroDivisionError, KeyError):
            # An uncomputable relationship is simply not evidence. Skipped, never
            # fatal: these checks exist to catch mistakes, not to create them.
            continue

        outcomes.append(
            CheckOutcome(
                name=relationship.name,
                passed=passed,
                expected=expected,
                actual=actual,
                involved=(*relationship.needs, relationship.target),
            )
        )

    return outcomes


def implicated(outcomes: list[CheckOutcome]) -> set[str]:
    """Keys involved in at least one failed check.

    These are the rows a human should look at. Note the asymmetry that makes this
    trustworthy: a passing check is strong evidence that every value it touched is
    right, while a failing one only narrows the suspects. Both are more useful
    than a number the model made up about itself.
    """
    suspect: set[str] = set()
    for outcome in outcomes:
        if not outcome.passed:
            suspect.update(outcome.involved)
    return suspect
