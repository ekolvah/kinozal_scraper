"""TMDB videos как источник трейлера (#329, эпик трейлеров).

Гипотеза эпика: «официальный трейлер с приоритетом русского» — решённая задача;
каноничный способ — метаданный API с language-размеченными видео, а не
YouTube-скрейпинг + эвристика (#141) / LLM (#142) / эмбеддинги (#143). TMDB
`/movie/{id}/videos` отдаёт per-video `key` (YouTube-id), `iso_639_1`, `type`,
`official`, `site` — правило отбора схлопывается в детерминированный фильтр.

Граница retrieval → selection зеркалит `youtube.py` (§II): `TmdbClient.resolve`
(внешняя граница, DI) тянет видео, чистая `pick_trailer` их ранжирует. Прод не
подключается до отдельной интеграции (аналог #144) — это offline-компонент под
замер на eval-harness.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from kinozal_scraper.trailer_strategy import FilmProfile, TrailerPick

_YOUTUBE = "YouTube"
_TMDB_API = "https://api.themoviedb.org/3"


@dataclass
class TmdbVideo:
    """Одно видео из TMDB `/movie/{id}/videos`. `key` — YouTube-id (кладётся в
    `TrailerPick.video_id`); `iso_639_1`/`type`/`official`/`site` — сигналы отбора;
    `name` несёт accessibility-нюанс (ASL-вариант) для внутритировой де-приоритизации."""

    key: str
    iso_639_1: str
    type: str
    official: bool
    site: str
    name: str = ""


# ── selection: чистое детерминированное правило (§II, без сети/LLM) ────────────


def _tier(video: TmdbVideo) -> int:
    """Приоритет-тир (меньше = лучше); `_INELIGIBLE` — не трейлер/тизер вовсе.
    RU Trailer → RU Teaser → official en Trailer → любой en Trailer. Спорный стык
    `RU Teaser (1) < official en Trailer (2)` пришпилен тестом (§I)."""
    is_ru = video.iso_639_1 == "ru"
    if is_ru and video.type == "Trailer":
        return 0
    if is_ru and video.type == "Teaser":
        return 1
    if video.type == "Trailer" and video.official:
        return 2
    if video.type == "Trailer":
        return 3
    return _INELIGIBLE


_INELIGIBLE = 99


def _is_sign_language(video: TmdbVideo) -> bool:
    """ASL/сурдоперевод-вариант (кейс Битлджуса) — одна substring-проверка `name`,
    НЕ растущая accessibility-таксономия (§VII)."""
    return "sign language" in video.name.lower()


def pick_trailer(videos: list[TmdbVideo]) -> TrailerPick | None:
    """Выбрать трейлер из TMDB-видео. Только `site=YouTube`; тир-приоритет
    (`_tier`), внутри тира ASL-вариант де-приоритезируется. Нечего выбрать →
    `None` (§IV miss-семантика, порождает видимый маркер в проде, не тихий дефолт)."""
    eligible = [v for v in videos if v.site == _YOUTUBE and _tier(v) != _INELIGIBLE]
    if not eligible:
        return None
    best = min(eligible, key=lambda v: (_tier(v), _is_sign_language(v)))
    tier = _tier(best)
    confidence, reason = _TIER_META[tier]
    return TrailerPick(best.key, confidence, reason)


# Уверенность/атрибуция по тиру — язык-приоритет виден в скоркарте (§IV).
_TIER_META: dict[int, tuple[float, str]] = {
    0: (0.95, "tmdb ru trailer"),
    1: (0.7, "tmdb ru teaser"),
    2: (0.6, "tmdb official en trailer"),
    3: (0.4, "tmdb en trailer"),
}


# ── retrieval: внешняя граница (DI, зеркало Youtube) — НЕ юнит-тестится (§II) ──


class TmdbClient:
    """Внешняя граница TMDB (DI-паттерн `Youtube`): `resolve(profile)` → видео.
    Токен из `os.environ["TMDB_TOKEN"]` (v4 Bearer). Сетевой I/O — тонкий слой над
    чистой `pick_trailer`, поэтому юнит-тестами не покрывается (§II)."""

    def __init__(self, session: requests.Session | None = None) -> None:
        token = os.environ["TMDB_TOKEN"]
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.get(f"{_TMDB_API}{path}", params=params, timeout=15)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    def _find_movie_id(self, profile: FilmProfile) -> int | None:
        query = profile.original_title or profile.ru_title
        params: dict[str, Any] = {"query": query}
        if profile.year:
            params["primary_release_year"] = profile.year
        results = self._get("/search/movie", params).get("results", [])
        return int(results[0]["id"]) if results else None

    def resolve(self, profile: FilmProfile) -> list[TmdbVideo]:
        """Видео фильма = union ru-RU ∪ en-US (дедуп по `key`) — RU-дорожка обязана
        оказаться в пуле, когда она есть (зеркало union-retrieval `search_candidates`).
        Фильм не найден → пустой список (§IV: pick_trailer → None → видимый Miss)."""
        movie_id = self._find_movie_id(profile)
        if movie_id is None:
            return []
        seen: set[str] = set()
        out: list[TmdbVideo] = []
        for lang in ("ru-RU", "en-US"):
            data = self._get(f"/movie/{movie_id}/videos", {"language": lang})
            for item in data.get("results", []):
                key = item.get("key")
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(
                    TmdbVideo(
                        key=key,
                        iso_639_1=item.get("iso_639_1", ""),
                        type=item.get("type", ""),
                        official=bool(item.get("official", False)),
                        site=item.get("site", ""),
                        name=item.get("name", ""),
                    )
                )
        return out
