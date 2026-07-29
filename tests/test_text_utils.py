"""RED tests for #141: normalize_title / has_cyrillic pure helpers.

Обе — вспомогательные для language-aware пред-фильтра (`HeuristicStrategy`):
`normalize_title` даёт устойчивый substring-матч названия, `has_cyrillic` —
первичный языковой сигнал (#315 RU>EN). Живут в text_utils рядом с
`title_year_matches` (§II — общая title-логика не переизобретается).
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
    """#412: второй ` / `-сегмент — оригинальное название ИЛИ служебный токен.

    #385 отличал их по категории листинга (`t=7` → оригинала нет), из-за чего у
    локализованных игр терялось настоящее английское название и в YouTube уходил
    только русский запрос, которого там не существует. Дискриминатор переезжает
    в саму грамматику заголовка: служебные формы перечислены по замеру всех 3764
    raw-заголовков из Sheets — `x64` (888), `RU` (139), `EN` (1); ничего иного
    служебного во второй позиции не встречается.
    """

    _GAME_RAW = (
        "Marvel Человек-Паук 2 / Marvel's Spider-Man 2 (Digital Deluxe Edition)"
        " / x64 / RU / Action / 2025 / Portable / PC (Windows)"
    )

    def test_service_segment_is_not_original(self) -> None:
        # `x86`/`x32` в выгрузке не встретились, но названы грамматикой #385 —
        # держим в наборе форм, чтобы первая же такая раздача не поехала мусором.
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
        # Характеризация: у локализованной игры оригинал есть и он во 2-м сегменте
        # — целиком, со скобочным суффиксом издания (его снимает уже матчинг).
        self.assertEqual(
            original_title(self._GAME_RAW), "Marvel's Spider-Man 2 (Digital Deluxe Edition)"
        )

    def test_film_segment_unchanged(self) -> None:
        # Характеризация: фильмовая грамматика правкой не задета.
        self.assertEqual(original_title("Гнев / Man on Fire / 2026 / WEB-DLRip"), "Man on Fire")

    def test_short_film_title_is_not_service(self) -> None:
        # Замер: короткие 2-е сегменты у не-игровых — настоящие названия
        # (`Silo`, `From`, `Halo`, `Apex`), поэтому дискриминатор — точный
        # литерал, а не эвристика «короткий → служебный».
        self.assertEqual(original_title("Укрытие (1 сезон) / Silo / 2023 / WEB-DLRip"), "Silo")


if __name__ == "__main__":
    unittest.main()
