"""RED tests for #141: normalize_title / has_cyrillic pure helpers.

Both support the language-aware prefilter (`HeuristicStrategy`):
`normalize_title` provides stable title substring matching and `has_cyrillic`
provides the primary language signal (#315 RU>EN). They live beside
`title_year_matches` so shared title logic is not reinvented (§II).
"""

from __future__ import annotations

import unittest

from kinozal_scraper.text_utils import has_cyrillic, normalize_title, original_title


class TestNormalizeTitle(unittest.TestCase):
    def test_strips_punctuation_and_lowercases(self) -> None:
        self.assertEqual(normalize_title("Dune: Part Two!"), "dune part two")

    def test_keeps_cyrillic(self) -> None:
        self.assertIn("волк", normalize_title("Волк, 2025"))


class TestHasCyrillic(unittest.TestCase):
    def test_true_for_russian(self) -> None:
        self.assertTrue(has_cyrillic("Волк трейлер"))

    def test_false_for_latin(self) -> None:
        self.assertFalse(has_cyrillic("The Wolf trailer"))


class TestOriginalTitle(unittest.TestCase):
    """#412: the second ` / ` segment is an original title or service token.

    Listing category alone lost real English names for localized games (#385).
    The title grammar now distinguishes the exact service forms observed across
    all 3,764 raw Sheets titles: `x64` (888), `RU` (139), and `EN` (1).
    """

    _GAME_RAW = (
        "Marvel Человек-Паук 2 / Marvel's Spider-Man 2 (Digital Deluxe Edition)"
        " / x64 / RU / Action / 2025 / Portable / PC (Windows)"
    )

    def test_service_segment_is_not_original(self) -> None:
        # `x86`/`x32` were absent from the sample but are part of #385's grammar.
        cases = [
            "S.T.A.L.K.E.R. 2 / x64 / RU / Action / 2024 / Portable / PC (Windows)",
            "Old Game / x86 / RU / Action / 2004 / PC (Windows)",
            "Old Game / x32 / RU / Action / 2004 / PC (Windows)",
            "Fallout 2 / RU / RPG / 2006 / RePack / PC (Windows)",
            "Some Game / EN / Action / 2024 / PC (Windows)",
            "Some Game / ru / Action / 2024 / PC (Windows)",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(original_title(raw), "")

    def test_localised_game_keeps_original(self) -> None:
        # The complete original title occupies the second segment; later matching
        # removes the parenthesized edition suffix.
        self.assertEqual(
            original_title(self._GAME_RAW), "Marvel's Spider-Man 2 (Digital Deluxe Edition)"
        )

    def test_film_segment_unchanged(self) -> None:
        # Characterization: the film-title grammar remains unchanged.
        self.assertEqual(original_title("Гнев / Man on Fire / 2026 / WEB-DLRip"), "Man on Fire")

    def test_short_film_title_is_not_service(self) -> None:
        # Short non-game second segments are real titles, so discriminate by exact
        # literals rather than a "short means service" heuristic.
        self.assertEqual(original_title("Укрытие (1 сезон) / Silo / 2023 / WEB-DLRip"), "Silo")


if __name__ == "__main__":
    unittest.main()
