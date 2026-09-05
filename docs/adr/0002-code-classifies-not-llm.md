# ADR 0002 — Deterministic code decides the band, not the LLM

**Status:** accepted · **Date:** 2026-09-05

## Context

We use an LLM to read lab reports. It would be simpler to also let it judge whether
each value is normal, borderline or out of range — the model has strong medical
knowledge and can handle report types we never anticipated.

## Decision

The LLM extracts, explains and reasons. **Deterministic code performs every
comparison of a value against a range**, unit conversion, and trend computation.

## Rationale

- **Consistency.** Ask a model twice whether 6.1 is borderline and you can get two
  answers. Same report, two verdicts, destroys trust permanently in a health product.
- **Testability.** Band logic can be unit-tested. Model judgment cannot.
- **Coverage was never the problem.** The concern that we cannot hardcode ranges for
  every possible test does not apply: *the report prints its own reference range*.
  The model reads it. Our curated table only covers the ~30 markers where clinical
  guidelines disagree with the printed range.
- Comparing a number to a range is something computers have done perfectly since 1970.

## Consequences

- Requires unit normalization to be correct before classification — non-negotiable
- Requires the canonical test dictionary as a first-class, maintained asset
- Unmapped tests must degrade gracefully to `band=unknown` rather than guessing
- The model is still fully used where it is genuinely better: reading arbitrary
  layouts, mapping synonyms, and cross-marker pattern reasoning
