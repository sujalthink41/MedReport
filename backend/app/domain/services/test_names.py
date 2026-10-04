"""Turning whatever a lab printed into a stable lookup key.

Nothing medical is hardcoded here. This module knows no test names — it only
knows how to normalise text so that ``S.G.P.T.``, ``SGPT`` and ``S G P T`` all
reach the same dictionary entry.

The dictionary itself starts **empty** and fills from what the model reads out of
real reports. This file is the part that has to be deterministic, because the key
it produces is what every historical trend is joined on. If normalisation changed
between releases, every user's chart would silently split in two.
"""

import re
import unicodedata

# Dots and apostrophes are abbreviation marks, not separators. Removing them first
# is what makes "S.G.P.T." collapse to "sgpt" rather than exploding into four
# single letters.
# The curly apostrophe is deliberate: lab PDFs use typographic quotes, and
# "Gilbert's" must reach the same key whichever form was printed.
_ABBREVIATION_MARKS = re.compile("[.’']")  # noqa: RUF001 - the curly form is intentional, see above
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_PARENTHETICAL = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")

# Words labs add that carry no identity. Stripped so "Serum Creatinine" and
# "Creatinine" share a key. Deliberately a short, boring list of printing
# conventions - not clinical knowledge.
_NOISE_WORDS = frozenset(
    {
        "serum",
        "plasma",
        "blood",
        "whole",
        "total",
        "test",
        "level",
        "levels",
        "estimation",
        "quantitative",
        "method",
        "fasting",
        "random",
    }
)


def normalize(raw: str) -> str:
    """One canonical spelling of a printed test name.

    ``NFKC`` first because lab PDFs are full of typographic lookalikes — full-width
    characters, non-breaking spaces, ligatures. Two names that look identical on
    screen must not produce different keys.
    """
    text = unicodedata.normalize("NFKC", raw).strip().lower()
    text = _ABBREVIATION_MARKS.sub("", text)
    text = _NON_ALPHANUMERIC.sub(" ", text)
    return " ".join(text.split())


def strip_noise(normalized: str) -> str:
    """Drop printing conventions that carry no identity.

    Never applied blindly as the only key — see ``alias_keys``, which keeps the
    full form too. If a word in this list ever turns out to be meaningful for some
    assay, the unstripped key still matches.
    """
    words = [w for w in normalized.split() if w not in _NOISE_WORDS]
    return " ".join(words) if words else normalized


def alias_keys(raw: str) -> list[str]:
    """Candidate lookup keys for one printed name, most specific first.

    Labs write the same test several ways, and the most common is a parenthetical
    synonym::

        "SGPT (ALT)"  ->  ["sgpt alt", "sgpt", "alt"]

    Returning all three means a dictionary entry recorded under *any* of them is
    found. The caller tries them in order and takes the first hit, so the most
    specific form wins and a bare "alt" only matches when nothing better does.

    Order matters more than it looks: searching the bare parenthetical first would
    let "Vitamin D (25-OH)" match an entry stored for "25-OH" alone, which may be a
    different assay.
    """
    normalized = normalize(raw)
    if not normalized:
        return []

    keys: list[str] = [normalized]

    inside = [normalize(m) for m in _PARENTHETICAL.findall(raw)]
    outside = normalize(_PARENTHETICAL.sub(" ", raw))

    if outside and outside != normalized:
        keys.append(outside)
    for candidate in inside:
        if candidate and candidate not in keys:
            keys.append(candidate)

    # Noise-stripped variants, appended rather than substituted so the exact form
    # is always tried first.
    for key in list(keys):
        stripped = strip_noise(key)
        if stripped and stripped not in keys:
            keys.append(stripped)

    return keys
