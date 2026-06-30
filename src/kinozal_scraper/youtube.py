"""Поиск YouTube-трейлера (Youtube)."""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

from googleapiclient.discovery import build

from kinozal_scraper.text_utils import title_year_matches

logger = logging.getLogger(__name__)


class Youtube:
    def __init__(self) -> None:
        self.youtube = build("youtube", "v3", developerKey=os.environ["API_KEY"])

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
