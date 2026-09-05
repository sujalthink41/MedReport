# Engineering Standards

This doc is both the rulebook and a teaching document. Every rule is grounded in
*this* codebase — if a principle can't be shown with a real example from MedReport,
it doesn't belong here.

---

## 1. The one rule everything else serves

> **Dependencies point inward. The domain knows nothing about the outside world.**

```
        ┌─────────────────────────────────────────────┐
        │  DRIVING ADAPTERS  (things that call us)    │
        │  FastAPI routers · Celery tasks · CLI       │
        └───────────────────┬─────────────────────────┘
                            │ calls
                            v
        ┌─────────────────────────────────────────────┐
        │  APPLICATION  (use cases / services)        │
        │  "process a report", "create a profile"     │
        └───────────────────┬─────────────────────────┘
                            │ depends on PORTS (interfaces)
                            v
        ┌─────────────────────────────────────────────┐
        │  DOMAIN  (pure)                             │
        │  entities · value objects · business rules  │
        │  NO imports of fastapi, sqlalchemy, litellm │
        └─────────────────────────────────────────────┘
                            ^
                            │ implements PORTS
        ┌───────────────────┴─────────────────────────┐
        │  DRIVEN ADAPTERS  (things we call)          │
        │  Postgres repos · R2 · LiteLLM · Celery     │
        └─────────────────────────────────────────────┘
```

**The test:** you can delete the entire `adapters/` folder and `domain/` still
imports and its tests still pass. If that isn't true, the dependency rule is broken.

**Why this matters for you specifically:** you said you want features to plug in and
out. This is the mechanism. A feature is plug-out-able exactly when nothing in the
domain imports it by name.

### Folder layout

```
backend/app/
├── domain/                 # PURE. no framework, no IO, not even async.
│   ├── models/             # entities & value objects
│   ├── errors.py           # domain exception hierarchy
│   ├── ports/              # Protocols — the interfaces we depend on
│   │   ├── repositories.py
│   │   └── services.py
│   └── services/           # pure business rules (classification, normalization)
│
├── application/            # use cases. orchestrates domain + ports.
│   └── use_cases/
│
├── adapters/               # ALL IO lives here. implements ports.
│   ├── db/                 # sqlalchemy models + repositories
│   ├── storage/            # r2, local disk
│   ├── llm/                # litellm client, prompts
│   └── queue/              # celery
│
├── api/                    # driving adapter — FastAPI
│   ├── v1/routers/
│   ├── v1/schemas/         # request/response DTOs
│   └── deps.py             # dependency injection wiring
│
├── pipeline/               # driving adapter — LangGraph
│   ├── graph.py
│   ├── state.py
│   └── nodes/
│
└── core/                   # config, logging, DI container, middleware
```

---

## 2. Three model types — do not collapse them

This confuses everyone at first, so be precise. The same concept exists in three
shapes, in three layers, for three reasons.

| | Lives in | Job | Example |
|---|---|---|---|
| **DTO** | `api/v1/schemas/` | Wire format. What the client sends and receives. | `ObservationResponse` |
| **Domain model** | `domain/models/` | Business truth + invariants. | `Observation` |
| **ORM model** | `adapters/db/models/` | Table shape. | `ObservationRow` |

**Why not just one?** Because they change for different reasons — the single best
justification for any separation.

- Rename a DB column → only the ORM model and its mapper change
- Add a field to the API → only the DTO changes
- Add a business rule → only the domain model changes

Collapse them and a database migration breaks your public API.

**Mapping** happens at the boundary, in explicit functions. Never leak an ORM object
past the repository. Never leak a DTO into the domain.

```python
# adapters/db/mappers.py
def to_domain(row: ObservationRow) -> Observation: ...
def to_row(obs: Observation) -> ObservationRow: ...
```

---

## 3. Ports = `Protocol`, not ABC

Use `typing.Protocol`. Structural typing means adapters don't inherit from anything —
they just have the right shape. Less coupling, and adapters never need to import a
base class from the domain.

