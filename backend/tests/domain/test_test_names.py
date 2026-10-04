"""Name normalisation — the function every trend is joined on.

Deterministic by necessity. If normalisation changed between releases, every
user's chart would silently split into two lines, and nothing would look broken.
"""

import pytest

from app.domain.services.test_names import alias_keys, normalize, strip_noise


class TestNormalize:
    @pytest.mark.parametrize(
        ("printed", "expected"),
        [
            ("SGPT", "sgpt"),
            ("S.G.P.T.", "sgpt"),  # dots are abbreviation marks, not separators
            ("S G P T", "s g p t"),
            ("HbA1c", "hba1c"),
            ("Alanine Transaminase", "alanine transaminase"),
            ("  Haemoglobin  ", "haemoglobin"),
            ("25-OH Vitamin D", "25 oh vitamin d"),
            ("T3, Total", "t3 total"),
            ("WBC/Leucocyte Count", "wbc leucocyte count"),
        ],
    )
    def test_printed_names_reduce_to_one_spelling(self, printed: str, expected: str) -> None:
        assert normalize(printed) == expected

    def test_typographic_lookalikes_collapse(self) -> None:
        # Lab PDFs are full of full-width characters and non-breaking spaces. Two
        # names that look identical on screen must not produce different keys.
        assert normalize("ＨｂA1ｃ") == normalize("HbA1c")
        assert normalize("Vitamin D") == normalize("Vitamin D")

    def test_curly_apostrophes_match_straight_ones(self) -> None:
        assert normalize("Gilbert's") == normalize("Gilbert’s")

    def test_empty_input_is_empty(self) -> None:
        assert normalize("   ") == ""


class TestStripNoise:
    @pytest.mark.parametrize(
        ("printed", "expected"),
        [
            ("serum creatinine", "creatinine"),
            ("plasma glucose fasting", "glucose"),
            ("total cholesterol", "cholesterol"),
        ],
    )
    def test_printing_conventions_are_dropped(self, printed: str, expected: str) -> None:
        assert strip_noise(printed) == expected

    def test_a_name_made_only_of_noise_survives(self) -> None:
        # Never return an empty key. "Total" alone is a bad name but it is what the
        # lab printed, and an empty key would collide with every other empty key.
        assert strip_noise("total") == "total"


class TestAliasKeys:
    def test_a_parenthetical_synonym_yields_all_three_forms(self) -> None:
        keys = alias_keys("SGPT (ALT)")

        # Labs write synonyms in brackets constantly. An entry recorded under any
        # of these must be findable.
        assert keys[0] == "sgpt alt"
        assert "sgpt" in keys
        assert "alt" in keys

    def test_the_most_specific_form_is_tried_first(self) -> None:
        keys = alias_keys("Vitamin D (25-OH)")

        # Order matters: searching the bare parenthetical first would let this
        # match an entry stored for "25-OH" alone, which may be a different assay.
        assert keys[0] == "vitamin d 25 oh"
        assert keys.index("vitamin d") < keys.index("25 oh")

    def test_square_brackets_work_too(self) -> None:
        assert "alt" in alias_keys("SGPT [ALT]")

    def test_a_plain_name_yields_itself(self) -> None:
        assert alias_keys("Haemoglobin")[0] == "haemoglobin"

    def test_noise_stripped_variants_come_after_the_exact_form(self) -> None:
        keys = alias_keys("Serum Creatinine")

        # Exact first, so a dictionary entry recorded for the full printed form
        # always wins over a looser match.
        assert keys[0] == "serum creatinine"
        assert "creatinine" in keys

    def test_keys_are_unique(self) -> None:
        keys = alias_keys("Creatinine")

        assert len(keys) == len(set(keys))

    def test_nothing_in_nothing_out(self) -> None:
        assert alias_keys("  ") == []

    def test_different_printings_of_one_marker_share_a_key(self) -> None:
        # The property the whole dictionary rests on.
        forms = ["SGPT", "S.G.P.T.", "sgpt", " SGPT "]

        assert len({alias_keys(f)[0] for f in forms}) == 1
