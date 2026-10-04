# ADR 0005 — Verify extraction with arithmetic, not with self-reported confidence

**Status:** accepted · **Date:** 2026-10-04

## Context

Every extracted row carries a `confidence` field, and the eval harness's safety
metric — `false_confidence`, meaning *wrong while claiming to be sure* — depends on
that number being meaningful.

It is not.

Running extraction over a real six-page scanned report produced `confidence` values
between **0.99 and 1.00 on every one of 51 rows**, on a document with no text layer
where every character came from pixels.

The prompt was then rewritten to anchor confidence behaviourally: 1.0 reserved for
text-layer-backed characters, 0.75 for anything resolved by context, explicit
warnings about decimal points and long numbers, and an instruction that uniform
confidence across a page means nothing was assessed.

The result moved from 0.99–1.00 to **0.98–0.99**. Still two distinct values across
21 rows. Still nothing below 0.9 on a scan.

This matches what is known generally: language models are poorly calibrated when
asked to rate their own certainty, and prompt wording does not fix it. Continuing
to tune the wording would be optimising a signal that does not exist.

## Decision

**Stop treating the model's `confidence` as evidence.** Keep the field — a very low
value is still worth noticing — but never gate anything on a high one.

Derive uncertainty from things we can observe instead, in priority order:

### 1. Arithmetic the report provides about itself

Lab reports are full of derived values, and a misread digit breaks the arithmetic.
From the first real report tested:

```
differential % sums to 100          81.0 + 11.0 + 7.0 + 1.0 + 0.0 = 100.0   OK
absolute count = % x WBC            81.0% x 15.24 = 12.34 vs 12.34          OK
MCHC = MCH / MCV x 100              30.1 / 88.6 x 100 = 34.0 vs 34.0        OK
PCV  = RBC x MCV / 10               4.45 x 88.6 / 10  = 39.4 vs 39.4        OK
```

Eight independent checks on one page, every one reconciling. This is **real
evidence**, it is free, and it is deterministic code rather than an opinion.

### 2. Self-consistency across two extractions

Extract a page twice and compare. Rows that differ between runs are genuinely
uncertain; rows that agree are stable. Measured, not self-reported. Costs one extra
call per page, so applied to pages where arithmetic checks are unavailable.

### 3. Plausibility against the row's own printed range

A value orders of magnitude outside the interval printed beside it is usually a
decimal error, not a dying patient.

### 4. Absence of a text layer

A property of the document, not of the model's mood. Every digit on a scanned page
came from pixels, and the whole page should be treated more sceptically.

## Consequences

- The `verify` node in CP20 becomes mostly deterministic arithmetic, with a model
  call reserved for rows that cannot be cross-checked. Cheaper and stronger than
  the agentic re-reading loop originally planned.
- The `false_confidence` eval metric must be computed from *verification outcome*,
  not from the model's self-rating, or it measures nothing.
- A library of intra-report relationships is now a product asset, alongside the
  dictionary. Like the dictionary, it can be learned: a relationship that holds
  across many reports is a real one.
- Nothing may be gated on high self-reported confidence. Low confidence may still
  be surfaced for review — being told "I am unsure" is informative even when "I am
  sure" is not.

## What this cost to learn

Two prompt versions and about five cents of real extraction. Worth noting that no
unit test could have found it: the fake LLM returns whatever the test told it to,
so calibration is invisible until a real model reads a real document.
