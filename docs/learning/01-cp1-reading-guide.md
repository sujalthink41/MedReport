# CP1 — Reading Guide

A guide to reading the code you just received, in an order that builds understanding
instead of confusion. Work through it once with the files open. It should take about
an hour, and it is the hour that makes every later checkpoint cheap.

---

## Part 0 — The razor that explains the entire folder structure

Before any file, hold one question in your head:

> **If we threw away every piece of technology we use — FastAPI, Postgres, OpenAI,
> Cloudflare — what would still be true about Baseline?**

Some things survive:

- A lab value compared against a reference range yields a band
- Vitamin D below 20 ng/mL is deficient
- SGPT and ALT are the same test under two names
- Two reports six months apart make a trend
- The owner of a profile may share it; a viewer may not

Every one of those was equally true on paper in 1985. They are **the business**.

Everything else — HTTP routes, SQL tables, S3 buckets, prompt strings — is a
*delivery mechanism*. Replaceable. Temporary. FastAPI will not be the framework of
choice in 2032.

That single question sorts the whole codebase:

| Survives the razor | Dies with the technology |
|---|---|
| `domain/` | `adapters/` |
| `application/` | `api/`, `pipeline/` |

**The folder structure is not organisation. It is a statement about what is permanent
and what is disposable, made structural so it cannot be forgotten.**

---

## Part 1 — Ports: the metaphor to never forget

Your laptop has a USB-C socket.

The laptop maker defined that socket. Every dongle, monitor and charger in the world
adapts *to it*. The laptop does not have "a Logitech port" and "a Dell port" — it has
one port, and the world adapts.

Now the punchline, and it is the part almost everyone gets wrong:

> **The port belongs to the consumer, not the implementer.**

`FileStorage` lives in `domain/ports/` — written by the domain, phrased in the
domain's language: *"store these bytes at this key."*

If instead you put that interface next to `R2Storage`, you have not built a port. You
have built *an R2-shaped hole*, with S3's vocabulary (`Bucket`, `Key`, `ACL`) leaking
through it. Swapping to local disk then still breaks everything, and you will conclude
that "abstractions don't help" — when what actually happened is you pointed the
dependency the wrong way.

**A real port is defined by need. A fake port is defined by whatever library you
happened to pick first.**

---

## Part 2 — Folder by folder

```
app/
├── domain/          survives the razor. no framework, no IO, not even async.
│   ├── models/      entities and value objects — Report, CanonicalValue, Band
│   ├── ports/       Protocols. what the domain NEEDS from the outside world.
│   ├── services/    pure business rules — classification, normalization
│   └── errors.py    our exception vocabulary
│
├── application/     use cases. one file per thing a user can DO.
│   └── use_cases/   UploadReport, ShareProfile, ProcessReport
│
├── adapters/        everything that touches the outside world. all IO lives here.
│   ├── db/          SQLAlchemy — implements the repository ports
│   ├── storage/     R2 and local disk — implement FileStorage
│   ├── llm/         LiteLLM — implements LLMClient
│   └── queue/       Celery — implements TaskQueue
│
├── api/             DRIVING adapter — HTTP calls us
├── pipeline/        DRIVING adapter — the worker calls us
└── core/            config, logging, and the composition root
```

**Driving vs driven** is worth internalising, because it is the distinction the word
"adapter" hides:

- **Driving adapters** (`api/`, `pipeline/`) *call into* the application. They are
  entry points. Swap HTTP for gRPC and only this layer changes.
- **Driven adapters** (`adapters/`) *are called by* the application through ports.
  They are exits. Swap Postgres for anything and only this layer changes.

The application sits in the middle and never learns which is which.

**Why `application/` is separate from `domain/`:**
`domain/` holds rules that are always true. `application/` holds *sequences* — first
hash the file, then check for a duplicate, then store it, then enqueue. Rules are
timeless; sequences are choices about how this product works. Different reasons to
change, different folders.

---

## Part 3 — The seven words, in plain language

`domain` · `use case` · `application` · `port` · `adapter` · `factory` · `pipeline`

