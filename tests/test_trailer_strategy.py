"""RED tests for #139: TrailerStrategy Protocol + FirstResultStrategy baseline.

FirstResultStrategy повторяет текущую прод-логику отбора (`_search_youtube`
loop): первый Candidate, чей title проходит общий `title_year_matches`; при
year=None — первый кандидат (как прод, который применяет год-фильтр только при
truthy year). Год-правило шарится с продом (§II), не переписывается.
"""

from __future__ import annotations

import unittest

from kinozal_scraper.trailer_strategy import (
    Candidate,
    FilmProfile,
    FirstResultStrategy,
    TrailerPick,
)


class TestFirstResultStrategy(unittest.TestCase):
    def test_picks_first_year_matching_candidate(self) -> None:
        strat = FirstResultStrategy()
        film = FilmProfile(ru_title="Гнев", original_title="Man on Fire", year=2026)
        candidates = [
            Candidate(video_id="a", title="Man on Fire 2026 Official Trailer"),
            Candidate(video_id="b", title="Man on Fire 2026 Trailer Reaction"),
        ]
        pick = strat.pick(film, candidates)
        self.assertIsInstance(pick, TrailerPick)
        self.assertEqual(pick.video_id, "a")

    def test_skips_wrong_year_candidate(self) -> None:
        # Замена §II-фейкового test_2026_film_skips_2015_kingsman_trailer:
        # kingsman-2015 в пуле пропущен, выбран корректный 2026 — на реальных
        # Candidate, через общий title_year_matches, без мока внутренней логики.
        strat = FirstResultStrategy()
        film = FilmProfile(ru_title="Секретная служба", original_title="Kingsman", year=2026)
        candidates = [
            Candidate(
                video_id="old_2015", title="Kingsman: Секретная служба (2015) Трейлер на русском"
            ),
            Candidate(video_id="correct_id", title="Секретная служба 2026 Официальный трейлер"),
        ]
        pick = strat.pick(film, candidates)
        self.assertEqual(pick.video_id, "correct_id")

    def test_no_match_returns_none_pick(self) -> None:
        # Замена §II-фейкового test_miss_returns_miss_marker на уровне стратегии:
        # пустой пул → TrailerPick(video_id=None). Прод-маркер §IV
        # (_TRAILER_MISS_MARKER) остаётся отдельным тестом enrich_with_trailer.
        strat = FirstResultStrategy()
        film = FilmProfile(ru_title="X", original_title="X", year=2026)
        pick = strat.pick(film, [])
        self.assertIsNone(pick.video_id)

    def test_no_year_takes_first_candidate(self) -> None:
        # year=None → прод не применяет год-фильтр → первый кандидат.
        strat = FirstResultStrategy()
        film = FilmProfile(ru_title="Дюна", original_title="Dune", year=None)
        candidates = [
            Candidate(video_id="first", title="Dune 2021 Trailer"),
            Candidate(video_id="second", title="Dune Part Two Trailer"),
        ]
        pick = strat.pick(film, candidates)
        self.assertEqual(pick.video_id, "first")

    def test_falsy_year_zero_mirrors_prod(self) -> None:
        # Пришпиливает точное зеркало прода (`if film_year:`): falsy year (0) —
        # как None — год-фильтр не применяет → первый кандидат, даже с чужим
        # годом в заголовке. Guard против будущего дрейфа `not year` → `is None`.
        strat = FirstResultStrategy()
        film = FilmProfile(ru_title="Ноль", original_title="Zero", year=0)
        candidates = [Candidate(video_id="first", title="Zero 1999 Trailer")]
        pick = strat.pick(film, candidates)
        self.assertEqual(pick.video_id, "first")


if __name__ == "__main__":
    unittest.main()
