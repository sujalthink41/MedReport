# Architecture

## Core principle

> **Model for meaning. Code for math.**

The LLM reads, identifies, explains and reasons.
Deterministic code converts units, compares numbers to ranges, and computes trends.

Never let a probabilistic model decide whether a value is out of range. Ask an LLM
twice whether 6.1 is borderline and you can get two different answers — same report,
two verdicts. In health that destroys trust permanently, and you cannot write a test
for it. Comparing a number to a range is something computers have done perfectly
since 1970.

## Request flow

```
  Client
    |
    |  POST /reports  (multipart)
    v
  FastAPI ──── store original → R2
    |          create report row (status=queued)
    |          enqueue celery task
    v
  202 Accepted { report_id }        Client polls GET /reports/{id}
                                     or subscribes to SSE
```

## Processing pipeline (Celery task → LangGraph)

```
                    ┌──────────────┐
                    │    ingest    │  fetch from R2, split pages,
                    └──────┬───────┘  render to images
                           │
              ┌────────────┼────────────┐        fan-out, parallel
              v            v            v
         ┌────────┐  ┌────────┐  ┌────────┐
         │extract │  │extract │  │extract │      vision model per page
         │ page 1 │  │ page 2 │  │ page N │      → rows + confidence
         └────┬───┘  └────┬───┘  └────┬───┘
              │           │           │
              v           v           v
         ┌────────┐  ┌────────┐  ┌────────┐
         │ verify │  │ verify │  │ verify │      the agentic loop
         └────┬───┘  └────┬───┘  └────┬───┘
              └───────────┼───────────┘
                          v
                    ┌──────────────┐
                    │    merge     │  de-duplicate repeated headers,
                    └──────┬───────┘  stitch rows split across pages
                           v
                    ┌──────────────┐
                    │  normalize   │  canonical test id + unit conversion
                    └──────┬───────┘  (deterministic, dictionary-driven)
                           v
                    ┌──────────────┐
                    │  classify    │  pure code. four bands.
                    └──────┬───────┘
                           v
                    ┌──────────────┐
                    │   explain    │  per-marker copy, heavily cached
                    └──────┬───────┘
                           v
                    ┌──────────────┐
                    │   reason     │  panel-level pattern insight
                    └──────┬───────┘
                           v
                    ┌──────────────┐
                    │  prep_sheet  │  the shareable artifact
                    └──────┬───────┘
                           v
                        persist
                     status=complete
```

Each node is a LangGraph checkpoint. A failure at `explain` resumes from `explain`,
not from `ingest`. This matters: re-running vision extraction on a 25-page report
because a downstream node timed out is both slow and expensive.

## Why fan out per page

Pages extract **independently** — no shared context. A 25-page report then takes
roughly as long as a 3-page one. The cost of independence is duplicate rows (lab
headers repeat on every page), which the `merge` node resolves deterministically.

## Node ownership

| Node | Type | Notes |
|---|---|---|
| `ingest` | code | PDF → page images (PyMuPDF) |
| `extract` | **LLM** (vision) | strict JSON, per page |
| `verify` | **LLM + tools** | the one agentic loop |
| `merge` | code | de-dup, stitch |
| `normalize` | code + dictionary | canonical ids, unit conversion |
| `classify` | **code only** | four bands |
| `explain` | **LLM** | cached by `(test_id, band)` |
| `reason` | **LLM** | full panel in context |
| `prep_sheet` | **LLM** | needs history + current report |

## Model routing (via LiteLLM)

Different nodes have different needs. Do not use one model for everything.

- `extract` — vision, high accuracy, structured output. The expensive one.
- `explain` — cheap model is fine; output is cached and reused across all users.
- `reason` / `prep_sheet` — strongest reasoning model. Low volume, high value.

## Idempotency

Every node is safe to re-run. Reprocessing a report must produce the same rows, not
duplicates. Key on `(report_id, page, canonical_test_id)`.
