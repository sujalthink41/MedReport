# Baseline

**Upload a lab report. Understand what's off, what changed, and what to do about it.**

Baseline reads lab reports (PDF or phone photo), explains every value in plain
language, and — because it holds every report you've ever uploaded — shows you the
*direction* your health is moving, not just today's number.

The thesis is in the name: what matters isn't a generic reference range,
it's **your** baseline.

---

## Core principle

> **Model for meaning. Code for math.**

An LLM reads arbitrary report layouts, maps synonyms, explains markers and reasons
across a whole panel. Deterministic code converts units, compares values to ranges,
and computes trends.

A probabilistic model never decides whether a value is out of range.

## Framing

Baseline helps people **understand** their reports. It does not diagnose, and it does
not prescribe. See [`docs/06-safety.md`](docs/06-safety.md).

---

## Docs

| Doc | What's in it |
|---|---|
| [00 Product brief](docs/00-product-brief.md) | Problem, user, wedge, success metric |
| [01 V1 scope](docs/01-v1-scope.md) | What ships, what's explicitly cut |
| [02 Tech stack](docs/02-tech-stack.md) | Locked decisions + deployment |
| [03 Architecture](docs/03-architecture.md) | Request flow and the processing graph |
| [04 Extraction pipeline](docs/04-extraction-pipeline.md) | Nodes, the agentic verifier, evaluation |
| [05 Classification](docs/05-classification.md) | The three-layer band logic |
| [06 Safety](docs/06-safety.md) | Framing, critical values, PHI handling |
| [07 Data model](docs/07-data-model.md) | Postgres schema |
| [08 Engineering standards](docs/08-engineering-standards.md) | Architecture, SOLID, patterns, errors, retry, logging |
| [09 Build checkpoints](docs/09-build-checkpoints.md) | The 25-step build plan |
| [ADRs](docs/adr/) | Decisions worth explaining later |
| [Learning guides](docs/learning/) | Per-checkpoint reading guides — how to study the code |

## Stack

FastAPI · Postgres · Redis · Celery · LangGraph · LiteLLM · Cloudflare R2 ·
Google OAuth · Next.js · Docker Compose

## Status

**CP1 complete** — backend skeleton, tooling and the dependency-rule linter.
Next: **CP2 - config, logging, error hierarchy**.