These seven words are the entire vocabulary. Until they click, the folders feel
arbitrary. Once they click, you will place any new file correctly without thinking.

### One image that holds all seven: a restaurant

| The word | In a restaurant |
|---|---|
| **Domain** | The recipes. "Carbonara uses egg, not cream." |
| **Use case** | One dish on the menu. "Make one carbonara." |
| **Application** | The menu — everything that can be ordered. |
| **Port** | A hatch in the kitchen wall labelled `EGGS`. |
| **Adapter** | Farm A, Farm B, the frozen warehouse — whoever delivers through that hatch. |
| **Factory** | The blueprint for assembling a kitchen — a real one, or a test kitchen with fake suppliers. |
| **Pipeline** | The assembly line for the 40-minute dish: prep → grill → sauce → plating. |

Hold that image. Now each one properly.

---

### 1. Domain — what the business *is*

**Plain:** the rules that would still be true if you ran this business with pen and
paper.

**Restaurant:** the recipes. "Carbonara uses egg, not cream" is true in Rome and in
Delhi, true whether the order arrived by phone or app, true whichever farm sent the
eggs. The recipe does not know any of those things exist.

**In Baseline:**

```python
# domain/services/classify.py
def classify(value: CanonicalValue, ref: ReferenceRange) -> Band:
    if ref.low is not None and value.amount < ref.low:
        return Band.OUT_OF_RANGE
    ...
```

Give it two numbers, get a band. No HTTP, no Postgres, no OpenAI. This function would
have worked in 1985 and will work in 2045.

**Intent:** protect what is valuable and permanent from what is disposable and
fast-changing. Your clinical logic is the asset. FastAPI is a rental.

**The test:** *could this code run in a plain Python REPL, with no internet, no
database, and no API key?* If yes, it belongs in `domain/`.

---

### 2. Use case — what the business *does*

**Plain:** one complete thing a user can accomplish, start to finish.

**Restaurant:** one dish on the menu. "Make one carbonara" is a *sequence* — boil the
water, fry the guanciale, temper the eggs — that draws on recipes (domain) and
ingredients (ports).

**In Baseline:** `UploadReport` — hash the file, check for a duplicate, store the
original, save the row, enqueue processing.

**The distinction that trips everyone up:**

| | Nature | Example |
|---|---|---|
| **Domain = rules** | always true, not our choice | "Vitamin D under 20 ng/mL is deficient" |
| **Use case = sequence** | our choice about how *this* product works | "When a file arrives, hash it first, then dedupe" |

We could have deduped *after* storing instead. That would be a different product
decision and the same clinical truth. Rules are discovered; sequences are decided.
Different reasons to change → different folders.

**Naming test:** a use case is a **verb phrase a user would actually say**.
`UploadReport`, `ShareProfile`, `GeneratePrepSheet`. If you cannot name it as
something a person *does*, it is not a use case — it is a helper hiding in the wrong
layer.

---

### 3. Application — the menu

**Plain:** the collection of all use cases. The layer that answers "what can this
system do?"

**Restaurant:** the menu. Not the cooking, not the suppliers — the list of things a
customer can order.

**In Baseline:** `application/use_cases/` will hold `upload_report.py`,
`share_profile.py`, `process_report.py`, `generate_prep_sheet.py`.

**Intent, and this is the part people underestimate:** open that folder, read the
filenames, and you know what the product does — without reading one line of
implementation. That is free, permanently-accurate documentation, and it is why use
cases get one file each rather than being bundled into a `ReportService` with fourteen
methods.

A `services/` folder full of god-classes tells you nothing. A `use_cases/` folder
tells you the product.

---

### 4. Port — a labelled socket

**Plain:** a written description of something you *need*, that says nothing about who
provides it.

**Restaurant:** a hatch in the kitchen wall with `EGGS` written above it. The kitchen
built that hatch and defined its size. It does not care which farm turns up.

**In Baseline:**

```python
# domain/ports/services.py
class FileStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
```

Look at the vocabulary: `key`, `bytes`. Not `Bucket`, not `ACL`, not `StorageClass`.
That is the kitchen's language, not the supplier's.

