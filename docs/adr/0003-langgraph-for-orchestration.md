# ADR 0003 — LangGraph for pipeline orchestration

**Status:** accepted · **Date:** 2026-09-05

## Context

Processing a report is a multi-step job that can run 30-90+ seconds and involves
several model calls. Reports can be 25+ pages. Steps fail — rate limits, timeouts,
malformed output.

Options considered: plain Python, LangChain, LangGraph, Google ADK.

## Decision

**LangGraph**, with **LiteLLM** as the model access layer, on **Celery** workers.

## Rationale

- **Durable checkpointing.** A failure at `explain` must resume from `explain`, not
  re-run vision extraction over 25 pages. This is the deciding factor.
- **Fan-out.** Pages extract independently and in parallel, so a 25-page report takes
  roughly as long as a 3-page one.
- **Not LangChain classic** — abstraction tax, thin benefit for our shape of work.
- **Not Google ADK** — pulls toward Google infrastructure, which conflicts with our
  model choice and our self-hosted deployment.
- **LiteLLM** keeps provider choice a config value. Different nodes will use
  different models; vision extraction and clinical reasoning are different jobs.

## Consequences

- Celery is sync-first; LangGraph/LiteLLM calls need an `asyncio.run()` bridge inside
  the task. Wrapped once in a decorator.
- Checkpoint state lives in Postgres, adding schema we must migrate
- We accept framework coupling in the pipeline layer only. Business logic,
  classification and the clinical dictionary stay framework-free and independently
  testable.
