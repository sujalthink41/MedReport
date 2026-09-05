# Classification Rules

> **Code decides the band. The LLM never does.**

Same report uploaded twice must produce the same verdict, every time.

## Three layers, in priority order

### Layer 1 — the lab's own printed reference range (primary)

Every report prints its reference interval next to the value. **Use it.**

Ranges legitimately differ between labs because of different machines and methods —
the same haemoglobin can be "normal" at one lab and "low" at another, and the lab's
own range is the correct answer *for that report*.

Never invent a range when the report provides one.

### Layer 2 — fallback table (range missing or photo cropped)

Curated internal table keyed by `canonical_test_id + age_band + sex + unit`.

Age and sex matter a great deal — haemoglobin, creatinine and ferritin all have
materially different ranges for men and women.

When Layer 2 is used, **mark the result** so the UI can say the range came from a
general reference, not from this lab.

### Layer 3 — clinical guideline thresholds (the differentiator)

For a curated set of high-value markers, the reference range is **not** the number
that matters. Here we override.

| Marker | Lab range says | What actually matters |
|---|---|---|
| HbA1c | "up to 5.9 normal" | 5.7–6.4 prediabetes, ≥6.5 diabetes (ADA) |
| LDL | varies wildly | no true normal range — target depends on risk profile |
| Vitamin D | varies wildly | <20 deficient, 20–30 insufficient |
| TSH | wide range | pregnancy and age change the target |

**This layer is the reason we're better than a PDF viewer.** It is a hand-curated
asset covering roughly 30 markers. Each entry cites its guideline source.

## Prerequisite: unit normalization

Convert to canonical units **before any comparison**. `mg/dL` vs `mmol/L`,
`ng/mL` vs `nmol/L`. Every comparison downstream assumes this has happened.

## Band logic

| Band | Rule |
|---|---|
| `normal` | Inside range, not near either edge |
| `borderline` | Inside range but within ~10% of an edge — the "watch this" zone |
| `out_of_range` | Outside the range, high or low |
| `needs_attention` | Past a hard threshold on the curated critical-values list |

Position within range is computed as a normalized 0–1 value, so the UI can show
*where* in the range something sits, not just which band it's in.

## Special cases

**One-sided markers.** LDL, triglycerides, ESR have only an upper bound.
Do not apply "low" logic to them. The dictionary marks each test as
`two_sided | upper_only | lower_only`.

**Non-numeric results.** `Negative`, `Positive`, `Not Detected`, titres like `1:40`.
These have their own comparison logic — never coerce to a number.

**Missing range and no fallback entry.** Band is `unknown`. Show the value, say we
don't have a reference for it. Do not guess.

## The fourth signal — trending

A value **fully inside range** that has moved 25%+ over 6 months is worth flagging.

This is only possible because we hold the history, and it is our single strongest
differentiator.

Most conditions announce themselves over years while every individual report still
reads "normal." Someone whose fasting glucose went `88 → 96 → 103 → 109` across four
years has never once been flagged by a lab — but that is the most useful thing anyone
could tell them.

**Trend flags require:**
- ≥2 reports with the same canonical test id
- Same canonical unit (guaranteed by `normalize`)
- A minimum time gap, so two tests a week apart don't trigger noise
- Direction, magnitude, and time span all shown — never just "rising"

## What the user sees

Sort by clinical significance, never by the order the lab printed rows.

```
needs_attention  >  out_of_range  >  trending  >  borderline  >  normal
```