```python
# domain/ports/repositories.py
from typing import Protocol
from uuid import UUID
from app.domain.models.report import Report

class ReportRepository(Protocol):
    async def get(self, report_id: UUID) -> Report | None: ...
    async def add(self, report: Report) -> None: ...
    async def find_by_hash(self, profile_id: UUID, sha256: str) -> Report | None: ...
```

Note what is **absent**: no `session`, no `select()`, no SQLAlchemy. The domain
expresses *what it needs*, never *how it is stored*.

```python
# domain/ports/services.py
class FileStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def signed_url(self, key: str, ttl_seconds: int) -> str: ...

class Clock(Protocol):
    def now(self) -> datetime: ...
```

> **`Clock` is not over-engineering.** Trend logic depends on time. A `Clock` port
> means you test "value rose 25% over 6 months" in milliseconds with a fake clock,
> instead of not testing it at all.

---

## 4. SOLID, in this codebase

**S — Single Responsibility.** *A class changes for one reason.*
`RangeResolver` finds the applicable range. `BandClassifier` assigns the band.
`TrendDetector` compares across reports. Three reasons to change, three classes.
The temptation is one `HealthAnalyzer` that does all three. Resist it.

**O — Open/Closed.** *Open to extension, closed to modification.*
Adding a fourth range source must not require editing `BandClassifier` — it is a new
strategy appended to a list. Adding a pipeline node must not require editing the
graph builder — the node registers itself. **This is your plug-in/plug-out
requirement, and this principle is how you get it.**

**L — Liskov Substitution.** *Any implementation of a port must be truly swappable.*
`LocalDiskStorage` must behave like `R2Storage` — same errors, same semantics for a
missing key. If your tests only pass against one implementation, you have violated
this and your "swappable adapter" is a lie.

**I — Interface Segregation.** *Small, focused ports.*
The `explain` node needs to read observations. Do not hand it a fat
`EverythingRepository` with 30 methods; give it `ObservationReader` with two. Small
ports also make fakes trivial to write.

**D — Dependency Inversion.** *Depend on abstractions.*
Services take ports in `__init__`. They never import an adapter. The **composition
root** (`core/container.py`) is the single place that knows which concrete class
implements what — and the only file that changes when you swap a provider.

---

## 5. Design patterns we actually use

Only patterns that earn their place. A pattern used without a forcing reason is just
indirection with a fancy name.

### Repository — data access
The domain asks for objects, not rows. Covered above.

### Chain of Responsibility — range resolution
The three-layer range logic in `05-classification.md` *is* this pattern. Each
resolver either handles the case or passes it along.

```python
class RangeResolver(Protocol):
    def resolve(self, obs: Observation, ctx: PatientContext) -> Range | None: ...

RESOLVERS: list[RangeResolver] = [
    GuidelineThresholdResolver(),   # layer 3 — highest priority
    LabPrintedRangeResolver(),      # layer 1
    FallbackTableResolver(),        # layer 2
]

def resolve_range(obs, ctx) -> Range | None:
    for r in RESOLVERS:
        if (found := r.resolve(obs, ctx)) is not None:
            return found
    return None
```

Adding a fourth source is one line appended to a list. Nothing else changes. That is
Open/Closed made concrete rather than recited.

### Strategy — model routing
`extract` needs a vision model, `explain` a cheap one, `reason` the strongest.
A `ModelRouter` maps node to model config, so changing a model is a config edit.

### Decorator — cross-cutting concerns on LLM calls
Retry, tracing, caching and cost accounting are **four separate concerns**. Do not
write them into one function.

```python
llm = TracedLLM(RetryingLLM(CachingLLM(LiteLLMClient(...))))
```

Each wrapper implements `LLMClient` and delegates inward. Each is independently
testable, and any one can be dropped without touching the others.

### Registry — pipeline nodes
Nodes self-register with a decorator; the graph builder reads the registry. Adding a
node never means editing a central `if/elif`.

