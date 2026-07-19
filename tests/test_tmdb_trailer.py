"""RED tests for #329: TMDB videos как источник трейлера (official + язык).

`pick_trailer` — чистое детерминированное правило отбора (§II, без сети/LLM):
только `site=YouTube`; приоритет RU Trailer → RU Teaser → official en Trailer →
любой en Trailer → None. ASL-вариант де-приоритезируется ВНУТРИ тира (PoC-нюанс
Битлджуса — одна substring-проверка `name`, не растущая таксономия). Выбранный
`key` кладётся в `TrailerPick.video_id`. `TmdbClient` (внешняя граница, DI как
`Youtube`) НЕ юнит-тестится (§II)."""

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
        # Спорный стык тир-лестницы (§I пришпиливает решение): RU Teaser выше
        # official en Trailer — русская дорожка ценнее «более официального» англа.
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
        # Vimeo RU-трейлер (иначе топ-тир) отфильтрован → берётся YouTube en.
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
        # Кейс Битлджуса: ASL-official Trailer уступает обычному en Trailer в том
        # же тире (ASL-вариант первым в списке — порядок не должен его вытянуть).
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
        # §IV miss-семантика: нечего выбрать → None (не тихий дефолт).
        self.assertIsNone(pick_trailer([]))
