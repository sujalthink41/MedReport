# Build Checkpoints — Backend V1

## How we work

**One checkpoint at a time.** I write it, you read it, we talk about it, then we move.
No large drops of code — a checkpoint you didn't understand is worse than no code.

Each checkpoint has:

- **Build** — what gets written
- **Learn** — the concepts it exists to teach you (this is why the order is what it is)
- **Done when** — the objective bar
- **Review focus** — what to look at hardest when you read it

Every checkpoint must also pass the definition-of-done in
[`08-engineering-standards.md`](08-engineering-standards.md#15-definition-of-done-for-any-checkpoint).

**This list is a plan, not a contract.** Reorder, split, or drop anything.

---

## Deliberate ordering note

We build the **pure clinical domain (CP13–15) before any AI code**.

That ordering is not an accident. The domain is the part that must be *correct*, it
needs no API keys, no network and no Docker, and it is fully testable in milliseconds.
Getting it solid first means that when model output later looks wrong, you already
know the classification logic isn't the culprit. Most people build this in the
opposite order and then cannot tell which half is broken.

---

# Phase 0 — Foundation

### CP1 · Repo skeleton and tooling
**Build** `backend/` layout per the standards doc (empty packages with `__init__.py`),
`pyproject.toml`, ruff + mypy + pytest config, `.pre-commit-config.yaml`,
`docker-compose.yml` with Postgres and Redis, `.env.example`, `Makefile`.
**Learn** Why the folder shape encodes the dependency rule · `mypy --strict` scoped to
domain only · pre-commit as a guardrail, not a formality.
**Done when** `make up`, `make lint`, `make test` all run green on an empty suite.
**Review focus** Does the layout make an illegal import obvious just by looking?

### CP2 · Config, logging, error hierarchy
**Build** `core/config.py` (pydantic-settings), `core/logging.py` (structlog + JSON +
PHI redaction processor), `domain/errors.py` (the full exception tree).
**Learn** 12-factor config · why structured logging is queryable and f-strings aren't ·
designing an exception hierarchy around *who is at fault*.
**Done when** A log line emits as JSON with a bound `request_id`; a test proves the
redaction processor strips a value that looks like PHI.
**Review focus** The domain/infrastructure error split — everything downstream leans
on it.

### CP3 · FastAPI skeleton, middleware, error mapping
**Build** `main.py`, request-id middleware, the single exception handler + `STATUS_MAP`,
`/health` and `/ready`.
**Learn** Driving adapter · middleware order · why routers contain no try/except ·
liveness vs readiness.
**Done when** A route raising `ProfileNotFound` returns a clean 404 with a request id,
and the stack trace appears only in logs.
**Review focus** Trace one error from `raise` to HTTP response. That path is the whole
pattern.

---

# Phase 1 — Domain and persistence

### CP4 · Domain models and ports (zero IO)
**Build** `domain/models/` — `Profile`, `Report`, `Observation`, and the value objects
`CanonicalValue`, `ReferenceRange`, `Band`. `domain/ports/` — all Protocols.
**Learn** Entity vs value object · `frozen=True` immutability · `Decimal` over `float` ·
`Protocol` vs ABC · making illegal states unrepresentable.
**Done when** `pytest tests/domain` passes with Docker stopped and no DB configured.
**Review focus** Try to construct an invalid `ReferenceRange`. You shouldn't be able to.

### CP5 · Database, SQLAlchemy 2.0, Alembic
**Build** async engine and session factory, ORM models for the full schema in
`07-data-model.md`, first migration, testcontainers fixture.
**Learn** Async session lifecycle and why it's request-scoped · migrations as version
control for data · ORM model ≠ domain model.
**Done when** `alembic upgrade head` builds the schema; a smoke test round-trips a row.
**Review focus** Confirm no ORM class is importable from `domain/`.

### CP6 · Repositories and Unit of Work
**Build** Concrete repositories implementing the CP4 ports, `mappers.py`, a UoW that
owns the transaction, plus in-memory fakes for tests.
**Learn** Repository pattern · why the use case owns the commit, not the repo · fakes
over mocks · Liskov — the fake and the real one must behave identically.
**Done when** The same test suite passes against both the fake and real Postgres.
**Review focus** That shared suite. It's the proof your abstraction is real.

---

# Phase 2 — Identity, authorization and upload

### CP7 · Authentication — Google OAuth and sessions
**Build** OAuth flow via Authlib, JWT issuance and rotation, `get_current_user`
dependency, `users` persistence.
**Learn** Authentication vs authorization (who you are vs what you may do) · FastAPI
dependency injection as the composition root · token lifetimes and refresh · why auth
is an adapter concern, not a domain one.
**Done when** A protected route rejects an anonymous caller and resolves a real user.
**Review focus** How DI wires a port to a concrete class in exactly one place.

### CP8 · Authorization — RBAC and the policy layer
**Build** `permissions`, `roles`, `role_permissions`, `user_roles`, `profile_members`,
`access_audit`. A `PolicyService` in the **domain** answering
`can(actor, action, resource)`. A `require(...)` dependency for routers. Seed
permissions and system roles.
**Learn** **ReBAC vs RBAC and why we need both** ([ADR 0004](adr/0004-two-authorization-systems.md)) ·
permission-based checks instead of role-name checks · deny-by-default · why the policy
layer is pure domain (testable with no DB, callable from API, worker and CLI) ·
audit logging as a product requirement in health.
**Done when** A parametrized truth table passes: owner / caregiver / viewer / stranger
/ support / admin × read / upload / delete / share — with no database running.
**Review focus** Confirm no code anywhere compares a role *name*. Grep for `== "admin"`;
it must return nothing.

### CP9 · Profiles and sharing — the reference vertical slice
**Build** Router → DTOs → use case → domain → repository. Create, list, get, delete a
profile; invite a member; change a member's role. Every read authorized via CP8.
**Learn** **This is the reference slice.** The three model types side by side, mapping
at each boundary, where validation happens at each layer, and where the permission
check belongs (application layer, not the router).
**Done when** Two users share one profile with different roles and each sees exactly
what their role allows.
**Review focus** Read this one twice. Every later feature copies its shape.

### CP10 · Storage port with two adapters
**Build** `FileStorage` port, `R2Storage`, `LocalDiskStorage`, selected by config.
**Learn** **Your plug-in/plug-out requirement, demonstrated.** One config value swaps
the implementation; nothing else in the codebase changes.
**Done when** The identical storage test suite passes against both adapters.
**Review focus** Count how many files change when you flip the config. Answer: one.

### CP11 · Upload endpoint
**Build** `POST /reports` multipart, validation (type, 50MB cap), sha256 dedupe,
store original, create report row, return `202 + report_id`. `GET /reports/{id}` status.
**Learn** Idempotency via natural keys · why we return 202 and never block · streaming
large uploads instead of buffering them in memory.
**Done when** Uploading the same file twice returns the same `report_id`, not a
duplicate.
**Review focus** The dedupe path — your first real idempotency guarantee.

### CP12 · Celery and the async bridge
**Build** Celery app, two queues (cheap / expensive), the `asyncio.run()` bridge
decorator, task error wrapper, one trivial end-to-end task.
**Learn** Queue topology and why one 25-page report must not block ten small ones ·
the sync/async bridge · Celery retries vs application-level retries (different layers,
do not stack them blindly).
**Done when** Upload enqueues a task, the worker runs it, and status transitions.
**Review focus** The bridge decorator. Write it once, correctly, then forget it.

# Phase 3 — The clinical core (pure domain, no AI)

> This phase is the heart of the product. All of it is pure functions with exhaustive
> tests and not a single network call.

### CP13 · Clinical dictionary and seed data
**Build** `canonical_tests`, `test_aliases`, `unit_conversions` tables, seed data for
~40 common markers, `unmapped_test_names` logging.
**Learn** Table-driven design — behaviour as data instead of code · why an alias
dictionary is a compounding asset · never silently dropping the unknown.
**Done when** Seeds load and `lookup_canonical("S.G.P.T.")` returns `alt`.
**Review focus** The alias normalization function. Punctuation, case, spacing, Unicode.

### CP14 · Normalization service
**Build** `domain/services/normalize.py` — raw name → canonical id, raw unit + value →
canonical unit + `Decimal` value, per-analyte conversion factors.
**Learn** Why conversion factors are per-analyte (molecular weight), not one formula ·
`Decimal` arithmetic and rounding policy · degrading to `unmapped` rather than guessing.
**Done when** A parametrized suite covers every conversion, plus unknown units, missing
units, and unmapped names.
**Review focus** The rounding policy. Decide it once, write it down, apply everywhere.

### CP15 · Classification engine
**Build** `RangeResolver` chain (the three layers), `BandClassifier`,
`position_in_range`, critical-value check.
**Learn** **Chain of Responsibility** · Open/Closed in practice · Strategy · why this
is deliberately code and not the model ([ADR 0002](adr/0002-code-classifies-not-llm.md)).
**Done when** The full edge-case table from the standards doc passes: boundary values,
the 10% edge, missing range, one-sided markers, qualitative results, zero, negative,
null.
**Review focus** Add a fake fourth resolver. If you touched more than one line, the
pattern isn't right yet.

---

# Phase 4 — The AI layer

### CP16 · LLM port and LiteLLM adapter
**Build** `LLMClient` port, `LiteLLMClient`, structured output with a Pydantic schema,
`ModelRouter` (node → model), `llm_traces` persistence.
**Learn** Provider-agnostic seams · structured outputs and schema enforcement · why
different nodes deserve different models · cost accounting from day one.
**Done when** A typed call returns a validated Pydantic object and writes a trace row.
**Review focus** Swap the model string to a different provider. Nothing else should
need to change.

### CP17 · Decorators — retry, cache, trace
**Build** `RetryingLLM`, `CachingLLM`, `TracedLLM`, each implementing `LLMClient`,
composed in the container.
**Learn** **Decorator pattern** · tenacity with exponential backoff and jitter ·
retryable vs non-retryable classification · the deadline budget.
**Done when** A simulated 503 retries with backoff; a `LLMInvalidOutput` does not.
**Review focus** Each decorator in isolation. Removing any one must leave the rest
working.

### CP18 · Extraction prompt and the golden set
**Build** The extraction prompt, its output schema, the eval harness, and the first
10 hand-labelled golden reports.
**Learn** Prompt engineering against a measurable target · why evals are scored, not
pass/fail · row recall as the metric that matters most.
**Done when** `make eval` prints recall, value accuracy, unit accuracy, and false
confidence rate.
**Review focus** The false-confidence metric. That's the number that can hurt a user.

---

# Phase 5 — The pipeline

### CP19 · LangGraph skeleton
**Build** `GraphState`, node registry + decorator, Postgres checkpointer, graph builder,
`ingest` node (PDF → page images).
**Learn** **Registry pattern** · durable checkpointing and resume-from-failure ·
designing graph state so nodes stay independent.
**Done when** Killing the worker mid-run and restarting resumes from the last completed
node.
**Review focus** Register a no-op node without editing the graph builder. That's the
plug-in test.

### CP20 · Extract and verify nodes
**Build** Per-page parallel fan-out for `extract`; the `verify` agentic loop with its
four tools and a 3-round cap.
**Learn** Fan-out/fan-in · **the one agentic loop** — tool dispatch, termination
conditions, and self-correction · why "I couldn't read this" is a feature.
**Done when** A page with a deliberately corrupted row is marked `unreadable` rather
than guessed.
**Review focus** The termination condition. Unbounded agent loops are how you get a
surprise invoice.

### CP21 · Merge, normalize, classify wired in
**Build** The `merge` node (dedupe headers, stitch page-split rows), then wire in the
CP14 and CP15 services as nodes.
**Learn** Composition — pure domain services reused unchanged inside the pipeline ·
partial-failure handling (`status=partial`).
**Done when** A real multi-page report produces correctly classified observations
end to end.
**Review focus** Note that CP14/CP15 needed no changes to run here. That's the
architecture paying you back.

### CP22 · Explain, reason, trends
**Build** `explain` node with `(test, band)` caching, `reason` node for panel-level
insights, `TrendDetector` over report history.
**Learn** Cache keys chosen to be PHI-free and globally shareable · giving a model a
whole panel so it can find a story rather than list flags · time-based logic tested
with a fake `Clock`.
**Done when** Two reports six months apart produce a correct trend flag; the same
explanation is served from cache on the second report.
**Review focus** Confirm nothing in the cache key can identify a person.

---

# Phase 6 — Delivery and hardening

### CP23 · Report read API
**Build** `GET /reports/{id}` full result, grouped by panel, sorted by significance,
with trends and insights attached.
**Learn** Designing a response DTO around what the UI actually renders · avoiding N+1
queries · pagination and projection.
**Done when** One request returns everything the results screen needs.
**Review focus** Query count. It should be a small constant, not proportional to rows.

### CP24 · Prep sheet
**Build** `prep_sheet` node, its generation prompt, persistence, and export endpoint.
**Learn** Building the product's wedge deliberately · templating a document from
structured data rather than free generation.
**Done when** A generated sheet is one page and genuinely useful to carry to a doctor.
**Review focus** Read the output as a patient, not as an engineer. Would you carry it?

### CP25 · Safety pass
**Build** Critical-value templated messaging (code-written, never model-written), the
persistent framing disclaimer, uncertainty surfacing, hard-delete cascade.
**Learn** Where generated copy is forbidden and why · designing deletion properly
across DB and object storage.
**Done when** A critical value produces the exact templated message every single time.
**Review focus** Verify the LLM cannot influence critical-value copy on any path.

### CP26 · Production readiness
**Build** Rate limiting, Sentry with PHI redaction, nightly `pg_dump` to R2, Caddy +
compose for the VPS, GitHub Actions deploy.
**Learn** Operational thinking · why backups are a product feature in health · deploy
as a boring, repeatable script.
**Done when** A restore from backup is *tested*, not merely configured.
**Review focus** Actually run the restore. An untested backup is not a backup.

---

## Progress

| | Checkpoint | Status |
|---|---|---|
| CP1 | Repo skeleton and tooling | ✅ |
| CP2 | Config, logging, errors | ✅ |
| CP3 | FastAPI skeleton and error mapping | ✅ |
| CP4 | Domain models and ports | ✅ |
| CP5 | Database and migrations | ☐ |
| CP6 | Repositories and Unit of Work | ☐ |
| CP7 | Authentication — Google OAuth | ☐ |
| CP8 | Authorization — RBAC and policy layer | ☐ |
| CP9 | Profiles and sharing (reference slice) | ☐ |
| CP10 | Storage port, two adapters | ☐ |
| CP11 | Upload endpoint | ☐ |
| CP12 | Celery and async bridge | ☐ |
| CP13 | Clinical dictionary | ☐ |
| CP14 | Normalization service | ☐ |
| CP15 | Classification engine | ☐ |
| CP16 | LLM port and LiteLLM adapter | ☐ |
| CP17 | Retry / cache / trace decorators | ☐ |
| CP18 | Extraction prompt and golden set | ☐ |
| CP19 | LangGraph skeleton | ☐ |
| CP20 | Extract and verify nodes | ☐ |
| CP21 | Merge, normalize, classify wired | ☐ |
| CP22 | Explain, reason, trends | ☐ |
| CP23 | Report read API | ☐ |
| CP24 | Prep sheet | ☐ |
| CP25 | Safety pass | ☐ |
| CP26 | Production readiness | ☐ |