```python
@register_node("extract", depends_on=["ingest"])
async def extract_node(state: GraphState) -> GraphState: ...
```

### Unit of Work — transaction boundary
The **use case** owns the transaction, never the repository. One use case, one
commit. Repositories participate in a session they do not own.

### Value Object — make illegal states unrepresentable
`CanonicalValue(value: Decimal, unit: Unit)` instead of a bare float. You then cannot
accidentally compare `mg/dL` against `mmol/L` — the type system stops you before the
test suite has to.

> In a product where a unit mix-up shows someone a wrong number about their own
> health, this is not academic. It is the highest-value type in the codebase.

**Use `Decimal`, never `float`, for lab values.** `0.1 + 0.2 != 0.3` is a bug you do
not want anywhere near a medical number.

---

## 6. Error handling

### An exception hierarchy that means something

```python
# domain/errors.py
class MedReportError(Exception):
    """Root. Everything we raise deliberately."""

# --- domain errors: the caller did something invalid -----------------
class DomainError(MedReportError): ...
class ProfileNotFound(DomainError): ...
class DuplicateReport(DomainError): ...
class UnsupportedFileType(DomainError): ...

# --- infrastructure errors: something we depend on failed ------------
class InfrastructureError(MedReportError): ...
class StorageUnavailable(InfrastructureError): ...
class LLMUnavailable(InfrastructureError): ...
class LLMInvalidOutput(InfrastructureError): ...

# --- pipeline errors -------------------------------------------------
class PipelineError(MedReportError): ...
class UnreadableDocument(PipelineError): ...
```

**Why the split matters:** `DomainError` maps to 4xx, must not be retried, and is the
caller's problem. `InfrastructureError` maps to 5xx, *should* be retried, and is
ours. That one distinction drives both the HTTP layer and the retry policy — two
systems, one taxonomy.

### Rules

1. **Never catch bare `Exception`** except at the outermost boundary — the API error
   middleware and the Celery task wrapper. Nowhere else.
2. **Never swallow.** `except: pass` is banned. To deliberately ignore something, log
   at debug with a comment saying why.
3. **Translate at boundaries.** Adapters convert foreign exceptions into ours:
   `botocore.ClientError` becomes `StorageUnavailable`. The domain must never see a
   `botocore` type — that would be an inward dependency, breaking rule #1.
4. **Errors carry context, not prose.** `ProfileNotFound(profile_id=...)`, not
   `Exception("profile 3f2a not found")`. Structured fields are greppable and
   loggable; strings are neither.
5. **One mapper, at the edge.** A single `exception_handler` maps our hierarchy onto
   HTTP. Routers contain zero try/except.

```python
# api/error_handlers.py
STATUS_MAP: dict[type[MedReportError], int] = {
    ProfileNotFound:     404,
    DuplicateReport:     409,
    UnsupportedFileType: 415,
    LLMUnavailable:      503,
}
```

6. **Never leak internals to the client.** Log the stack trace against the request id;
   return a generic message plus that id. The user gets something to quote to
   support; an attacker learns nothing about your stack.

### Partial failure is a first-class outcome

A 25-page report where page 7 fails is not a failed report. It is `status=partial`
with 24 pages of results and an honest note about the gap. Designing this
all-or-nothing would be technically simpler and wrong for the product.

---

## 7. Retry

### Only retry what is worth retrying

```python
RETRYABLE  = (LLMUnavailable, StorageUnavailable, TimeoutError, ConnectionError)
NEVER_RETRY = (DomainError, LLMInvalidOutput)   # will fail identically forever
```

Retrying a validation error burns money and delays the user's answer to reach the
same conclusion.

### Exponential backoff **with jitter**

```python
from tenacity import (retry, stop_after_attempt, wait_exponential_jitter,
                      retry_if_exception_type, before_sleep_log)

@retry(
    retry=retry_if_exception_type(RETRYABLE),
    wait=wait_exponential_jitter(initial=1, max=30, jitter=2),
    stop=stop_after_attempt(4),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def call_model(...): ...
```

