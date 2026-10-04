# Golden set

Real lab reports plus hand-typed ground truth. The only way to tell whether a
prompt change improved extraction or quietly broke it.

**Nothing in `reports/` or `truth/` is committed.** Both are gitignored, and a
pre-commit hook blocks PDFs and images outright. Health data must never reach git
history — once committed, rewriting history is the only cure.

## Adding a report

1. Drop the file in `reports/`, e.g. `reports/thyrocare-cbc-2024.pdf`

2. Create `truth/thyrocare-cbc-2024.json` by **reading the report and typing what
   it says**. Not by running extraction and correcting it — that bakes the model's
   mistakes into the thing meant to catch them.

```json
{
  "source": "Thyrocare, Mumbai, full body checkup",
  "lab_name": "Thyrocare Technologies Ltd",
  "collected_at": "2024-03-15",
  "pages": [
    {
      "page": 1,
      "rows": [
        {
          "raw_test_name": "Haemoglobin",
          "value_text": "11.2",
          "unit": "g/dL",
          "ref_low": "12.0",
          "ref_high": "15.5"
        },
        {
          "raw_test_name": "S.G.P.T. (ALT)",
          "value_text": "45",
          "unit": "U/L",
          "ref_low": null,
          "ref_high": "40"
        },
        {
          "raw_test_name": "HIV I & II",
          "value_text": "Non Reactive",
          "unit": null,
          "ref_low": null,
          "ref_high": null
        }
      ]
    }
  ]
}
```

### Rules for typing truth

- **Exactly as printed.** `S.G.P.T. (ALT)` not `ALT`. Keep the dots and brackets.
- **`null` when the report prints nothing.** A missing reference range is a fact
  worth measuring — a model that invents one is fabricating the thing we judge
  people against, and only a `null` here catches that.
- **Keep `<` and `>` prefixes.** `<0.5` is not `0.5`.
- **Every row**, including tests you do not recognise and rows whose value is
  genuinely illegible (use `"value_text": null`).

## De-identifying

Cover the name, address and patient ID with a black box before saving. **Leave the
layout, the dates and every result intact** — layout variation between labs is the
entire problem this set exists to measure, so a retyped or reformatted report is
worthless here.

## Running

```
make eval
```

Scored, never pass/fail. Watch **false confidence** above all: a change that lifts
accuracy while raising it has made the product worse.

## How many

Five to ten is enough to be useful. Aim for variety over volume — different
labs, a phone photo as well as a PDF, one multi-page full-body checkup, one
report with an unusual layout. Ten reports from one lab teach you about one lab.
