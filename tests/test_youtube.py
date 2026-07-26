"""RED tests for #140: retrieval refactor — `search_candidates` (пул кандидатов).

`youtube.py` перестаёт отдавать первый url и начинает возвращать `list[Candidate]`
со snippet-полями. Пул = union запроса по RU + оригинальному названию (retrieval
breadth под #315), дедуп по `video_id`, БЕЗ year/title-фильтра (фильтр — забота
selection, `FirstResultStrategy`, не retrieval). Сбой одной ветки union не роняет
retrieval — best-effort (§IV), но падение ВСЕХ веток = отказ retrieval и поднимает
`TrailerRetrievalError` (#383): пустой пул от 429 неотличим от честного «ничего не
нашлось», и в прогоне 2026-07-25 это дало 74 фальшивых «no trailer found». Клиент
инъектируется, чтобы harness переиспользовал тот же retrieval (§II — убирает дубль
`eval_trailers._search_candidates`).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from googleapiclient.errors import HttpError

from kinozal_scraper.trailer_strategy import Candidate, FilmProfile
from kinozal_scraper.youtube import (
    TrailerRetrievalError,
    YoutubeQuotaExhausted,
    _is_quota_error,
    search_candidates,
)


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

    def test_all_branches_failed_raises(self) -> None:
        # #383: пока падает ОДНА ветка — best-effort (тест выше). Когда падают ВСЕ,
        # пул пуст не потому, что трейлера нет, а потому что retrieval не состоялся;
        # молча вернуть [] значит выдать инфраструктурный отказ за честный промах.
        client = _FakeClient(
            [
                ("Волк", RuntimeError("YouTube 429 rateLimitExceeded")),
                ("The Wolf", RuntimeError("YouTube 429 rateLimitExceeded")),
            ]
        )
        profile = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        with pytest.raises(TrailerRetrievalError):
            search_candidates(client, profile)

    def test_single_branch_failure_raises_when_titles_collapse(self) -> None:
        # ru_title == original_title → ветка всего одна, поэтому её падение и есть
        # «упали все». Иначе тот же 429 читался бы как miss ровно для фильмов без
        # отдельного оригинального названия.
        client = _FakeClient([("Дюна", RuntimeError("YouTube 429 rateLimitExceeded"))])
        profile = FilmProfile(ru_title="Дюна", original_title="Дюна", year=2024)
        with pytest.raises(TrailerRetrievalError):
            search_candidates(client, profile)

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


class _Resp:
    """Носитель статуса для настоящего `HttpError` (у httplib2-ответа нужны только
    `.status`/`.reason`). Реальность, которую пинят тесты ниже, — не транспорт, а
    форма тела: где именно лежит машинный код причины."""

    def __init__(self, status: int, reason: str = "") -> None:
        self.status = status
        self.reason = reason


def _http_error(status: int, body: dict[str, Any]) -> HttpError:
    return HttpError(_Resp(status), json.dumps(body).encode("utf-8"))


# Настоящие тела ответов YouTube Data API. Legacy-форма (`error.errors[]`) — та,
# что пришла в прогоне 30143534431; ErrorInfo (`error.details[]`) — новая, куда
# Google мигрирует, и код причины там в SCREAMING_SNAKE.
_RATE_LIMIT_429 = {
    "error": {
        "code": 429,
        "message": "Rate Limit Exceeded",
        "errors": [
            {
                "message": "Rate Limit Exceeded",
                "domain": "usageLimits",
                "reason": "rateLimitExceeded",
            }
        ],
    }
}
_QUOTA_403 = {
    "error": {
        "code": 403,
        "message": "The request cannot be completed because you have exceeded your quota.",
        "errors": [
            {
                "message": "The request cannot be completed because you have exceeded your quota.",
                "domain": "youtube.quota",
                "reason": "quotaExceeded",
            }
        ],
    }
}
_RATE_LIMIT_ERRORINFO_429 = {
    "error": {
        "code": 429,
        "message": "Quota exceeded for quota metric 'Queries'",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": "RATE_LIMIT_EXCEEDED",
                "domain": "youtube.googleapis.com",
            }
        ],
    }
}
_BACKEND_500 = {
    "error": {
        "code": 500,
        "message": "Internal error encountered.",
        "errors": [{"message": "Internal error", "domain": "global", "reason": "backendError"}],
    }
}
_FORBIDDEN_403 = {
    "error": {
        "code": 403,
        "message": "The caller does not have permission",
        "errors": [{"message": "Forbidden", "domain": "global", "reason": "forbidden"}],
    }
}


class TestQuotaDetection:
    """#384: остановка по первому квотному отказу требует отличать «квота кончилась»
    от «сервер моргнул» — иначе один 500 глушил бы трейлеры на весь прогон."""

    @pytest.mark.parametrize(
        ("body", "status"),
        [(_RATE_LIMIT_429, 429), (_QUOTA_403, 403), (_RATE_LIMIT_ERRORINFO_429, 429)],
    )
    def test_predicate_matches_real_googleapiclient_httperror(
        self, body: dict[str, Any], status: int
    ) -> None:
        # Reality-anchor: собран настоящий HttpError, а не дубль. Пинится
        # `error_details`, а НЕ `.reason`: в googleapiclient `self.reason =
        # data["error"]["message"]` — человеческий текст («Rate Limit Exceeded»),
        # который Google волен переписать, машинный же код живёт в error_details.
        assert _is_quota_error(_http_error(status, body)) is True

    @pytest.mark.parametrize(("body", "status"), [(_BACKEND_500, 500), (_FORBIDDEN_403, 403)])
    def test_predicate_ignores_non_quota_httperror(self, body: dict[str, Any], status: int) -> None:
        # 403 без usageLimits-причины — отказ доступа, не квота: остановка прогона
        # тут была бы ложной. Статуса самого по себе для решения не хватает.
        assert _is_quota_error(_http_error(status, body)) is False

    def test_predicate_survives_body_without_machine_reason(self) -> None:
        # `error_details` бывает строкой (в теле только `message`) — предикат обязан
        # ответить «не квота», а не упасть на строке вместо списка.
        assert _is_quota_error(_http_error(429, {"error": {"message": "boom"}})) is False

    def test_all_branches_quota_failure_raises_quota_exhausted(self) -> None:
        err = _http_error(429, _RATE_LIMIT_429)
        client = _FakeClient([("Волк", err), ("The Wolf", err)])
        profile = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        with pytest.raises(YoutubeQuotaExhausted):
            search_candidates(client, profile)

    def test_generic_failure_raises_plain_retrieval_error(self) -> None:
        # Сетевой сбой ≠ исчерпанная квота: он роняет один фильм (#383), а не прогон.
        client = _FakeClient([("Волк", RuntimeError("boom")), ("The Wolf", RuntimeError("boom"))])
        profile = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        with pytest.raises(TrailerRetrievalError) as excinfo:
            search_candidates(client, profile)
        assert not isinstance(excinfo.value, YoutubeQuotaExhausted)
