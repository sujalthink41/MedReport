# Tech Stack — Decisions

## Locked

| Layer | Choice | Why |
|---|---|---|
| API | **FastAPI** | async throughout, Pydantic native |
| DB | **Postgres 16** | single source of truth |
| Migrations | **Alembic** | |
| Cache + broker | **Redis** | |
| Job queue | **Celery** | battle-tested, Flower for monitoring |
| Orchestration | **LangGraph** | per-page fan-out + checkpointing |
| Model access | **LiteLLM** → GPT | one string to swap providers |
| Validation | **Pydantic v2** | the extraction schema is a core asset |
| Auth | **Google OAuth** (Authlib) | JWT sessions |
| File storage | **Cloudflare R2** | S3 API, no egress fees |
| Frontend | **Next.js** | mobile-web first |
| Errors | **Sentry** | free tier is plenty |

## Rationale on the non-obvious calls

### No SQLite
Two databases means two SQL dialects and bugs that only appear in production.
Postgres in Docker locally — same effort, zero divergence.

### LangGraph, not raw Python
We have one genuinely long job (a 25-page report) that must fan out across pages,
checkpoint, and resume from step 9 rather than restart. That is precisely what
LangGraph's durable state is for. Without that requirement we would not use a framework.

**Not LangChain classic** — abstraction tax, thin benefit.
**Not Google ADK** — pulls toward Google infra we don't want, given GPT.

### Celery over ARQ
Chosen for ecosystem maturity and monitoring.
**Known gotcha:** Celery is sync-first. Async LangGraph/LiteLLM calls need an
`asyncio.run()` bridge inside the task. Wrap it once in a decorator, never think
about it again.

### LiteLLM
Provider churn is fast and we are not marrying one. All model calls go through a
single seam. Swapping GPT → Claude → Gemini is a config change.

We will likely use **different models for different nodes** —
vision extraction and clinical reasoning are different jobs.

### Observability — two phases

**Now:** an `llm_traces` Postgres table, written by one decorator around the LiteLLM
call: `report_id, node, model, prompt, response, tokens, latency, cost, created_at`.
~20 lines, zero ops.

**Later:** Langfuse, once there is real traffic. Free to license, but self-hosting it
needs ClickHouse + MinIO + its own Postgres and Redis — roughly 4 extra containers and
~4GB RAM. Free in money, not in ops. Our traces table is the same shape, so the
migration is clean.

**Not Langfuse Cloud** — it would ship health data to a third party.

## Deployment

**Single VPS + Docker Compose.** Hetzner CX22/CPX21, ~EUR 4-8/month, 4GB RAM.

```
compose:  api | worker (celery) | beat | postgres | redis | caddy
```

- **Caddy** in front for automatic HTTPS
- **Frontend** on Vercel free tier, pointed at the API
- **CI:** GitHub Actions → build image → push → `docker compose pull && up -d` over SSH

**Why not Railway/Render:** fine until you run 4-5 services, then the bill climbs and
you still can't self-host anything heavy. On a VPS, `docker compose up` gives you the
whole system and local dev is byte-identical to prod.

**Trade-off, stated plainly:** we own backups and updates.
Mitigation: nightly `pg_dump` to R2 via cron. Health data you cannot restore is a
product-ending event. This is set up in week one, not later.
