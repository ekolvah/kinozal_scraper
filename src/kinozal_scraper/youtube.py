"""YouTube-retrieval пула кандидатов трейлера (#140): `Youtube.search_candidates`.

Selection (выбор одного) — не здесь, а в `trailer_strategy.HeuristicStrategy` (#141),
вызывается из `kinozal_pipeline.enrich_with_trailer` (#144). Прежний одиночный
`get_trailer_url` (англо-смещённый после #138 → RU-регрессия #315) удалён."""

from __future__ import annotations

import html
import logging
import os
from typing import Any

from googleapiclient.discovery import build

from kinozal_scraper.trailer_strategy import Candidate, FilmProfile

logger = logging.getLogger(__name__)


class TrailerRetrievalError(RuntimeError):
    """Ни одна ветка union не отработала — retrieval не состоялся (#383).

    Живёт здесь, а не в отдельном модуле ошибок: один raiser (`search_candidates`),
    один ловец (`kinozal_pipeline.enrich_with_trailer`) — выносить абстракцию с
    единственным вызывающим запрещает §VII.
    """


class YoutubeQuotaExhausted(TrailerRetrievalError):
    """Квота YouTube исчерпана — до конца прогона в API ходить бессмысленно (#384).

    Подкласс, а не соседняя ветка иерархии: всё, что ловит `TrailerRetrievalError`
    (#383), продолжает работать без изменений, а знающий вызывающий
    (`kinozal_pipeline`) отличает «кончилась квота» от «сервер моргнул» и
    останавливает обогащение вместо того, чтобы биться о заведомый отказ.
    """


# Причины из usageLimits-семейства Google. Ось «квота/не-квота», а не «транзиент/
# терминал»: при 100 `search.list` в сутки (замер 2026-07-26) `rateLimitExceeded`
# и `quotaExceeded` для нас одно и то же событие — минутный и дневной потолки
# численно совпадают, поэтому пауза не поможет ни в одном из случаев.
_QUOTA_REASONS = frozenset(
    {"quotaexceeded", "dailylimitexceeded", "ratelimitexceeded", "userratelimitexceeded"}
)


