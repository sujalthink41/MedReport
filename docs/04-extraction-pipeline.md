# Extraction Pipeline

## The failure mode we are designing against

> A wrong number, shown confidently, about someone's health.

Everything below exists to make that outcome rare, and to make it visible when we
can't avoid it.

## `extract` node

Vision model, one call per page, strict JSON output.

Extracted per row:

| Field | Notes |
|---|---|
| `raw_test_name` | exactly as printed, no cleanup |
| `value` | numeric, or the literal string if non-numeric |
| `unit` | exactly as printed |
| `ref_low` / `ref_high` | from the report's own printed range |
| `ref_text` | raw range string — some are `"< 200"`, `"Negative"`, `"1:40"` |
| `flag` | the lab's own H/L marker if present |
| `page` | |
| `confidence` | model's own confidence, 0-1 |

**Rules:**
- Never invent a reference range. If the report doesn't print one, leave it null —
  `normalize` handles the fallback
- Never normalize the test name here. Raw only. Mapping happens in `normalize`
- Section headers (`LIVER FUNCTION TEST`) are captured as panel context, not as rows

## `verify` node — the one agentic loop

An agent that checks its own extraction against the source image and can call tools
to fix itself.

**Checks:**
- Does my row count match the visibly tabulated rows? *Did I drop a page or a column?*
- Is any value implausible for its unit? *(Hb of 130 means it read g/L, not g/dL)*
- Is the value outside its own printed range by an absurd factor? *Likely a decimal
  or OCR error*
- Missing reference range → look up the fallback table
- Unrecognized test name → search the canonical dictionary before giving up
- Low confidence on a row → re-crop that region and re-read it

**Tools available to the agent:**

```
lookup_canonical_test(raw_name)     -> canonical id + aliases, or null
lookup_fallback_range(test, age, sex, unit)
plausibility_check(test, value, unit)   -> ok | suspect | impossible
recrop_and_reread(page, bbox)       -> re-extract a single region
```

**Termination:** max 3 tool-use rounds per page. Then commit.

**The most important behaviour:** if a row is still uncertain after the loop, mark it
`unreadable` and surface that to the user. Do not guess.

> *"We couldn't read 2 rows on page 3 — tap to check"*
> is a better product than a confident wrong number.

## `merge` node

Deterministic. Handles:
- Duplicate rows from repeated page headers/footers
- Rows split across a page break
- The same test appearing twice (some labs print a summary and a detail section) —
  keep the one with the reference range and higher confidence

## `normalize` node

Deterministic, dictionary-driven. Two jobs:

**1. Canonical test ID**
`SGPT` / `ALT` / `Alanine Transaminase` / `S.G.P.T.` → `alt`

The alias dictionary is a **project asset**. It grows with every unmapped name we see.
Unmapped names are logged, never silently dropped.

**2. Unit conversion**
Everything to one canonical unit per test.
`mg/dL` ↔ `mmol/L`, `ng/mL` ↔ `nmol/L`, `g/L` ↔ `g/dL`.

Conversion factors are per-analyte (they depend on molecular weight) — this is a
table, not a formula.

> Skip this and every comparison silently breaks the moment a user switches labs.

## Evaluation — build this in week one

**A golden set of 50 real reports with hand-labelled ground truth.** Every value,
every unit, every range, typed out by hand.

Then `pytest` scores extraction against it:

- **Row recall** — did we find every row? *(the metric that matters most)*
- **Value accuracy** — exact match on numbers
- **Unit accuracy**
- **Range accuracy**
- **Mapping coverage** — % of rows resolved to a canonical id
- **False confidence rate** — rows we got wrong but marked high-confidence.
  *This is the dangerous one.*

Without this you are flying blind. You will change a prompt to fix one report and
silently break twenty. This is the single highest-leverage thing to build first, and
almost nobody does it.

Reports in the golden set must be de-identified and stored outside the repo.
