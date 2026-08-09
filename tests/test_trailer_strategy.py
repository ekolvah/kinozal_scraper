"""RED tests for #139: TrailerStrategy Protocol + FirstResultStrategy baseline.

FirstResultStrategy repeats the PREVIOUS production selection logic (the single
`get_trailer_url`, removed in #144): the first Candidate whose title passes shared
`title_year_matches`; with year=None, the first candidate (year filtering only for a
truthy year). It is now a baseline for harness/comparison (production selects
`HeuristicStrategy` #141). The year rule is shared (§II), not rewritten.
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
        # Replacement for the §II fake test_2026_film_skips_2015_kingsman_trailer:
        # Kingsman-2015 in the pool is skipped and correct 2026 selected—on real
        # Candidates, through shared title_year_matches, without mocking internal logic.
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
        # Replacement for the §II fake test_miss_returns_miss_marker at strategy level:
        # empty pool → TrailerPick(video_id=None). The production §IV marker
        # (_TRAILER_MISS_MARKER) remains a separate enrich_with_trailer test.
        strat = FirstResultStrategy()
        film = FilmProfile(ru_title="X", original_title="X", year=2026)
        pick = strat.pick(film, [])
        self.assertIsNone(pick.video_id)

    def test_no_year_takes_first_candidate(self) -> None:
        # year=None → production does not apply the year filter → first candidate.
        strat = FirstResultStrategy()
        film = FilmProfile(ru_title="Дюна", original_title="Dune", year=None)
        candidates = [
            Candidate(video_id="first", title="Dune 2021 Trailer"),
            Candidate(video_id="second", title="Dune Part Two Trailer"),
        ]
        pick = strat.pick(film, candidates)
        self.assertEqual(pick.video_id, "first")

    def test_falsy_year_zero_mirrors_prod(self) -> None:
        # Pins the exact production mirror (`if film_year:`): a falsy year (0), like
        # None, does not apply year filtering → first candidate even with a foreign
        # year in the title. Guard against future `not year` → `is None` drift.
        strat = FirstResultStrategy()
        film = FilmProfile(ru_title="Ноль", original_title="Zero", year=0)
        candidates = [Candidate(video_id="first", title="Zero 1999 Trailer")]
        pick = strat.pick(film, candidates)
        self.assertEqual(pick.video_id, "first")


class TestHeuristicStrategy(unittest.TestCase):
    """RED tests for #141: language-aware deterministic pre-filter.

    Ranking: language is primary (#315 RU>EN), cast in `description` is a secondary
    tie-breaker within one language. Ambiguity (equal top rank) → first by order + low
    confidence + `ambiguous` marker (a visible signal for #144).
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
        # #315: language is primary. An EN reaction with a cast name in description does NOT
        # beat RU without cast—the reaction-video false positive does not pass.
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
        # Two RU candidates with equal language+year: cast in description distinguishes them.
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
        # #327: teaser-vs-main is no longer ambiguous (trailer signal distinguishes them),
        # so the tie is two equivalent trailer versions—ambiguity semantics (first by order +
        # low confidence + marker) remains.
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
        # Actual #141 defect: a channel adds a sequel number (“Joker 2”) absent from ru_title →
        # a digit splits the normalized phrase and word-boundary phrase matching rejects the real RU trailer.
        # It must pass relevance (RED before the numeric-sequel-token fix).
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
        # Reverse B3 direction: the numeric-token fix must NOT let the base film
        # “Dune” (2021) match a different-year sequel candidate—the year filter remains
        # discriminating (green before and after the fix; red if the fix weakens it).
        film = FilmProfile(ru_title="Дюна", original_title="Dune", year=2021)
        cands = [Candidate(video_id="sequel", title="Дюна: Часть вторая — Русский трейлер (2024)")]
        self.assertIsNone(self._pick(film, cands).video_id)

    def test_tiebreak_prefers_trailer_over_news_clip(self) -> None:
        # Actual #141 defect: on an RU tie (equal language, no cast), pool ordering selected
        # a news clip/teaser instead of the real trailer. Trailer signal (trailer/dub − teaser)
        # breaks the tie (RED before the fix).
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
        # Review #324: word-boundary match—the short title “Dom” is NOT contained in “Domashniy”,
        # otherwise an unrelated candidate would be a confident wrong pick.
        film = FilmProfile(ru_title="Дом", original_title="Home", year=2026)
        cands = [Candidate(video_id="x", title="Домашний питомец 2026 трейлер")]
        self.assertIsNone(self._pick(film, cands).video_id)

    def test_no_year_matches_by_title(self) -> None:
        film = FilmProfile(ru_title="Дюна", original_title="Dune", year=None)
        cands = [Candidate(video_id="d", title="Dune Official Trailer")]
        self.assertEqual(self._pick(film, cands).video_id, "d")

    def test_edition_suffix_does_not_block_match(self) -> None:
        # #412: a game release carries an edition in its original title—`Marvel's Spider-Man 2
        # (Digital Deluxe Edition)`. Tokens `digital deluxe edition` never occur in trailer titles,
        # so the full title matches none of five official trailers and the user received a §IV marker
        # despite a non-empty pool (run 30421569845, pool=5).
        film = FilmProfile(
            ru_title="Marvel Человек-Паук 2",
            original_title="Marvel's Spider-Man 2 (Digital Deluxe Edition)",
            year=2025,
        )
        cands = [
            Candidate(video_id="ps5", title="Marvel's Spider-Man 2 - Launch Trailer | PC Games")
        ]
        self.assertEqual(self._pick(film, cands).video_id, "ps5")

    def test_bracket_in_ru_title_not_collapsed_to_franchise(self) -> None:
        # Guard on fix shape (architect review B3): bracket-tail trimming applies to `original_title`,
        # but NOT the Russian title. Otherwise the “Dune (Part Two)” title collapses to the `Dune` franchise,
        # and one-numeric-skip in `_title_tokens_in` admits it into “Dune 2”—the year does not save it
        # (trailer titles usually lack one), yielding a foreign link with confidence 0.9 instead of an honest §IV marker.
        film = FilmProfile(
            ru_title="Дюна (Часть вторая)", original_title="Dune: Part Two", year=None
        )
        cands = [
            Candidate(video_id="right", title="Dune: Part Two | Official Trailer"),
            Candidate(video_id="wrong", title="Дюна 2 — русский трейлер"),
        ]
        self.assertEqual(self._pick(film, cands).video_id, "right")

    def test_edition_in_candidate_title_does_not_win_over_trailer(self) -> None:
        # #412 measurement on a live pool: the game’s full title including edition appears consecutively
        # in a costume-compilation title (“All Marvel's Spider-Man 2 Digital Deluxe Edition Suits”), so
        # “base variant only when relevant is empty” (the original plan) would return that compilation
        # before reaching real trailers. The base variant is equal; `_trailer_signal` separates trailer
        # from non-trailer.
        film = FilmProfile(
            ru_title="Marvel Человек-Паук 2",
            original_title="Marvel's Spider-Man 2 (Digital Deluxe Edition)",
            year=2025,
        )
        cands = [
            Candidate(
                video_id="suits", title="All Marvel's Spider-Man 2 Digital Deluxe Edition Suits"
            ),
            Candidate(video_id="real", title="Marvel's Spider-Man 2 - Launch Trailer | PC Games"),
        ]
        self.assertEqual(self._pick(film, cands).video_id, "real")


if __name__ == "__main__":
    unittest.main()
