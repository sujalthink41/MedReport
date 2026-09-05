# Data Model

Postgres. Illustrative DDL — the source of truth is Alembic migrations.

## Identity

```
users
  id, google_sub (unique), email, name, created_at, last_login_at

profiles                       -- multi-profile from day one (caretaker use case)
  id, user_id -> users
  display_name                 -- "Me", "Amma", "Dad"
  date_of_birth, sex           -- REQUIRED: reference ranges depend on both
  relationship                 -- self | parent | child | spouse | other
  created_at
```

V1 ships single-profile UI, but every report hangs off a `profile_id`, never a
`user_id`. Adding the UI later is then purely a frontend change.

## Authorization

Two distinct systems. See [ADR 0004](adr/0004-two-authorization-systems.md).

### Relationship-based access — 99% of requests

```
profile_members               -- who can act on whose profile
  id, profile_id -> profiles, user_id -> users
  role                        -- owner | caregiver | viewer
  invited_by, accepted_at, created_at
  UNIQUE (profile_id, user_id)
```

This table *is* the sharing feature. Two siblings both caring for a parent are two
rows. Every read of a report resolves through it, never through a global role.

An owner always exists; deleting the last owner is forbidden.

### RBAC — staff capabilities only

```
permissions
  id                          -- 'report:read_any', 'dictionary:write'
  description

roles
  id, name, is_system         -- system roles cannot be deleted
  description

role_permissions
  role_id, permission_id
  UNIQUE (role_id, permission_id)

user_roles                    -- GLOBAL roles. staff only. normally empty.
  user_id, role_id, granted_by, granted_at, expires_at
  UNIQUE (user_id, role_id)
```

`expires_at` is deliberate: elevated access should be temporary by default.

**Seeded roles:** `support` (metadata + traces, no values), `clinical_curator`
(dictionary, thresholds, critical values), `admin` (user and role administration —
still not raw PHI).

### Audit — mandatory, not optional

```
access_audit
  id, actor_user_id, action, resource_type, resource_id
  subject_profile_id          -- whose data was touched
  via                         -- membership | role | break_glass
  reason                      -- required for break_glass
  request_id, ip, user_agent, created_at
```

Every staff access to PHI writes a row here. Break-glass access additionally
requires a typed reason and raises an alert. Append-only; no updates, no deletes.


## Reports

```
reports
  id, profile_id -> profiles
  storage_key                  -- R2 object key of the original
  mime_type, size_bytes, page_count, sha256   -- sha256 dedupes re-uploads
  lab_name                     -- extracted, nullable
  collected_at                 -- sample collection date, NOT upload date
  reported_at
  status                       -- queued|processing|complete|partial|failed
  failure_reason
  graph_thread_id              -- LangGraph checkpoint thread
  created_at

  UNIQUE (profile_id, sha256)  -- same file twice is the same report
```

`collected_at` drives every trend. Upload date is meaningless — people upload
three years of reports in one sitting.

## Observations — the core table

One row per measured parameter.

```
observations
  id, report_id -> reports, profile_id -> profiles

  -- as printed
  raw_test_name, raw_unit, raw_value_text, ref_text, lab_flag
  page, bbox, extraction_confidence

  -- resolved
  canonical_test_id -> canonical_tests   -- nullable if unmapped
  value_canonical                        -- numeric, in canonical unit
  unit_canonical
  ref_low, ref_high
  ref_source                             -- lab | fallback | none
  value_kind                             -- numeric | qualitative | titre

  -- computed by classify (code only)
  band                                   -- normal|borderline|out_of_range
                                         -- |needs_attention|unknown|unreadable
  position_in_range                      -- 0..1, for the UI

  created_at
  UNIQUE (report_id, page, canonical_test_id)   -- idempotent reprocessing
```

Both raw and resolved values are kept. When the alias dictionary improves, old
reports can be re-normalized from `raw_test_name` without re-reading the PDF.

## Clinical dictionary — the project's real asset

```
canonical_tests
  id                           -- 'alt', 'hba1c', 'ferritin'
  display_name, panel          -- 'Liver Function', 'Thyroid'
  canonical_unit
  sidedness                    -- two_sided | upper_only | lower_only
  value_kind
  significance_rank            -- drives result sort order

test_aliases
  canonical_test_id, alias_normalized   -- 'sgpt', 'alanine transaminase'
  UNIQUE (alias_normalized)

unit_conversions
  canonical_test_id, from_unit, to_unit, factor
  -- per-analyte: factors depend on molecular weight, not a global formula

reference_ranges              -- LAYER 2 fallback
  canonical_test_id, sex, age_min, age_max, unit, low, high, source

guideline_thresholds          -- LAYER 3 override, ~30 markers
  canonical_test_id, label     -- 'prediabetes', 'deficient'
  comparator, value, unit
  band_override
  guideline_source             -- 'ADA 2025' — always cited

critical_values               -- code-driven, never LLM-written
  canonical_test_id, comparator, value, unit, message_template
```

```
unmapped_test_names           -- the dictionary's growth queue
  raw_name_normalized, occurrences, first_seen_at, resolved_to
```

Never silently drop an unrecognized test. Log it; it becomes tomorrow's alias.

## Generated content

```
explanation_cache             -- shared across ALL users
  canonical_test_id, band, locale, content, model, created_at
  UNIQUE (canonical_test_id, band, locale)

insights                      -- panel-level reasoning, per report
  report_id, kind, title, body, related_observation_ids[]

trend_flags
  profile_id, canonical_test_id
  direction, pct_change, span_days, from_value, to_value
  first_report_id, latest_report_id

prep_sheets
  report_id, content_json, rendered_key, created_at
```

`explanation_cache` is keyed on `(test, band)` only — no PHI in it, which is exactly
why it can be shared globally and why per-marker copy costs almost nothing at scale.

`insights` and `prep_sheets` DO contain PHI. Different table, different handling.

## Observability

```
llm_traces
  id, report_id, node, model
  prompt, response               -- contains PHI; stays on our infra
  prompt_tokens, completion_tokens, cost_usd, latency_ms
  status, error
  created_at
```

Written by one decorator around the LiteLLM call. The day a user says "it showed my
haemoglobin wrong", this table is the only way to find out why.

## Indexes that matter

```
observations (profile_id, canonical_test_id, created_at)   -- trend queries
observations (report_id)                                   -- report view
reports (profile_id, collected_at DESC)                    -- history
test_aliases (alias_normalized)                            -- normalize hot path
```