**Intent:** let important code depend on a **promise** instead of a **product**.

**What breaks without it:** `UploadReport` imports `boto3` directly. Now testing
upload needs AWS credentials. Now switching to R2 means editing business logic. Now
the CLI bulk-importer drags S3 in as well. One import, three problems — and none of
them are visible on the day you write it.

---

### 5. Adapter — the plug that fits the socket

**Plain:** real code that fulfils a port using one specific technology.

**Restaurant:** Farm A, Farm B, the frozen warehouse. Different lorries, different
prices, same hatch.

**In Baseline:** `R2Storage` and `LocalDiskStorage` both satisfy `FileStorage`. The
composition root picks one. `UploadReport` never learns which.

**Two directions** — the distinction the word "adapter" hides:

- **Driving adapters** (`api/`, `pipeline/`) *call into* your application. The waiter
  taking a table order, the phone, the delivery app. Entry points.
- **Driven adapters** (`adapters/`) are *called by* your application, through ports.
  The egg supplier. Exits.

**Intent:** keep every "how" in a place where replacing it is a contained act.

**The trap (Liskov):** an adapter that only *mostly* fits is worse than none. If
`LocalDiskStorage` raises `FileNotFoundError` for a missing key while `R2Storage`
returns `None`, your "swappable storage" is a lie that will surface in production.
Same behaviour, same errors — or it is not an adapter.

---

### 6. Factory — a function that assembles a thing

**Plain:** instead of building the object once when the file loads, you write a
function that builds it on demand.

```python
app = FastAPI()                    # built at import. one, forever, shared by all.
def create_app(settings): ...      # built when asked. as many as you like.
```

**Restaurant:** not one kitchen that exists the moment the building opens, but a
blueprint you can use to assemble the real kitchen — or a test kitchen wired to fake
suppliers.

**Three concrete payoffs, all of which you already have:**

1. **Tests get their own app.** `tests/conftest.py` builds one per test with its own
   settings. One test can run in production mode and the next in local mode, in the
   same run. With a module-level `app`, every test shares one object and they leak
   into each other.
2. **No import side effects.** `import app.main` does nothing at all. With
   module-level construction, merely importing the module to read a type annotation
   can open connection pools.
3. **It is the composition root.** One visible function is the only place that
   answers "which storage? which model? which repository?" When you swap R2 for S3 at
   CP10, this is the file that changes — and the *only* file that changes.

**The general lesson:** whenever construction involves a choice or configuration,
wrap it in a function. `create_app`, `create_llm_client`, `build_graph`. Objects built
at import time are objects you cannot configure, cannot vary, and cannot test.

---

### 7. Pipeline — an assembly line for long work

**Plain:** work too slow for a single request, broken into named stages, each doing
one thing and handing forward.

**Restaurant:** the 40-minute dish. Prep station → grill → sauce → plating. Separate
stations — and if plating goes wrong you re-plate, you do not re-grill the steak.

**In Baseline:** `ingest → extract → verify → merge → normalize → classify → explain
→ reason → prep_sheet`.

**Why stages instead of one long function:**

| Property | What it buys |
|---|---|
| **Checkpointed** | Failure at `explain` resumes at `explain` — not re-running a vision model over 25 pages. Slow and expensive, avoided. |
| **Parallel** | `extract` fans out per page, so 25 pages take about as long as 3. |
| **Per-stage models** | Vision model for `extract`, cheap model for `explain`, strongest for `reason`. |
| **Individually testable** | `merge` is tested with fabricated rows and no model call at all. |
| **Visible** | Named stages mean you can show a real progress indicator. |

**And the part that ties it back:** the pipeline is a **driving adapter**. It *calls*
the domain. The `classify` node calls the exact same pure `classify()` function an API
route could call. The domain has no idea a pipeline exists.

That is why, at CP21, wiring the classification service into the pipeline will require
**zero changes** to that service. Watch for it — that moment is the architecture
paying you back.

---

### How they talk to each other — one real request

Follow a single upload all the way down:

```
  Someone taps "Upload" in the browser
        │
        v
  api/v1/routers/reports.py                     DRIVING ADAPTER
        │   validates the HTTP shape, nothing more
        v
  application/use_cases/upload_report.py        USE CASE — the sequence
        │
        ├─► domain/models/report.py             DOMAIN — the rules
        │       Report.new() enforces its own invariants
        │
        ├─► domain/ports/services.py            PORT — "I need storage"
        │       FileStorage.put(...)
        │            └─► adapters/storage/r2.py      DRIVEN ADAPTER — actually R2
        │
        ├─► domain/ports/repositories.py        PORT — "I need persistence"
        │            └─► adapters/db/reports.py      DRIVEN ADAPTER — actually Postgres
        │
        └─► domain/ports/queue.py               PORT — "I need to enqueue"
                     └─► adapters/queue/celery.py    DRIVEN ADAPTER — actually Celery
                              │
                              v
                     pipeline/graph.py          DRIVING ADAPTER — the worker
                              │   calls back into the same domain services
                              v
                         ingest → extract → ... → prep_sheet
```

Read that top to bottom and notice: **the use case in the middle names three ports and
zero technologies.** It does not know it is behind HTTP. It does not know storage is
R2. Replace every box on the right-hand edge and the middle is untouched.

That is the whole idea, in one picture.

---

### The seven, one line each

| Word | One line | Lives in |
|---|---|---|
| **Domain** | Rules that outlive the technology | `app/domain/` |
| **Use case** | One thing a user can do, as a sequence | `app/application/use_cases/` |
| **Application** | The menu of everything the system does | `app/application/` |
| **Port** | A need, written down, with no supplier named | `app/domain/ports/` |
| **Adapter** | A specific technology fulfilling a port | `app/adapters/`, `app/api/`, `app/pipeline/` |
| **Factory** | A function that assembles a configured object | `app/main.py`, `app/core/` |
| **Pipeline** | Long work as resumable named stages | `app/pipeline/` |

---

### "Where does this file go?" — the decision you will make constantly

```
Does it touch the network, the disk, or a third-party library?
   YES → adapters/     (or api/ | pipeline/ if it is an entry point)
   NO  ↓

Is it a sequence of steps a user would name as an action?
   YES → application/use_cases/
   NO  ↓

Is it a rule that would be true with pen and paper?
   YES → domain/services/   or   domain/models/
   NO  ↓

Is it a need you want somebody else to fulfil?
   YES → domain/ports/
   NO  → you are probably describing configuration → core/
```

---

### The two mistakes everyone makes

**1. Rules leaking into adapters.**
A permission check written inside a router. A business calculation written as a SQL
`CASE` statement. It works — and now that rule exists in exactly one entry point, so
the worker and the CLI silently behave differently. *Rules belong in the middle.*

**2. Ports that mirror a library.**
Writing `class Storage: def put_object(self, Bucket, Key, Body)`. That is not an
abstraction, it is S3 wearing a hat. The giveaway is vocabulary — if your port speaks
the vendor's nouns, the vendor still owns you. *Ports are written in your language,
describing your need.*

---

## Part 4 — The reading order

Read in this order. It is not folder order — it is the order in which each file makes
the next one make sense.

### 1. `pyproject.toml` — the rules of the game (10 min)

Read the `[tool.ruff]` and `[tool.mypy]` blocks especially.

**Notice:**
- `DTZ` bans naive `datetime.now()`. Your whole trend feature is time arithmetic; a
  naive datetime compared against an aware one is a crash or a silently wrong interval
- `mypy strict` applies **only** to `domain` and `application`. Strictness everywhere
  just teaches people to write `# type: ignore`
- `disallow_any_explicit` on the domain: no escape hatch in the part that must be right
- Dependencies are added per checkpoint, not upfront

**Answer this:** why is `ignore_missing_imports = true` acceptable for adapters but
unthinkable for the domain?

### 2. `.importlinter` — the architecture, as law (10 min)

**This is the most important file in the repo.** Read it slowly.