**Why jitter is not optional:** without it, a provider blip makes every worker retry
at exactly t+1s, t+2s, t+4s — you rebuild the thundering herd that caused the outage
in the first place. Jitter spreads them. This is one of the small details that
separates code working at 10 users from code working at 10,000.

### Three things people forget

- **Deadline, not just attempt count.** A report must finish within N minutes total.
  Track a budget across the whole pipeline, not per call.
- **Idempotency is a precondition.** Retry is only safe if re-running the node is
  safe. That is exactly why `observations` carries
  `UNIQUE (report_id, page, canonical_test_id)`.
- **Circuit breaker at scale.** If a provider has failed continuously for 60s, stop
  trying — fail fast, keep the queue moving, recover cleanly. Not needed on day one;
  design the LLM adapter so it can be added later as one more decorator.

### Malformed LLM output is a different problem

Do not blindly retry the same prompt. **Repair**: feed the validation error back and
ask for a correction. One repair attempt, then fail that row and mark it
`unreadable`. Honest beats persistent.

---

## 8. Validation — three distinct boundaries

Validation is not one thing. Different boundaries, different failure modes.

**1. API input** — Pydantic DTOs. Types, formats, sizes. Rejects garbage off the
wire, returns 422.

**2. Domain invariants** — enforced inside the domain model, so an invalid object
cannot be constructed at all.

```python
@dataclass(frozen=True)
class ReferenceRange:
    low: Decimal | None
    high: Decimal | None

    def __post_init__(self) -> None:
        if self.low is None and self.high is None:
            raise DomainError("range must bound at least one side")
        if self.low is not None and self.high is not None and self.low >= self.high:
            raise DomainError("range low must be < high")
```

> **`frozen=True` on value objects.** Immutability removes an entire category of bug —
> nothing can mutate a range after it has been validated.

**3. LLM output** — the most hostile boundary, because the model can return anything.
Strict schema first, then *semantic* checks Pydantic cannot express: is this value
plausible for this unit? Is it wildly outside its own printed range?

Validation lives at boundaries. Once inside the domain, code trusts its inputs
because the types guarantee them. Defensive checks scattered through business logic
are a symptom of weak boundaries, not of careful engineering.

---

## 9. Serialization

- **Inbound:** DTO → mapper → domain model. An explicit function, never `Model(**d)`.
- **Outbound:** domain model → response DTO. The API contract is deliberate, never
  "whatever fields the domain happens to have today."
- **`Decimal` over the wire:** serialize as a string. JSON floats lose precision, and
  a lab value must not.
- **Datetimes:** UTC, ISO-8601, always timezone-aware. Naive `datetime.now()` is
  banned — use the `Clock` port.
- **Enums:** string-valued, and the wire value is frozen forever. Renaming
  `out_of_range` breaks every client that ever shipped.
- **Never** `response_model=None` with a raw dict return. That is an undocumented API.

---

## 10. Logging

**Structured, JSON, via `structlog`.** Logs are queryable data, not prose.

```python
log.info("observation_classified",
         report_id=report_id, test=canonical_id, band=band, source=ref_source)
```

Not `log.info(f"Classified {canonical_id} as {band}")`. You cannot query that.

### Correlation

A `request_id` is generated at the edge, stored in a `contextvar`, and bound to every
log line automatically. Inside the pipeline, `report_id` and `node` are bound too.
One filter on `report_id` then gives you the complete story of one report across the
API, the worker, and every model call.

### Levels — use them with discipline

| Level | Means | Example |
|---|---|---|
| `DEBUG` | dev only | prompt lengths, cache keys |
| `INFO` | a normal thing happened | report queued, node completed |
| `WARNING` | degraded, but we recovered | retry succeeded, fallback range used |
| `ERROR` | this request or task failed | node failed after all retries |
| `CRITICAL` | the system is unwell | database unreachable |

A retry that succeeded is a `WARNING`, not an `ERROR`. If everything is `ERROR`,
nothing is.

