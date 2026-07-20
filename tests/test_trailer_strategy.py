"""RED tests for #139: TrailerStrategy Protocol + FirstResultStrategy baseline.

FirstResultStrategy повторяет ПРЕЖНЮЮ прод-логику отбора (одиночный
`get_trailer_url`, удалён в #144): первый Candidate, чей title проходит общий
`title_year_matches`; при year=None — первый кандидат (год-фильтр только при
truthy year). Теперь baseline под harness/сравнение (прод отбирает
`HeuristicStrategy` #141). Год-правило шарится (§II), не переписывается.
"""

from __future__ import annotations

import unittest

from kinozal_scraper.trailer_strategy import (
    Candidate,
    FilmProfile,
    FirstResultStrategy,
    HeuristicStrategy,
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


class TestHeuristicStrategy(unittest.TestCase):
    """RED tests for #141: language-aware детерминированный пред-фильтр.

    Ранжирование: язык первичен (#315 RU>EN), каст в `description` — вторичный
    тай-брейк внутри одного языка. Неоднозначность (равный топ-ранг) → первый по
    порядку + низкий confidence + `ambiguous`-маркер (сигнал для #144, не тихий).
    """

    def _pick(self, film: FilmProfile, cands: list[Candidate]) -> TrailerPick:
        return HeuristicStrategy().pick(film, cands)

    def test_prefers_ru_when_tied_with_eng(self) -> None:
        film = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        cands = [
            Candidate(video_id="eng", title="The Wolf 2025 Official Trailer"),
            Candidate(video_id="ru", title="Волк 2025 трейлер на русском"),
        ]
        self.assertEqual(self._pick(film, cands).video_id, "ru")

    def test_prefers_ru_among_multiple_eng(self) -> None:
        film = FilmProfile(ru_title="Хищник", original_title="Predator", year=2025)
        cands = [
            Candidate(video_id="eng1", title="Predator 2025 Official Trailer"),
            Candidate(video_id="eng2", title="Predator 2025 Final Trailer"),
            Candidate(video_id="ru", title="Хищник 2025 официальный трейлер"),
        ]
        self.assertEqual(self._pick(film, cands).video_id, "ru")

    def test_ru_beats_eng_even_when_eng_has_cast(self) -> None:
        # #315: язык первичен. EN-реакция с именем каста в description НЕ
        # побеждает RU без каста — reaction-video false-positive не проходит.
        film = FilmProfile(
            ru_title="Волк", original_title="The Wolf", year=2025, cast=["John Smith"]
        )
        cands = [
            Candidate(
                video_id="eng",
                title="The Wolf 2025 Reaction",
                description="John Smith reacts to the trailer",
            ),
            Candidate(video_id="ru", title="Волк 2025 трейлер", description=""),
        ]
        self.assertEqual(self._pick(film, cands).video_id, "ru")

    def test_cast_breaks_tie_within_same_language(self) -> None:
        # Два RU-кандидата равного языка+года: каст в description различает.
        film = FilmProfile(
            ru_title="Ярость", original_title="Ярость", year=2026, cast=["Иван Петров"]
        )
        cands = [
            Candidate(video_id="react", title="Ярость 2026 трейлер разбор", description="обзор"),
            Candidate(
                video_id="cast",
                title="Ярость 2026 официальный трейлер",
                description="В главной роли Иван Петров",
            ),
        ]
        self.assertEqual(self._pick(film, cands).video_id, "cast")

    def test_excludes_wrong_year(self) -> None:
        film = FilmProfile(ru_title="Забвение", original_title="Oblivion", year=2026)
        cands = [Candidate(video_id="old", title="Oblivion 2019 Trailer")]
        self.assertIsNone(self._pick(film, cands).video_id)

    def test_no_title_match_returns_none(self) -> None:
        film = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        cands = [Candidate(video_id="x", title="Completely Different Movie 2025")]
        self.assertIsNone(self._pick(film, cands).video_id)

    def test_single_match_is_confident(self) -> None:
        film = FilmProfile(ru_title="Оппенгеймер", original_title="Oppenheimer", year=2023)
        cands = [Candidate(video_id="o", title="Oppenheimer 2023 Official Trailer")]
        pick = self._pick(film, cands)
        self.assertEqual(pick.video_id, "o")
        self.assertGreaterEqual(pick.confidence, 0.7)

    def test_ambiguous_two_eng_picks_first_low_confidence(self) -> None:
        # #327: teaser-vs-main больше НЕ ambiguous (trailer-signal их различает),
        # поэтому ничью держат две равноценные версии трейлера — ambiguity-
        # семантика (первый по порядку + низкий confidence + маркер) сохраняется.
        film = FilmProfile(ru_title="Барби", original_title="Barbie", year=2023)
        cands = [
            Candidate(video_id="main", title="Barbie 2023 Official Trailer"),
            Candidate(video_id="second", title="Barbie 2023 Final Trailer"),
        ]
        pick = self._pick(film, cands)
        self.assertEqual(pick.video_id, "main")
        self.assertLessEqual(pick.confidence, 0.5)
        self.assertIn("ambiguous", pick.reason)

    def test_numbered_sequel_relevance_match(self) -> None:
        # Реальный дефект #141: канал добавляет номер сиквела («Джокер 2»),
        # которого нет в ru_title → нормализованная фраза разорвана цифрой, и
        # word-boundary phrase-match отвергает настоящий RU-трейлер. Он обязан
        # пройти relevance (RED до numeric-sequel-token фикса).
        film = FilmProfile(
            ru_title="Джокер: Безумие на двоих",
            original_title="Joker: Folie à Deux",
            year=2024,
        )
        cands = [
            Candidate(
                video_id="ru_trailer",
                title="Джокер 2: Безумие на двоих — Русский трейлер (2024)",
            )
        ]
        self.assertEqual(self._pick(film, cands).video_id, "ru_trailer")

    def test_base_title_does_not_crossmatch_sequel(self) -> None:
        # Обратное направление B3: numeric-token фикс НЕ должен дать базовому
        # фильму «Дюна» (2021) матчить сиквел-кандидат другого года — год-фильтр
        # остаётся дискриминатором (инвариант зелёный до и после фикса; краснеет,
        # если фикс случайно ослабит год-фильтр).
        film = FilmProfile(ru_title="Дюна", original_title="Dune", year=2021)
        cands = [Candidate(video_id="sequel", title="Дюна: Часть вторая — Русский трейлер (2024)")]
        self.assertIsNone(self._pick(film, cands).video_id)

    def test_tiebreak_prefers_trailer_over_news_clip(self) -> None:
        # Реальный дефект #141: при RU-ничьей (равный язык, нет каста) выбор шёл
        # по порядку в пуле → новостной клип/тизер вместо настоящего трейлера.
        # trailer-signal (трейлер/дубляж − тизер) разрывает ничью (RED до фикса).
        film = FilmProfile(
            ru_title="Джокер: Безумие на двоих",
            original_title="Joker: Folie à Deux",
            year=2024,
        )
        cands = [
            Candidate(
                video_id="news",
                title="🃏 Вышел тизер-трейлер фильма «Джокер: Безумие на двоих»",
            ),
            Candidate(
                video_id="trailer",
                title="Джокер: Безумие на двоих — Русский трейлер (Дубляж, 2024)",
            ),
        ]
        self.assertEqual(self._pick(film, cands).video_id, "trailer")

    def test_short_title_not_matched_inside_word(self) -> None:
        # review #324: word-boundary матч — короткое «Дом» НЕ входит в «Домашний»,
        # иначе был бы уверенный wrong-pick несвязанного кандидата.
        film = FilmProfile(ru_title="Дом", original_title="Home", year=2026)
        cands = [Candidate(video_id="x", title="Домашний питомец 2026 трейлер")]
        self.assertIsNone(self._pick(film, cands).video_id)

    def test_no_year_matches_by_title(self) -> None:
        film = FilmProfile(ru_title="Дюна", original_title="Dune", year=None)
        cands = [Candidate(video_id="d", title="Dune Official Trailer")]
        self.assertEqual(self._pick(film, cands).video_id, "d")


if __name__ == "__main__":
    unittest.main()
