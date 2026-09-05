# CP2 — Reading Guide

Short one. Four files, five ideas, five drills. About 30 minutes.

---

## What "production grade" actually means

Not "more layers". Not "more code". A system is production grade when you can answer
yes to these five questions:

1. **Can I debug it at 3am from the logs alone?** — I cannot attach a debugger to
   production, and the user is not going to reproduce it for me.
2. **Does it fail safely?** — when something goes wrong, does it leak, or does it
   contain?
3. **Does it degrade instead of collapsing?** — one bad page in a 25-page report
   should not fail the report.
4. **Can a machine decide what to do with a failure?** — retry or not, 4xx or 5xx,
   without a human reading the message.
5. **Are the safety properties enforced, or merely intended?** — "we agreed not to
   log patient data" is an intention. A processor that strips it is enforcement.

CP2 is the checkpoint that answers 1, 2, 4 and 5. Keep those five questions in mind
while reading — every choice in these files is aimed at one of them.

---

## Reading order

### 1. `app/domain/errors.py` (10 min)

**Notice:**
- The hierarchy splits on **who is at fault**, not on feature area
- Errors take `**context` and keep it as a dict — data, not a sentence
- `retryable` is a `ClassVar` on the class, not a value on the instance
- `LLMInvalidOutputError` overrides it to `False` despite being infrastructure

**Answer this:** why is `DuplicateReportError` an exception at all, when the upload use
case is going to return the existing report rather than fail?

### 2. `app/core/logging.py` (15 min)

The densest file so far. Read the docstring first, then `redact_sensitive`, then
`configure_logging` from the bottom up.

**Notice:**
- `shared_processors` is a **list of functions applied in order** — a pipeline
- `merge_contextvars` is first, `redact_sensitive` is last before rendering
- `foreign_pre_chain` is what drags uvicorn's logs into the same shape
- `_scrub` is depth-capped

**Answer this:** why does redaction sit *before* the renderer rather than inside it?

### 3. `app/core/config.py` (5 min)

**Notice:** `use_json_logs` is a **derived** setting, not a stored one. `log_json` is
`bool | None`, and `None` means "decide from the environment".

**Answer this:** what would go wrong if `use_json_logs` were a plain env var that
every deployment had to set correctly?

### 4. `tests/core/test_logging.py` (10 min)

**Notice:** the redaction tests are written like security tests — they try to smuggle
PHI through in a nested dict, in a list of dicts, and with different capitalisation.
Not "does the happy path work" but "how would an attacker, or a careless colleague,
get past this?"

**Answer this:** which realistic bypass is *not* covered by these tests?

---

## The five ideas

### 1. Organise errors by fault, not by feature

The instinct is `ReportError`, `ProfileError`, `UploadError` — grouped by area of the
codebase. Ours groups by **who is to blame**:

```
DomainError          the caller asked for something invalid   ->  4xx, never retry
InfrastructureError  something we depend on failed            ->  5xx, retry
```

Why this is the better cut: **two unrelated systems can now make decisions
automatically.** The HTTP layer (CP3) picks a status code from it. The retry
decorator (CP17) picks a policy from it. Neither has to enumerate every error type,
and adding a new error means it is routed correctly the day it is written.

Feature grouping gives you neither, and you end up hand-writing the retry decision at
every single call site — which is where inconsistency lives.

### 2. Errors carry data, not sentences

```python
raise ProfileNotFoundError(profile_id=pid, requested_by=actor)
raise Exception(f"profile {pid} not found for {actor}")
```

The first can be logged as structured fields, filtered in a dashboard
(`code=profile_not_found`), counted, alerted on, and mapped to a status code. The
second can only be printed, and to get the id back out you would have to parse your
own error message with a regex.

Also note the message is built from **sorted** keys, so two occurrences of the same
error produce byte-identical strings. Log aggregators group by message — unsorted keys
would scatter one problem across several buckets.

### 3. Logs are data, not prose

```python
log.info("observation_classified", test="hba1c", band="borderline")   # a record
log.info(f"Classified hba1c as borderline")                           # a sentence
```

