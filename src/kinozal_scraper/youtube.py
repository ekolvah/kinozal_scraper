"""Поиск YouTube-трейлера (Youtube) + retrieval пула кандидатов (#140)."""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any

from googleapiclient.discovery import build

from kinozal_scraper.text_utils import title_year_matches
from kinozal_scraper.trailer_strategy import Candidate, FilmProfile

logger = logging.getLogger(__name__)


def _search_one(client: Any, query: str) -> list[Candidate]:
    """Один YouTube-запрос → кандидаты (только `youtube#video`), snippet-поля
    отображены в `Candidate`. БЕЗ year/title-фильтра — это чистый retrieval, год
    отсеивает selection (`FirstResultStrategy`), не retrieval."""
    response = (
        client.search()
        .list(q=query, part="id,snippet", maxResults=5, type="video", videoDuration="short")
        .execute()
    )
    out: list[Candidate] = []
    for item in response.get("items", []):
        if item.get("id", {}).get("kind") != "youtube#video":
            continue
        snippet = item.get("snippet", {})
        out.append(
            Candidate(
                video_id=item["id"]["videoId"],
                title=snippet.get("title", ""),
                channel=snippet.get("channelTitle", ""),
                description=snippet.get("description", ""),
                published_at=snippet.get("publishedAt", ""),
            )
        )
    return out


def search_candidates(client: Any, profile: FilmProfile) -> list[Candidate]:
    """Пул кандидатов трейлера = **union** запроса по RU + оригинальному названию,
    дедуп по `video_id` (#140). RU-трейлер обязан оказаться в пуле, когда он есть
    (#315 — retrieval breadth, не selection-bias); язык отбирает selection (#141),
    не retrieval.

    Один запрос при `ru_title == original_title` (нет отдельного оригинала —
    экономит YouTube-квоту). Сбой ОДНОЙ ветки union (§IV best-effort) логируется
    WARNING и не роняет retrieval — отдаём кандидатов уцелевшей ветки. `client` —
    инъектируемый googleapiclient youtube-resource, чтобы harness (`--record`)
    переиспользовал тот же retrieval (§II)."""
    year = profile.year
    titles = [profile.ru_title]
    if profile.original_title and profile.original_title != profile.ru_title:
        titles.append(profile.original_title)
    seen: set[str] = set()
    pool: list[Candidate] = []
    for title in titles:
        query = f"{title} {year} trailer" if year else f"{title} trailer"
        try:
            candidates = _search_one(client, query)
        except Exception as exc:  # noqa: BLE001 — best-effort breadth: one union branch failing must not sink the whole pool (§IV)
            logger.warning("trailer retrieval branch failed for %r: %s", query, exc)
            continue
        for candidate in candidates:
            if candidate.video_id in seen:
                continue
            seen.add(candidate.video_id)
            pool.append(candidate)
    return pool


class Youtube:
    def __init__(self) -> None:
        self.youtube = build("youtube", "v3", developerKey=os.environ["API_KEY"])

    def search_candidates(self, profile: FilmProfile) -> list[Candidate]:
        """Пул кандидатов для `profile` через общий `search_candidates` (#140)."""
        return search_candidates(self.youtube, profile)

    def get_trailer_url(self, film: str, year: int | None = None) -> str:
        query = f"{film} {year} trailer" if year else f"{film} trailer"
        if year:
            published_after = f"{year}-01-01T00:00:00Z"
        else:
            cutoff = date.today() - timedelta(days=180)
            published_after = f"{cutoff.isoformat()}T00:00:00Z"
        result = self._search_youtube(query, published_after, film_year=year)
        if result:
            return result
        return self._search_youtube(query, published_after=None, film_year=year) or ""

    def _search_youtube(
        self,
        query: str,
        published_after: str | None = None,
        film_year: int | None = None,
    ) -> str | None:
        params: dict = dict(
            q=query, part="id,snippet", maxResults=5, type="video", videoDuration="short"
        )
        if published_after:
            params["publishedAfter"] = published_after
        response = self.youtube.search().list(**params).execute()
        for item in response.get("items", []):
            if item["id"].get("kind") != "youtube#video":
                continue
            if film_year:
                title = item.get("snippet", {}).get("title", "")
                if not title_year_matches(title, film_year):
                    continue
            return f"https://www.youtube.com/watch?v={item['id']['videoId']}"
        return None
