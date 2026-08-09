"""RED tests for #140: retrieval refactor — `search_candidates` (candidate pool).

`youtube.py` stops returning the first URL and returns `list[Candidate]` with snippet fields.
The pool is the union of RU and original-title queries (retrieval breadth under #315), deduplicated
by `video_id`, WITHOUT year/title filtering (filtering belongs to selection, `FirstResultStrategy`,
not retrieval). Failure of one union branch does not fail retrieval—best effort (§IV), but failure of
ALL branches is retrieval failure and raises `TrailerRetrievalError` (#383): a pool empty from 429 is
indistinguishable from an honest “nothing found,” producing 74 false “no trailer found” outcomes in the
2026-07-25 run. The client is injected so the harness reuses the same retrieval (§II—removes the
`eval_trailers._search_candidates` duplicate).
"""

from __future__ import annotations

import json
import logging
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
    """Minimal googleapiclient youtube-resource double: `.search().list(**p).execute()`.

    `by_needle` is a list (query substring → items | Exception); the first whose substring
    occurs in `q` determines the response. Exception → raised from execute() (simulates one
    union-branch failure). Substring matching rather than exact-query matching keeps the test
    resilient to query-string format—the contract is only “title is included in the query.”"""

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

    def test_html_entities_decoded(self) -> None:
        # #412: YouTube returns HTML-escaped snippets, while title matching operates on tokens—
        # `&#39;` becomes token `39`, `&amp;` becomes `amp`, and the phrase breaks (`Deadpool & Wolverine`
        # does not find `Deadpool &amp; Wolverine`). Decode at the protocol boundary, not normalize_title.
        client = _FakeClient(
            [
                (
                    "Marvel",
                    [
                        _video_item(
                            "v1",
                            "Marvel&#39;s Spider-Man 2 &quot;Launch&quot; Trailer",
                            description="Deadpool &amp; Wolverine",
                            channelTitle="PlayStation &amp; Insomniac",
                        )
                    ],
                )
            ]
        )
        profile = FilmProfile(ru_title="Marvel Человек-Паук 2", original_title="", year=2025)
        (candidate,) = search_candidates(client, profile)
        assert candidate.title == 'Marvel\'s Spider-Man 2 "Launch" Trailer'
        assert candidate.description == "Deadpool & Wolverine"
        assert candidate.channel == "PlayStation & Insomniac"

    def test_logs_actual_queries(self, caplog: Any) -> None:
        # §IV (#412 review): the query is the only place showing what the pipeline considered a title.
        # These lines found #385; after per-run listing classification was removed, a new service literal in
        # segment two would otherwise silently reach YouTube and be indistinguishable from an honest miss.
        client = _FakeClient([("Волк", [_video_item("v1", "Волк 2025 трейлер")])])
        profile = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        with caplog.at_level(logging.INFO, logger="kinozal_scraper.youtube"):
            search_candidates(client, profile)
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "Волк 2025 trailer" in logged
        assert "The Wolf 2025 trailer" in logged

    def test_pool_unions_ru_and_original_queries(self) -> None:
        # #315 core: an RU trailer must enter the pool beside English when it exists.
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
        # One video found by both queries → one Candidate, not two.
        dup = _video_item("same", "Волк / The Wolf 2025 trailer")
        client = _FakeClient([("Волк", [dup]), ("The Wolf", [dup])])
        profile = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        result = search_candidates(client, profile)
        assert [c.video_id for c in result] == ["same"]

    def test_single_query_when_ru_equals_original(self) -> None:
        # No separate original → one query, not two (saves YouTube quota).
        client = _FakeClient([("Дюна", [_video_item("v1", "Дюна 2024 трейлер")])])
        profile = FilmProfile(ru_title="Дюна", original_title="Дюна", year=2024)
        search_candidates(client, profile)
        assert len(client.queries) == 1

    def test_one_query_failure_still_returns_other_pool(self) -> None:
        # One union-branch failure (§IV best effort) must not fail retrieval—return candidates from the surviving branch.
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
        # #383: while ONE branch fails, use best effort (test above). When ALL fail, the pool is empty not
        # because there is no trailer, but because retrieval did not happen; silently returning [] misreports
        # infrastructure failure as an honest miss.
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
        # ru_title == original_title → there is only one branch, so its failure means
        # “all failed.” Otherwise the same 429 reads as a miss specifically for films without a separate original title.
        client = _FakeClient([("Дюна", RuntimeError("YouTube 429 rateLimitExceeded"))])
        profile = FilmProfile(ru_title="Дюна", original_title="Дюна", year=2024)
        with pytest.raises(TrailerRetrievalError):
            search_candidates(client, profile)

    def test_no_year_filter_in_retrieval(self) -> None:
        # Retrieval = pure breadth: a candidate with a “foreign” year in title remains in
        # the pool (year filtering belongs to FirstResultStrategy selection, not retrieval).
        client = _FakeClient([("Дюна", [_video_item("v_old", "Дюна 2015 трейлер")])])
        profile = FilmProfile(ru_title="Дюна", original_title="Дюна", year=2024)
        ids = [c.video_id for c in search_candidates(client, profile)]
        assert ids == ["v_old"]

    def test_skips_non_video_items(self) -> None:
        # search.list can return channel/playlist—take only youtube#video.
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
    """Status carrier for a real `HttpError` (an httplib2 response needs only `.status`/`.reason`).
    The reality pinned by the tests below is not transport but body shape: where the machine reason code lives."""

    def __init__(self, status: int, reason: str = "") -> None:
        self.status = status
        self.reason = reason