The first lets you ask `band=out_of_range AND ref_source=fallback` — every case where
we used a generic range and flagged someone. That is a real question you will ask.
The second can only be answered with a regex you invent under pressure.

The habit to build: **the event name is a constant, everything variable is a keyword
argument.** If you are formatting a string into a log call, you are throwing away the
data.

### 4. Correlation is what makes logs usable at all

```python
bind_context(request_id="req-7f3a")     # once, at the edge
...
log.info("report_queued", report_id="r-12")     # never mentions request_id
```

Yet the output carries it:

```json
{"event": "report_queued", "report_id": "r-12", "request_id": "req-7f3a", ...}
```

This is `contextvars` — like a thread-local, but correct for async, so a thousand
concurrent requests each get their own copy instead of overwriting one global.

**Why it matters:** without it, debugging one failed report in production means
grepping timestamps across the API, the queue and the worker and *guessing* which
lines belong together. With it, one filter on `report_id` gives you the entire story.

The corollary is `clear_context()`. Workers reuse event loops. Miss the clear and one
report's id leaks into the next report's logs — which is worse than no correlation at
all, because now you trust something wrong.

### 5. Enforce, don't intend

This is the big one, and it generalises far beyond logging.

> "We agreed not to log patient data" is an **intention**. It survives about three
> sprints.
>
> A processor in the chain that strips it is **enforcement**. It survives forever,
> including the intern's first pull request.

Redaction is one function, in one list, applied to every log line ever written —
console, JSON, whatever sink comes later. There is no code path around it.

You have now seen this same move three times:

| Intention | Enforcement |
|---|---|
| "domain shouldn't import sqlalchemy" | `.importlinter` fails the build |
| "don't commit patient PDFs" | a pre-commit hook that blocks it |
| "don't log patient data" | a redaction processor |

**Whenever you catch yourself writing a rule in a document, ask what would make it
impossible to break instead.** That instinct, more than any pattern, is what separates
systems that stay clean from systems that decay.

---

## Five drills

**1. See redaction work.**
```
uv run python -c "
from app.core.config import Settings, Environment
from app.core.logging import configure_logging, get_logger, bind_context
configure_logging(Settings(environment=Environment.PRODUCTION))
bind_context(request_id='req-1')
get_logger('x').info('observation', test='hba1c', value=6.1, band='borderline')
"
```
Note `value` is gone, `test` and `band` survived, `request_id` appeared unasked.

**2. Find the denylist's weakness.**
Log a key that is clearly PHI but not in `SENSITIVE_KEYS` — try `haemoglobin=13.2` or
`patient_email=...`. Watch it leak.
*Learn:* a denylist is the weaker defence. We chose it deliberately (an allowlist would
swallow the diagnostic fields we need), which means the list must be reviewed whenever
a new field is added. Know the weakness of your own design.

**3. Break correlation.**
Comment out `clear_context()` in `test_context_can_be_cleared_between_requests`. Watch
the test fail, and read what it is telling you about worker reuse.

**4. Use the taxonomy.**
```python
from app.domain.errors import ProfileNotFoundError, LLMUnavailableError, LLMInvalidOutputError
for e in (ProfileNotFoundError(), LLMUnavailableError(), LLMInvalidOutputError()):
    print(type(e).__name__, e.code, "retry?" , e.retryable)
```
*Learn:* CP17's retry decorator will be roughly `if error.retryable: back off and try
again`. That is the entire policy, because the taxonomy already did the thinking.

**5. Audit your own habit.**
```
grep -rn 'log\.\(info\|warning\|error\)(f"' app/
```
Should return nothing. Run this on any codebase you join — the ratio of f-string logs
to structured logs tells you a lot about how debuggable it is.

---

## What to carry into CP3

CP3 wires this to HTTP: a middleware that generates `request_id` and binds it, and one
exception handler mapping `MedReportError` subclasses onto status codes.

Watch for this: **the routers will contain zero try/except.** The error taxonomy plus
one handler does all the work. If you ever find yourself writing `try/except` inside a
route, it means either the hierarchy is missing a type or the handler is missing a
mapping — the fix is upstream, never in the route.
