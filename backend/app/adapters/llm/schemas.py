"""Structured output shapes.

Pydantic, and deliberately in ``adapters/`` rather than ``domain/``: these describe
the wire format of an external service, not business concepts. The domain stays
free of Pydantic, and changing a provider's response shape never touches it.

Every field here is **as printed**. No normalising, no converting, no judging —
that all happens later in deterministic code. The model's only job is to read
what is on the page.
"""

from pydantic import BaseModel, Field


class ExtractedRow(BaseModel):
    """One line of a lab report, transcribed exactly."""

    raw_test_name: str = Field(
        description="The test name EXACTLY as printed, including punctuation, "
        "capitalisation and any bracketed synonym. Do not expand abbreviations, "
        "do not correct spelling, do not translate."
    )

    value_text: str | None = Field(
        default=None,
        description="The result EXACTLY as printed: '11.2', 'Negative', '<0.5', "
        "'1:40', 'Not Detected'. Keep any < or > prefix. Null ONLY if the value "
        "is genuinely unreadable - never guess a digit.",
    )

    unit: str | None = Field(
        default=None,
        description="The unit as printed, e.g. 'g/dL', 'mg/dL', '%', 'cells/uL'. "
        "Null if no unit is shown. Do not infer a unit that is not on the page.",
    )

    ref_text: str | None = Field(
        default=None,
        description="The reference range EXACTLY as printed: '12.0 - 15.5', "
        "'< 200', 'Negative', '0.4-4.0'. Null if the report prints none. "
        "NEVER supply a range from your own knowledge - a missing range is "
        "information, an invented one is a fabrication.",
    )

    ref_low: float | None = Field(
        default=None,
        description="Lower bound as a number, ONLY if ref_text contains one. "
        "For '< 200' this is null. For 'Negative' this is null.",
    )
    ref_high: float | None = Field(
        default=None,
        description="Upper bound as a number, ONLY if ref_text contains one. "
        "For '> 40' this is null.",
    )

    lab_flag: str | None = Field(
        default=None,
        description="The lab's own marker if printed: 'H', 'L', 'HIGH', 'LOW', "
        "'*'. Null if absent. Report it, do not compute it.",
    )

    panel: str | None = Field(
        default=None,
        description="The section heading this row sits under, e.g. "
        "'LIVER FUNCTION TEST', 'COMPLETE BLOOD COUNT'. Null if none.",
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="How certain you are that this row is transcribed correctly. "
        "Below 0.7 means a human should check it. Be honest: a wrong number "
        "reported confidently is the worst outcome here, far worse than "
        "admitting uncertainty.",
    )

    unreadable_reason: str | None = Field(
        default=None,
        description="If confidence is low, say why in a few words: 'blurred', "
        "'cut off at page edge', 'overlapping text', 'handwritten'.",
    )


class ExtractedPage(BaseModel):
    """Everything on one page of a report."""

    rows: list[ExtractedRow] = Field(
        description="Every measured parameter on the page, in the order printed. "
        "Include rows you do not recognise. Include rows whose value you could "
        "not read - with value_text null. A missing row is invisible to everyone; "
        "a row marked uncertain is a question a human can answer."
    )

    lab_name: str | None = Field(
        default=None,
        description="The laboratory's name, if printed on this page.",
    )
    collected_at: str | None = Field(
        default=None,
        description="Sample collection date as printed, ISO format YYYY-MM-DD if "
        "you can determine it unambiguously. This is the date the blood was "
        "drawn - NOT the report date, print date, or registration date. If the "
        "page shows several dates and you cannot tell which is collection, "
        "return null rather than guessing.",
    )
    reported_at: str | None = Field(
        default=None, description="Report/release date, ISO format, if printed."
    )

    patient_sex: str | None = Field(
        default=None,
        description="'male', 'female' or null. Only if printed on the page.",
    )
    patient_age_years: int | None = Field(default=None, description="Only if printed on the page.")

    page_notes: str | None = Field(
        default=None,
        description="Anything about this page a human should know: 'page is a "
        "continuation of a table started on the previous page', 'bottom third "
        "is cut off', 'this page is a methodology appendix with no results'.",
    )


class ProposedMapping(BaseModel):
    """What one printed test name should be called internally."""

    raw_test_name: str = Field(description="The name exactly as given to you.")

    canonical_id: str = Field(
        description="A short lowercase snake_case identifier for this test, e.g. "
        "'alt', 'hba1c', 'ldl_cholesterol', 'tsh'. Use the most widely recognised "
        "short form. This id is permanent once assigned, so prefer the obvious "
        "choice over a clever one."
    )
    display_name: str = Field(
        description="A clear name for a patient to read, e.g. 'Alanine Transaminase (ALT)'."
    )
    panel: str | None = Field(
        default=None,
        description="Clinical grouping, e.g. 'Liver Function', 'Lipid Profile', "
        "'Thyroid', 'Complete Blood Count'.",
    )
    sidedness: str = Field(
        description="'two_sided' if both a high and a low result are clinically "
        "meaningful (haemoglobin, sodium). 'upper_only' if only high matters "
        "(LDL, triglycerides, ESR) - a low result must never be flagged. "
        "'lower_only' if only low matters."
    )
    synonyms: list[str] = Field(
        default_factory=list,
        description="Other names the same test is printed under, e.g. for ALT: "
        "['SGPT', 'Alanine Aminotransferase', 'ALAT'].",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Below 0.7 means you are unsure this is a standard test or "
        "unsure of the mapping. An unsure mapping is stored for review rather "
        "than trusted.",
    )


class ProposedMappings(BaseModel):
    mappings: list[ProposedMapping]
