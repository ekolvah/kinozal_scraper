"""RED tests for #140: retrieval refactor — `search_candidates` (пул кандидатов).

`youtube.py` перестаёт отдавать первый url и начинает возвращать `list[Candidate]`
со snippet-полями. Пул = union запроса по RU + оригинальному названию (retrieval
breadth под #315), дедуп по `video_id`, БЕЗ year/title-фильтра (фильтр — забота
selection, `FirstResultStrategy`, не retrieval). Сбой одной ветки union не роняет
retrieval — best-effort (§IV). Клиент инъектируется, чтобы harness переиспользовал
тот же retrieval (§II — убирает дубль `eval_trailers._search_candidates`).
"""

from __future__ import annotations

from typing import Any

from kinozal_scraper.trailer_strategy import Candidate, FilmProfile
from kinozal_scraper.youtube import search_candidates


def _video_item(video_id: str, title: str, **snippet: str) -> dict[str, Any]:
    return {
        "id": {"kind": "youtube#video", "videoId": video_id},
        "snippet": {"title": title, **snippet},
    }


class _FakeClient:
    """Минимальный дубль googleapiclient youtube-resource: `.search().list(**p).execute()`.

    `by_needle` — список (подстрока-запроса → items | Exception); первая, чья
    подстрока входит в `q`, определяет ответ. Exception → поднимается на execute()
    (симулирует сбой одной ветки union). Матч по подстроке (не по точному запросу)
    держит тест устойчивым к формату query-строки — контракт лишь «название входит
    в запрос»."""

    def __init__(self, by_needle: list[tuple[str, Any]]) -> None:
        self.by_needle = by_needle
        self.queries: list[str] = []

    def search(self) -> _FakeClient:
        return self

    def list(self, **params: Any) -> _FakeExec:
        q = params["q"]
        self.queries.append(q)
        payload: Any = []
        for needle, items in self.by_needle:
            if needle in q:
                payload = items
                break
        return _FakeExec(payload)


class _FakeExec:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def execute(self) -> dict[str, Any]:
        if isinstance(self.payload, Exception):
            raise self.payload
        return {"items": self.payload}


class TestSearchCandidates:
    def test_maps_snippet_to_candidate_fields(self) -> None:
        client = _FakeClient(
            [
                (
                    "Дюна",
                    [
                        _video_item(
                            "v1",
                            "Дюна 2024 трейлер",
                            channelTitle="КиноПоиск",
                            description="официальный русский трейлер",
                            publishedAt="2024-01-10T00:00:00Z",
                        )
                    ],
                )
            ]
        )
        profile = FilmProfile(ru_title="Дюна", original_title="Дюна", year=2024)
        result = search_candidates(client, profile)
        assert result == [
            Candidate(
                video_id="v1",
                title="Дюна 2024 трейлер",
                channel="КиноПоиск",
                description="официальный русский трейлер",
                published_at="2024-01-10T00:00:00Z",
            )
        ]

    def test_pool_unions_ru_and_original_queries(self) -> None:
        # Ядро #315: RU-трейлер обязан оказаться в пуле рядом с англ., когда он есть.
        client = _FakeClient(
            [
                ("Волк", [_video_item("ru_wolf", "Волк 2025 трейлер на русском")]),
                ("The Wolf", [_video_item("eng_wolf", "The Wolf 2025 Official Trailer")]),
            ]
        )
        profile = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        ids = {c.video_id for c in search_candidates(client, profile)}
        assert ids == {"ru_wolf", "eng_wolf"}

    def test_dedups_video_id_across_union(self) -> None:
        # Одно видео найдено обоими запросами → один Candidate, не два.
        dup = _video_item("same", "Волк / The Wolf 2025 trailer")
        client = _FakeClient([("Волк", [dup]), ("The Wolf", [dup])])
        profile = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        result = search_candidates(client, profile)
        assert [c.video_id for c in result] == ["same"]

    def test_single_query_when_ru_equals_original(self) -> None:
        # Нет отдельного оригинала → один запрос, не два (экономия YouTube-квоты).
        client = _FakeClient([("Дюна", [_video_item("v1", "Дюна 2024 трейлер")])])
        profile = FilmProfile(ru_title="Дюна", original_title="Дюна", year=2024)
        search_candidates(client, profile)
        assert len(client.queries) == 1

    def test_one_query_failure_still_returns_other_pool(self) -> None:
        # Сбой одной ветки union (§IV best-effort) не должен ронять retrieval —
        # отдаём кандидатов уцелевшей ветки.
        client = _FakeClient(
            [
                ("Волк", [_video_item("ru_wolf", "Волк 2025 трейлер")]),
                ("The Wolf", RuntimeError("YouTube 500")),
            ]
        )
        profile = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        ids = {c.video_id for c in search_candidates(client, profile)}
        assert ids == {"ru_wolf"}

    def test_no_year_filter_in_retrieval(self) -> None:
        # Retrieval = чистый breadth: кандидат с «чужим» годом в title остаётся в
        # пуле (год-фильтр — забота selection FirstResultStrategy, не retrieval).
        client = _FakeClient([("Дюна", [_video_item("v_old", "Дюна 2015 трейлер")])])
        profile = FilmProfile(ru_title="Дюна", original_title="Дюна", year=2024)
        ids = [c.video_id for c in search_candidates(client, profile)]
        assert ids == ["v_old"]

    def test_skips_non_video_items(self) -> None:
        # search.list может вернуть channel/playlist — берём только youtube#video.
        client = _FakeClient(
            [
                (
                    "Дюна",
                    [
                        {"id": {"kind": "youtube#channel", "channelId": "c1"}, "snippet": {}},
                        _video_item("v1", "Дюна 2024 трейлер"),
                    ],
                )
            ]
        )
        profile = FilmProfile(ru_title="Дюна", original_title="Дюна", year=2024)
        assert [c.video_id for c in search_candidates(client, profile)] == ["v1"]