def _http_error(status: int, body: dict[str, Any]) -> HttpError:
    return HttpError(_Resp(status), json.dumps(body).encode("utf-8"))


# Real YouTube Data API response bodies. Legacy form (`error.errors[]`) came in run 30143534431;
# ErrorInfo (`error.details[]`) is the new form Google is migrating to, with reason code in SCREAMING_SNAKE.
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
    """#384: stopping on the first quota failure requires distinguishing “quota exhausted”
    from “server flickered”—otherwise one 500 suppresses trailers for the whole run."""

    @pytest.mark.parametrize(
        ("body", "status"),
        [(_RATE_LIMIT_429, 429), (_QUOTA_403, 403), (_RATE_LIMIT_ERRORINFO_429, 429)],
    )
    def test_predicate_matches_real_googleapiclient_httperror(
        self, body: dict[str, Any], status: int
    ) -> None:
        # Reality anchor: construct a real HttpError, not a double. Pin `error_details`, NOT `.reason`:
        # googleapiclient sets `self.reason = data["error"]["message"]`, human text Google can rewrite,
        # while the machine code lives in error_details.
        assert _is_quota_error(_http_error(status, body)) is True

    @pytest.mark.parametrize(("body", "status"), [(_BACKEND_500, 500), (_FORBIDDEN_403, 403)])
    def test_predicate_ignores_non_quota_httperror(self, body: dict[str, Any], status: int) -> None:
        # 403 without a usageLimits reason is access denial, not quota: stopping the run would be false.
        # Status alone is insufficient for the decision.
        assert _is_quota_error(_http_error(status, body)) is False

    def test_predicate_survives_body_without_machine_reason(self) -> None:
        # `error_details` can be a string (body has only `message`)—the predicate must answer
        # “not quota,” not fail by treating a string as a list.
        assert _is_quota_error(_http_error(429, {"error": {"message": "boom"}})) is False

    def test_all_branches_quota_failure_raises_quota_exhausted(self) -> None:
        err = _http_error(429, _RATE_LIMIT_429)
        client = _FakeClient([("Волк", err), ("The Wolf", err)])
        profile = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        with pytest.raises(YoutubeQuotaExhausted):
            search_candidates(client, profile)

    def test_mixed_branch_failures_prefer_the_quota_signal(self) -> None:
        # Quota is already exhausted; a second branch failing for another reason does not undo that.
        # Selecting the “last failure” loses the signal and makes the next film rediscover it.
        client = _FakeClient(
            [
                ("Волк", _http_error(429, _RATE_LIMIT_429)),
                ("The Wolf", RuntimeError("connection reset")),
            ]
        )
        profile = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        with pytest.raises(YoutubeQuotaExhausted):
            search_candidates(client, profile)

    def test_generic_failure_raises_plain_retrieval_error(self) -> None:
        # Network failure ≠ exhausted quota: it fails one film (#383), not the run.
        client = _FakeClient([("Волк", RuntimeError("boom")), ("The Wolf", RuntimeError("boom"))])
        profile = FilmProfile(ru_title="Волк", original_title="The Wolf", year=2025)
        with pytest.raises(TrailerRetrievalError) as excinfo:
            search_candidates(client, profile)
        assert not isinstance(excinfo.value, YoutubeQuotaExhausted)