### **Never log PHI**

Not names, not values, not raw model output. Log **identifiers**, then read actual
content from the `llm_traces` table, which lives on our own infrastructure and is
access-controlled.

Add a redaction processor to the structlog chain, so this is enforced by the pipeline
rather than by everyone remembering.

---

## 11. Helper methods and avoiding redundancy

**Something becomes a helper when:**
- It is used in 3+ places, **or**
- It has a name that expresses an intention the inline code does not

**It should not when:**
- It is used once and the inline version reads more clearly
- It lands in a `utils.py` bag with no cohesion. `utils.py` always becomes a landfill —
  name modules for what they do: `units.py`, `dates.py`, `text_normalize.py`

**Duplication rules:**
- Two things that *look* alike but change for different reasons are **not**
  duplication. Merging them couples unrelated features — a worse bug than the copy.
- Three strikes: copy once, wince the second time, extract on the third. Extracting
  too early produces a helper with five boolean flags, which is worse than both.
- **Redundant code is worse than duplicated code.** Dead branches, unreachable
  guards, "just in case" checks. Delete them; git remembers.

---

## 12. Testing

```
        /\        e2e (few)         upload a real PDF → assert prep sheet
       /  \
      /────\      integration       repos against real Postgres (testcontainers)
     /      \
    /────────\    unit (many)       domain logic. no DB, no network, milliseconds.
```

**The domain test suite must run without Docker.** If you need a database to test
band classification, the hexagonal architecture is buying you nothing.

- **Fakes over mocks.** An `InMemoryReportRepository` implementing the port beats
  `Mock()`. It tests behaviour instead of asserting call sequences, so it does not
  shatter on every refactor.
- **Edge cases are the deliverable.** For classification, write the table first:
  value exactly at the boundary · exactly at the 10% edge · missing range ·
  one-sided marker · qualitative result · unit mismatch · negative value · zero ·
  null. One parametrized test, one row each.
- **Golden set evals** run separately and are *scored*, not pass/fail. See
  `04-extraction-pipeline.md`.

---

## 13. Scaling — decisions that cost nothing now

- **Stateless API.** No in-process state; scale by adding containers.
- **All slow work is queued.** No request ever waits on a model call.
- **Separate queues** for cheap and expensive work, so one 25-page report cannot block
  ten quick ones.
- **Cache what has no PHI.** `explanation_cache` is keyed on `(test, band)` and shared
  across every user — at scale, per-marker copy becomes nearly free.
- **Connection pools are a real limit.** Postgres has a hard max. Pool sizing across
  API plus N workers is a capacity calculation, not a default you leave alone.
- **Every write is idempotent**, keyed on natural keys, so retries and replays are safe.
- **Cost is a metric.** Track tokens and USD per report from day one. It is the number
  that decides whether the business works.

---

## 14. Tooling — non-negotiable

| Tool | Setting |
|---|---|
| **ruff** | lint + format. Replaces black, isort and flake8. |
| **mypy** | `strict = true` on `domain/` and `application/`. Looser on adapters. |
| **pytest** | `-x --cov`, coverage floor enforced on `domain/` |
| **pre-commit** | ruff, mypy, no-large-files, **no-PDF-committed** |
| **pydantic-settings** | all config from env. No literal ever configures behaviour. |

Type hints are mandatory everywhere. In a codebase built on Protocols, the types
*are* the architecture — an untyped function is invisible to the design.

---

## 15. Definition of done for any checkpoint

- [ ] Dependency rule intact — `domain/` imports nothing outward
- [ ] Ports defined before adapters
- [ ] Domain logic unit-tested without Docker
- [ ] Errors typed, translated at the boundary, never swallowed
- [ ] Structured logs with correlation ids, zero PHI
- [ ] `ruff` clean, `mypy` clean, tests green
- [ ] Edge cases enumerated in a parametrized test, not "handled" in prose
- [ ] No dead code, no speculative abstraction, no `utils.py`
