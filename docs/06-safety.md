# Safety, Framing & Data Handling

## Product framing — the line we do not cross

**We help people understand. We do not diagnose.**

| Allowed | Not allowed |
|---|---|
| "Ferritin is low. Ferritin reflects iron stores." | "You have anaemia." |
| "Low ferritin is commonly associated with fatigue." | "Your tiredness is caused by this." |
| "This is often investigated further with X." | "You should take iron supplements." |
| "Worth discussing with your doctor." | "You don't need to see a doctor." |

This framing is not a legal fig leaf — it is also the *honest* description of what a
lab value can tell you without a clinician. It costs nothing in usefulness.

**Every report view carries a persistent, non-dismissible line** stating this is for
understanding, not medical advice.

## Critical values

Some results need immediate care, not a trend line. A curated critical-values list
(e.g. severe hyperkalaemia, critically low haemoglobin, very high glucose) triggers a
clear, unmissable prompt to seek medical attention now.

This must never be softened by generated copy. It is a **code-driven, templated
message** — the LLM does not write it.

## Uncertainty is shown, not hidden

- Rows we couldn't read reliably are labelled as such
- Ranges taken from our fallback table are marked as "general reference", not
  "this lab's range"
- Values with no available reference are shown as `unknown`, not guessed

## Benchmarks are context, not marketing

Published medical-LLM accuracy figures (e.g. structured report understanding around
88% on MedRepBench) are **evidence the approach is feasible** — never a number we put
in the UI or a pitch, and never a substitute for our own golden-set evaluation on real
reports from our actual users' labs.

## Health data handling

**PHI never leaves our infrastructure except to the model provider.**

- Encrypt at rest — database volume and R2 bucket
- Never log raw PHI to third-party error tracking. Redact before sending to Sentry
- The `llm_traces` table lives in our own Postgres, precisely so prompts containing
  health data stay on our infra
- Not Langfuse Cloud, for the same reason
- Signed, short-lived URLs for file access. Never public bucket objects
- Nightly `pg_dump` to R2, encrypted. Health data you cannot restore is a
  product-ending event
- Model provider: use an enterprise/BAA-eligible agreement and opt out of training

**Retrofitting any of this later is a rewrite.** It goes in from commit one.

## Deletion

A user can delete a profile and everything under it — reports, extracted rows,
original files in R2, and traces. Hard delete, not a soft flag.
