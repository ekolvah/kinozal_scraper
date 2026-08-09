"""RED tests for #329: TMDB videos as a trailer source (official + language).

`pick_trailer` is a pure deterministic selection rule (§II, no network/LLM):
only `site=YouTube`; priority RU Trailer → RU Teaser → official en Trailer →
any en Trailer → None. The ASL variant is deprioritized WITHIN a tier (the Beetlejuice
PoC nuance—one `name` substring check, not a growing taxonomy). The selected
`key` is stored in `TrailerPick.video_id`. `TmdbClient` (an external boundary, DI like
`Youtube`) is NOT unit-tested (§II)."""

from __future__ import annotations

import unittest

from kinozal_scraper.tmdb_trailer import TmdbVideo, pick_trailer


def _v(
    key: str,
    iso: str = "en",
    kind: str = "Trailer",
    official: bool = True,
    site: str = "YouTube",
    name: str = "",
) -> TmdbVideo:
    return TmdbVideo(key=key, iso_639_1=iso, type=kind, official=official, site=site, name=name)


class TestPickTrailer(unittest.TestCase):
    def test_prefers_ru_trailer_over_official_en(self) -> None:
        pick = pick_trailer(
            [
                _v("en1", iso="en", kind="Trailer", official=True),
                _v("ru1", iso="ru", kind="Trailer", official=False),
            ]
        )
        assert pick is not None
        self.assertEqual(pick.video_id, "ru1")

    def test_ru_teaser_beats_official_en_trailer(self) -> None:
        # Contested tier-boundary decision (§I pins it): RU Teaser outranks an
        # official en Trailer—the Russian track is more valuable than “more official” English.
        pick = pick_trailer(
            [
                _v("en1", iso="en", kind="Trailer", official=True),
                _v("ruTeaser", iso="ru", kind="Teaser", official=False),
            ]
        )
        assert pick is not None
        self.assertEqual(pick.video_id, "ruTeaser")

    def test_prefers_official_en_over_nonofficial_en(self) -> None:
        pick = pick_trailer(
            [
                _v("nonoff", iso="en", kind="Trailer", official=False),
                _v("off", iso="en", kind="Trailer", official=True),
            ]
        )
        assert pick is not None
        self.assertEqual(pick.video_id, "off")

    def test_skips_non_youtube_site(self) -> None:
        # A Vimeo RU trailer (otherwise top tier) is filtered → YouTube en is selected.
        pick = pick_trailer(
            [
                _v("vimeoRu", iso="ru", kind="Trailer", official=True, site="Vimeo"),
                _v("ytEn", iso="en", kind="Trailer", official=True, site="YouTube"),
            ]
        )
        assert pick is not None
        self.assertEqual(pick.video_id, "ytEn")

    def test_teaser_not_chosen_when_ru_trailer_exists(self) -> None:
        pick = pick_trailer(
            [
                _v("ruTeaser", iso="ru", kind="Teaser"),
                _v("ruTrailer", iso="ru", kind="Trailer"),
            ]
        )
        assert pick is not None
        self.assertEqual(pick.video_id, "ruTrailer")

    def test_deprioritizes_sign_language_variant(self) -> None:
        # Beetlejuice case: an ASL official Trailer loses to a normal en Trailer in the
        # same tier (ASL is first in the list—ordering must not pull it through).
        pick = pick_trailer(
            [
                _v(
                    "asl",
                    iso="en",
                    kind="Trailer",
                    official=True,
                    name="Official Trailer (ASL Sign Language)",
                ),
                _v("plain", iso="en", kind="Trailer", official=True, name="Official Trailer"),
            ]
        )
        assert pick is not None
        self.assertEqual(pick.video_id, "plain")

    def test_empty_videos_returns_none(self) -> None:
        # §IV miss semantics: nothing to select → None (not a silent default).
        self.assertIsNone(pick_trailer([]))