**Notice:**
- The `layers` contract encodes direction. Read bottom-up: domain knows nothing;
  application may use domain; api and pipeline sit above both
- The `:` between `app.api` and `app.pipeline` means *siblings* — neither may import
  the other. Two entry points that must never learn about each other
- The `forbidden` contracts name real libraries. `fastapi` inside `domain/` is not a
  style opinion; it is a build failure

**The idea to take away:** architecture that lives only in a document decays within
weeks, because there is no moment where violating it hurts. Architecture that fails
the build survives, because the pain arrives in seconds instead of months.

**Answer this:** what would go wrong, concretely, if `app/domain/` were allowed to
import `sqlalchemy`?

### 3. The folder tree itself (5 min)

`find backend/app -type d`

Now that you have read the contracts, the empty folders have meaning. Walk the tree
and say out loud, for each one: *driving, driven, or core?*

### 4. `app/core/config.py` (10 min)

The smallest real file. Four ideas packed into forty lines.

**Notice:**
- **12-factor config.** Every setting comes from the environment. The same Docker
  image runs in dev, staging and prod without rebuilding — that is the entire point
- `frozen=True`: settings cannot be mutated at runtime. A config that changes
  mid-process is a bug that only appears under load
- `@lru_cache` is doing two jobs: it reads settings once per process, **and** it gives
  tests a seam (`get_settings.cache_clear()`). A cache used as a test seam is a
  pattern you will reuse
- `StrEnum` over string literals: `Environment.PRODUCTION` is checkable by mypy;
  `"production"` is checkable by nobody, and `"prodution"` ships

**Answer this:** why is `env_prefix = "BASELINE_"` worth the extra typing?

### 5. `app/api/v1/routers/health.py` (10 min)

**Notice:**
- Liveness and readiness answer different questions. `/health` must check *nothing
  external* — if a database blip made it fail, the orchestrator would restart the
  container, turning a brief outage into a restart loop. `/ready` checks dependencies
  and pulls the instance out of the load balancer while leaving it alive to recover
- `response_model` on every route. The response shape is a **contract**, declared, not
  whatever a dict happened to contain that day
- `Literal["ok"]` — the type system expresses that this field has exactly one legal
  value

**Answer this:** a colleague suggests `/health` should check the database "so we know
it's really healthy." What do you say?

### 6. `app/main.py` — the composition root (15 min)

The most conceptually loaded file here.

**Notice:**
- `create_app()` is a **factory**, not `app = FastAPI()` at module level. Module-level
  means importing `app.main` has side effects and every test shares one config. A
  factory means each test builds its own app with its own settings
- This is the **composition root** — the one place that knows which concrete
  implementations exist. From CP10 it will pick `R2Storage` or `LocalDiskStorage`.
  Nothing else in the codebase will ever know that choice was made
- `lifespan` — connection pools are created once per process, not per request
- Docs are disabled in production. Interactive docs advertise every endpoint and
  schema to anyone who finds the URL

**Answer this:** what specific problem does the app factory solve that a module-level
`app` cannot?

### 7. `tests/conftest.py` (10 min)

**Notice:**
- Fixtures compose: `settings` → `app` → `client`. Ask for `client` and pytest builds
  the chain. This *is* dependency injection, and you now know it by using it
- `ASGITransport` calls the app in-process. No socket, no port, no server, no flake.
  Tests run in 30 milliseconds
- Read the docstring: no database, no Docker, no network. Domain and application tests
  must stay that way — the day they need a container, the dependency rule has broken
  somewhere and this file is your alarm

**Answer this:** why is the `app` fixture function-scoped rather than session-scoped?

### 8. `docker-compose.yml`, `Makefile`, `.pre-commit-config.yaml` (10 min)

**Notice:**
- **Healthchecks, not `sleep 10`.** Declare the condition; let the system wait for it.
  "Wait for a state" beats "wait for a duration" everywhere in engineering
- Named volumes: `make down` stops containers, data survives. Deleting data must be
  explicit
- The `Makefile` is a **task interface**. `make check` means the same thing on your
  laptop and in CI. Nobody memorises flags; nobody runs a different command than CI