def _is_quota_error(exc: BaseException) -> bool:
    """`HttpError` от исчерпанной квоты? Читает `error_details`, а НЕ `.reason`.

    В `googleapiclient/errors.py` `self.reason = data["error"]["message"]` — это
    человеческий текст («Rate Limit Exceeded»), который Google волен переписать;
    машинный код лежит в `error_details` и приходит в двух формах: legacy
    `errors[0]["reason"] == "rateLimitExceeded"` и ErrorInfo
    `details[0]["reason"] == "RATE_LIMIT_EXCEEDED"` — отсюда нормализация.
    Статус 403 наравне с 429: те же usageLimits-причины Google исторически отдаёт
    и как 403. `error_details` бывает и строкой (когда в теле только `message`) —
    такой ответ машинной причины не несёт, значит «не квота».
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status not in (403, 429):
        return False
    details = getattr(exc, "error_details", None)
    if not isinstance(details, list):
        return False
    for detail in details:
        reason = detail.get("reason") if isinstance(detail, dict) else None
        if isinstance(reason, str) and reason.replace("_", "").lower() in _QUOTA_REASONS:
            return True
    return False


def _search_one(client: Any, query: str) -> list[Candidate]:
    """Один YouTube-запрос → кандидаты (только `youtube#video`), snippet-поля
    отображены в `Candidate`. БЕЗ year/title-фильтра — это чистый retrieval, год
    отсеивает selection (`FirstResultStrategy`), не retrieval.

    Текстовые поля snippet'а приходят HTML-escaped (`Marvel&#39;s`, `Deadpool
    &amp; Wolverine`) и декодируются здесь, на границе протокола, а не в
    `normalize_title` (§II — чинить форму данных на входе, а не в каждом
    потребителе). Иначе entity доезжает до токенизации названия: `&#39;` даёт
    токен `39`, `&amp;` — `amp`, и фраза рвётся ровно там, где должна была
    совпасть (#412)."""
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
                title=html.unescape(snippet.get("title", "")),
                channel=html.unescape(snippet.get("channelTitle", "")),
                description=html.unescape(snippet.get("description", "")),
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
    WARNING и не роняет retrieval — отдаём кандидатов уцелевшей ветки. Но падение
    ВСЕХ веток — это отказ retrieval, а не пустой пул: `TrailerRetrievalError`
    (#383). Пустой список означает ровно одно — «запросы прошли, YouTube ничего не
    вернул»; смешивать с ним 429 нельзя, иначе инфраструктурный отказ читается как
    честный промах подбора (74 таких строки в прогоне 2026-07-25). `client` —
    инъектируемый googleapiclient youtube-resource, чтобы harness (`--record`)
    переиспользовал тот же retrieval (§II)."""
    year = profile.year
    titles = [profile.ru_title]
    if profile.original_title and profile.original_title != profile.ru_title:
        titles.append(profile.original_title)
    seen: set[str] = set()
    pool: list[Candidate] = []
    failed = 0
    last_exc: Exception | None = None
    quota_seen = False
    queries = [f"{t} {year} trailer" if year else f"{t} trailer" for t in titles]
    # §IV-видимость грамматики (#412 review): запросы — единственное место, где
    # видно, ЧТО пайплайн счёл названием. По ним же нашли #385 (`x64 2024 trailer`
    # 27 раз), но тогда они попали в лог случайно — как текст упавших по квоте
    # веток. Раньше это дополняла per-run строка классификации листингов, ушедшая
    # вместе с `_is_game_url`; без лога новый служебный литерал во 2-м сегменте
    # (`RUS`, `Multi`, `Update 5`) уехал бы в запрос как «оригинальное название», а
    # исход был бы неотличим от честного «трейлера не существует».
    logger.info("trailer retrieval queries for %r: %s", profile.ru_title, queries)
    for query in queries:
        try:
            candidates = _search_one(client, query)
        except Exception as exc:  # noqa: BLE001 — best-effort breadth: one union branch failing must not sink the whole pool (§IV); всеобщий отказ ловится счётчиком ниже
            logger.warning("trailer retrieval branch failed for %r: %s", query, exc)
            failed += 1
            last_exc = exc
            quota_seen = quota_seen or _is_quota_error(exc)
            continue
        for candidate in candidates:
            if candidate.video_id in seen:
                continue
            seen.add(candidate.video_id)
            pool.append(candidate)
    # `titles` непуст по построению (в нём всегда есть ru_title), поэтому
    # `failed == len(titles)` не может сработать на нулевом числе попыток —
    # страховка `attempted > 0` была бы мёртвым кодом (прецедент #256).
    if failed == len(titles):
        # Квотный отказ останавливает обогащение до конца прогона (#384), любой
        # другой роняет только этот фильм (#383) — смешать их значило бы глушить
        # трейлеры из-за одного 500. Решает ЛЮБАЯ квотная ветка, а не последняя:
        # при смешанной паре (RU упал по квоте, оригинал по таймауту) выбор по
        # `last_exc` потерял бы квотный сигнал и заставил следующий фильм открывать
        # его заново — квота-то уже кончилась.
        error = YoutubeQuotaExhausted if quota_seen else TrailerRetrievalError
        raise error(
            f"all {failed} retrieval branch(es) failed for {profile.ru_title!r}"
        ) from last_exc
    return pool


class Youtube:
    def __init__(self) -> None:
        self.youtube = build("youtube", "v3", developerKey=os.environ["API_KEY"])

    def search_candidates(self, profile: FilmProfile) -> list[Candidate]:
        """Пул кандидатов для `profile` через общий `search_candidates` (#140)."""
        return search_candidates(self.youtube, profile)
