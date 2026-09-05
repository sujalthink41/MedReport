# V1 Scope

The rule: **one loop, done properly.** Every feature below exists to get a user from
"I have a confusing PDF" to "I understand this and I know what to ask my doctor."

## In scope

### 1. Upload
- PDF and image (JPG/PNG/HEIC), multi-page
- No page limit. Soft guard: reject files > 50MB (abuse protection, not a product limit)
- Original file is stored — always. We may need to re-read it later

### 2. Extraction
Vision model reads the document into strict JSON per row:
`test_name, value, unit, reference_range_low, reference_range_high, page, bbox, confidence`

### 3. Verification (the one agentic loop)
Self-checking agent. See `04-extraction-pipeline.md`.
Key behaviour: when unsure, **say so**. `"couldn't read this row reliably"` is a better
product than a confident wrong number.

### 4. Normalization
The real engineering work. `SGPT` / `ALT` / `Alanine Transaminase` → one canonical test ID.
All units converted to canonical. Without this, trends silently break the moment the
user switches labs.

### 5. Classification
Deterministic code, four bands. See `05-classification.md`.
**The LLM never decides the band.**

### 6. Results view
Grouped by panel, sorted by severity. Mobile-web first.

### 7. Per-marker explanation
What it is, why it's high or low, common associated symptoms, what typically moves it.
Cached by `(canonical_test_id, band)` — we do not regenerate the same Vitamin D
explanation ten thousand times.

### 8. Panel-level insight
Cross-marker pattern reasoning. The "whoa" moment.

### 9. Trend view
Unlocks on the second report. Also our core metric.

### 10. Doctor prep sheet
One page, exportable as image/PDF. The wedge.

### 11. Accounts
Google OAuth, report history.
**Schema supports multiple profiles from day one** (the caretaker use case) —
but V1 ships single-profile UI.

## Explicitly out of scope

| Cut | Why |
|---|---|
| Chat-with-your-report | Tempting, expensive, doesn't drive retention |
| Symptom checker | Different product, much higher risk |
| Prescription / medicine reading | V2 |
| Radiology & imaging reports | Different extraction problem entirely |
| Doctor booking | Not our value |
| Medicine reminders | Not our value |
| Wearables integration | Noise, no signal, at this stage |
| Multi-profile UI | Schema ready, UI in v1.1 |

## Definition of done for V1

A user uploads two reports from two different labs, six months apart, and gets a
correct trend line and a prep sheet they would actually carry to a doctor.