- The pre-commit hook **hard-blocks** committing PDFs and images. Health data in git
  history can only be removed by rewriting history — so it is a block, not a warning

**Answer this:** why is `make check` valuable even though you could type the four
commands yourself?

---

## Part 5 — Concept inventory

Everything you have now touched, named properly. If you can define each in one
sentence *and* say what it costs, you understand CP1.

| Concept | One-line meaning |
|---|---|
| **Hexagonal architecture** | Business logic in the centre; technology at the edges, reachable only through interfaces |
| **The dependency rule** | Source-code dependencies point inward, always |
| **Port** | An interface defined by the consumer, describing a need |
| **Adapter** | A concrete implementation of a port |
| **Driving vs driven** | Things that call us vs things we call |
| **Dependency inversion** | Both sides depend on the abstraction, not on each other |
| **Composition root** | The single place concrete implementations are chosen |
| **Application factory** | Build the app in a function so nothing happens at import time |
| **12-factor config** | Configuration comes from the environment, never from code |
| **Executable architecture** | Design rules enforced by tooling, not by memory |
| **Shift left** | Catch errors as early as possible — type error beats production bug |
| **Liveness vs readiness** | "Restart me" vs "stop sending me traffic" |
| **Fixture composition** | Test dependencies built by declaring what you need |
| **Declarative infrastructure** | State the desired state; let the system reach it |

---

## Part 6 — Four drills

Reading teaches recognition. Only doing teaches understanding. Each takes minutes.

**Drill 1 — Break the rule on purpose.**
Add `import sqlalchemy` to a file in `app/domain/`. Run `make arch`. Watch it fail and
read the message. Then delete it.
*Learn:* the rule is real, and violating it is trivially easy without the linter. That
is exactly why the linter exists.

**Drill 2 — Break a sibling rule.**
Add `from app.pipeline import nodes` to a file in `app/api/`. Run `make arch`.
*Learn:* why two entry points must not know about each other.

**Drill 3 — Count the blast radius.**
For each change below, predict the number of files touched, then check yourself:

| Change | Your guess | Should be |
|---|---|---|
| Swap Cloudflare R2 for AWS S3 | ? | 1 new adapter + 1 line in the composition root |
| Rename a database column | ? | ORM model + mapper. API untouched |
| Add a `/v2` API | ? | New router package. Domain untouched |
| Change the borderline band from 10% to 5% | ? | 1 domain service + its test |
| Swap GPT for Claude | ? | 1 config value |

*Learn:* this is the actual payoff. Everything else in CP1 exists to make this table
true. **This is also the question senior engineers ask that juniors don't.**

**Drill 4 — The explain-back test.**
Without looking, explain to someone (or a wall) in under two minutes:
*Why does `FileStorage` live in `domain/ports/` and not next to `R2Storage`?*

If you can answer that cleanly, you understand ports. If you can't, re-read Part 1 —
it is the single highest-value idea in the whole codebase.

---

## Part 7 — What this has to do with being senior

The gap between mid and senior is not knowing more syntax. It is which question you
ask by reflex.

| Mid asks | Senior asks |
|---|---|
| Does it work? | What breaks when this changes? |
| How do I build this? | Who maintains this in two years? |
| Which library is best? | What happens when we have to replace it? |
| Is it fast? | Where is the bottleneck when we are 100× bigger? |
| Did the tests pass? | What would this test still miss? |
| It's done | What did I make harder for the next person? |

Every practice in this repo is the second column made structural — turned from a habit
that requires vigilance into a property the build enforces.

**And the senior move you should copy immediately:** notice that ruff rejected two of
my lines in CP1 — a pointless ternary and an unused parameter. The system caught them
in seconds. Building a system that catches *your own* mistakes is worth far more than
trying to make fewer of them.

---

## When you're ready

Come back with:
1. Your answers to the seven **Answer this** questions
2. Your guesses from Drill 3, and where you were wrong
3. Anything that felt arbitrary — that feeling usually marks a decision I explained
   badly, or one that is genuinely worth reversing
