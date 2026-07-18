"""RED tests for #141: normalize_title / has_cyrillic pure helpers.

Обе — вспомогательные для language-aware пред-фильтра (`HeuristicStrategy`):
`normalize_title` даёт устойчивый substring-матч названия, `has_cyrillic` —
первичный языковой сигнал (#315 RU>EN). Живут в text_utils рядом с
`title_year_matches` (§II — общая title-логика не переизобретается).
"""

from __future__ import annotations

import unittest

from kinozal_scraper.text_utils import has_cyrillic, normalize_title


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


if __name__ == "__main__":
    unittest.main()
