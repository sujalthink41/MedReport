# ADR 0004 — Two authorization systems, not one

**Status:** accepted · **Date:** 2026-09-05

## Context

MedReport needs access control. The obvious move is "add RBAC" — a `role` column on
`users`, then check it. That single mechanism does not fit the two very different
questions we actually have to answer.

**Question A (99% of requests):** *"Can this user see this report?"*
This is about a **relationship** — user → profile → report. No role is involved. A
person is not "an owner" globally; they are the owner *of a specific profile*.

**Question B (rare):** *"Can this support engineer read LLM traces?"*
This is a **capability** attached to a person regardless of any relationship.

Model A as a role and you get a `role` column that secretly means "owner of
something", which collapses the moment two people share one profile — which is
exactly our primary use case.

## Decision

Two mechanisms, deliberately separate.

**1. Relationship-based access (ReBAC)** via `profile_members`. Every request for
report data resolves through membership, never through a global role.

**2. RBAC** via `permissions` / `roles` / `role_permissions` / `user_roles`, used
**only** for staff capabilities. `user_roles` is empty for ordinary users.

Both are enforced by one policy layer, so call sites ask a single question and never
branch on which mechanism applied.

### Supporting rules

- **Code checks permissions, never role names.** `require(Permission.REPORT_DELETE_ANY)`,
  never `if user.role == "admin"`. Roles are data; adding one is a row, not a deploy.
- **Admin does not mean "reads all health data."** Support gets metadata and traces.
  Raw PHI requires either explicit user grant or a logged, alerted break-glass action.
- **Deny by default.** Absence of a rule is a denial, never a fallthrough.
- **Every staff access to PHI writes an `access_audit` row.**
- **Grants may expire.** Elevated access is temporary by default.

## Consequences

- More tables than a `role` column, and worth it: the membership table *is* the
  caretaker sharing feature from `00-product-brief.md`, so we would build it anyway.
- The policy layer must be a domain service with no framework dependency, so it is
  callable from the API, the pipeline, and a future CLI, and unit-testable without
  a database.
- Permission checks belong in the application layer, not in routers — otherwise the
  same rule would need re-implementing for every entry point.
- Seed data for permissions and system roles becomes a migration concern.
