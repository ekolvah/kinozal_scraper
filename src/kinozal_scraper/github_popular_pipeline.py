"""GitHub new-popular источник (run_github_popular_pipeline)."""

from __future__ import annotations

import logging
from typing import Any

import requests

from kinozal_scraper.gemini_enricher import FALLBACK_MARKER, Enricher, QuotaExhausted
from kinozal_scraper.generic_pipeline import (
    ROW_HEADERS,
    NormalizedItem,
    PipelineResult,
    SourceMetrics,
    build_notification,
    extract_from_json,
    select_new_items,
)
from kinozal_scraper.http_retry import retry_api_http
from kinozal_scraper.pipeline_config import load_sources_config
from kinozal_scraper.sheets_storage import Storage
from kinozal_scraper.telegram_notifier import Notifier

logger = logging.getLogger(__name__)

# Dedicated source type (grain of steam_pipeline's `steam_charts`), not the
# former format-keyed generic `json` bucket. A generic multi-source runner is
# deferred until ≥2 sources with a uniform single-GET fetch actually exist (#275).
_SOURCE_TYPE = "github_popular"

# Paging knobs live here, not in `sources.json`: HTTP fetching is the one part of
# a source that is deliberately NOT declarative (see pipeline.md §"new source =
# config, not code"). Both numbers come from the REST Search API documentation
# (max `per_page` 100, max 1000 results per search) — never from probing the live
# endpoint. We stop *before* the ceiling instead of discovering it by error, so
# whatever GitHub returns past 1000 results can never redden a nightly run.
_PER_PAGE = 100
_SEARCH_RESULT_CEILING = 1000


def _clean_headers(headers: dict[str, str]) -> dict[str, str]:
    """Headers safe to send: a blank value or a trailing space makes one unusable."""
    return {k: v for k, v in headers.items() if v and not v.endswith(" ")}


@retry_api_http
def _get_json(url: str, params: dict[str, str], headers: dict[str, str]) -> Any:
    """One GET of the GitHub Search API, retried on transient 5xx (#365).

    403/429 are NOT retried here — for this transport they are a rate limit, not
    the anti-bot challenge `http_fetch` survives; see `http_retry` for the split.
    """
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _fetch_json(url: str, params: dict[str, str], headers: dict[str, str]) -> Any:
    """Announce dropped headers once, then fetch (retrying) with what is left.

    The announcement sits *outside* `@retry_api_http` on purpose: header cleaning
    does not depend on the attempt, so leaving it inside would print the same line
    up to four times on a 5xx.

    §IV: dropping a header is right, dropping it *silently* is not — an unset
    `GITHUB_TOKEN` expands to `"Bearer "`, the header goes, and the run degrades to
    unauthenticated search (10 req/min) whose 403 has no visible cause.

    The two drop reasons are reported apart because they call for different fixes,
    and the trailing-space message names **both** of its causes: the filter cannot
    tell `"Bearer "` (secret unset, expanded into the template) from
    `"Bearer ghp_xxx "` (secret set but pasted with a stray space). Calling either
    one "empty" would send the operator to check whether the secret exists — and
    seeing that it does, rule out the real cause.

    Only header *names* are logged; values never are.
    """
    clean_headers = _clean_headers(headers)
    if blank := sorted(k for k, v in headers.items() if not v):
        logger.warning("dropping request header(s) with a blank value: %s", ", ".join(blank))
    if padded := sorted(k for k in headers if k not in clean_headers and k not in blank):
        logger.warning(
            "dropping request header(s) whose value ends with a space — secret unset "
            "and expanded into the template, or set but pasted with a stray space: %s",
            ", ".join(padded),
        )
    return _get_json(url, params, clean_headers)


def _unwrap_records(data: Any, json_path: str | None) -> list[dict[str, Any]]:
    """Navigate into the response to find the records array."""
    if json_path is None:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return list(data.values()) if all(isinstance(v, dict) for v in data.values()) else []
        return []
    obj: Any = data
    for key in json_path.split("."):
        obj = obj.get(key, []) if isinstance(obj, dict) else []
    return obj if isinstance(obj, list) else []


def run_github_popular_pipeline(
    storage: Storage,
    notifier: Notifier,
    enricher: Enricher | None = None,
    sources_config: dict[str, Any] | None = None,
) -> list[PipelineResult]:
    results: list[PipelineResult] = []
    config = sources_config or load_sources_config()
    github_sources = [
        s for s in config["sources"] if s.get("enabled") and s["type"] == _SOURCE_TYPE
    ]
    if not github_sources:
        logger.info("no enabled '%s' source found", _SOURCE_TYPE)
        return results

    for source in github_sources:
        try:
            result = _run_single_source(source, storage, notifier, enricher)
        except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
            logger.exception("[%s] unhandled error: %s", source["id"], exc)
            result = PipelineResult(source_id=source["id"])
            result.errors.append(f"unhandled error: {exc}")
        results.append(result)

    return results


def _enrich_new_items(
    new_items: list[NormalizedItem],
    enrich_config: dict[str, Any],
    enricher: Enricher,
    source_id: str,
) -> None:
    """Enrich each new item in-place, stopping on quota exhaustion.

    On `QuotaExhausted` the current and all remaining items get the fallback
    marker (visible tripwire, #128) so a mid-batch quota outage surfaces in the
    notification rather than silently blanking the field (§IV)."""
    field = enrich_config["field"]
    # Empty `on_error` would silently blank the enriched field — use
    # the visible marker so the operator sees a tripwire (#128).
    fallback: str = enrich_config.get("on_error") or FALLBACK_MARKER
    enriched, skipped = 0, 0
    for item in new_items:
        try:
            item.raw[field] = enricher.enrich(item, enrich_config)
            enriched += 1
        except QuotaExhausted:
            item.raw[field] = fallback
            skipped += 1
            for remaining in new_items[new_items.index(item) + 1 :]:
                remaining.raw[field] = fallback
                skipped += 1
            break
    if skipped:
        logger.warning(
            "[%s] enrichment quota exhausted: %d/%d enriched, %d skipped",
            source_id,
            enriched,
            enriched + skipped,
            skipped,
        )
    elif enriched:
        logger.info("[%s] enriched %d items", source_id, enriched)


def _collect_candidates(
    source: dict[str, Any],
    existing: set[str],
    limit: int,
    result: PipelineResult,
    metrics: SourceMetrics,
) -> list[NormalizedItem] | None:
    """Page through the search results until `limit` NEW items are in reach.

    Returns the accumulated candidates, or `None` when the page fetch/extraction
    failed (the reason is already on `result.errors`).

    Depth is what #459 is about: the delivery cap used to truncate the candidate
    set, and with `sort=stars&order=desc` a freshly-qualifying repo sits at the
    *bottom* of the result set — exactly where the truncation cut. Stopping
    conditions, in order: enough new items found; a short page (the API has no
    more); the documented 1000-result ceiling, which we compute and stay inside
    rather than discover through whatever error GitHub returns past it.

    Candidate order is the API's own (`sort`/`order` search params) — we never
    reorder, so the delivery cap applies to the ranking GitHub returned.

    Rate limit, for the record: authenticated search allows 30 requests/minute, so
    even the worst case here (10 pages, times `@retry_api_http` attempts) stays
    inside it. Unauthenticated it is 10/minute — and `_fetch_json` drops a blank
    `Authorization` (see its docstring), so a run with an unset `GITHUB_TOKEN`
    could in theory spend its whole minute budget on one source. In practice
    `created:>=T-30 stars:>1000` returns well under `_PER_PAGE` results, the
    short-page break fires on page 1, and the deep path stays unreachable.
    """
    source_id = source["id"]
    max_pages = max(1, _SEARCH_RESULT_CEILING // _PER_PAGE)
    base_params = dict(source.get("params", {}))
    candidates: list[NormalizedItem] = []
    selected: list[NormalizedItem] = []

    for page in range(1, max_pages + 1):
        params = {**base_params, "per_page": str(_PER_PAGE), "page": str(page)}
        try:
            data = _fetch_json(source["url"], params, source.get("headers", {}))
        except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
            logger.exception("[%s] fetch failed: %s", source_id, exc)
            result.errors.append(f"fetch failed: {exc}")
            return None

        records = _unwrap_records(data, source.get("json_path"))
        metrics.fetched += len(records)

        if records:
            # limit=0: the cap belongs to delivery, not to how deep we look.
            extracted = extract_from_json(records, source, limit=0)
            if not extracted.ok:
                # Fail-closed, deliberately asymmetric with `github_trending`, which
                # keeps a partial extraction green. The sibling scrapes an HTML page
                # whose markup shifts cosmetically all the time, so a few unparsable
                # rows are routine drift. This source reads a *versioned JSON API*
                # where `full_name` is a guaranteed field: a record without one means
                # the response contract changed, and continuing would ship items
                # whose dedupe identity we no longer trust. Paging widened the blast
                # radius (up to 1000 records examined instead of `limit`), which
                # raises the odds of tripping this — that is accepted, not overlooked.
                logger.error("[%s] extraction errors: %s", source_id, extracted.errors)
                result.errors.extend(extracted.errors)
                return None
            candidates.extend(extracted.items)
            # All three counters are updated per page, not once after the loop: a
            # later-page failure returns early, and `extracted=0 existing=0 new=0`
            # on a run whose first page yielded 100 items reads as "extraction
            # produced nothing" — a different and wrong diagnosis from "the second
            # fetch died". A wrong number on a red run is worse than none (§IV).
            metrics.extracted = len(candidates)
            selected, metrics.existing, metrics.new = select_new_items(candidates, existing, limit)

        if len(records) < _PER_PAGE:
            break
        # `limit <= 0` means "no cap" in `select_new_items`; without this guard the
        # comparison below would be trivially true and turn "no cap" into "one page".
        if limit > 0 and len(selected) >= limit:
            break
    else:
        message = (
            f"search result ceiling reached ({max_pages} pages x {_PER_PAGE}) — "
            "scan truncated, new repositories may remain beyond it"
        )
        # Also on `result.warnings`, not just in the log: this is exactly the caveat
        # that makes a `new=0` line less reassuring than it looks, so it has to reach
        # the operator on the surface that reports `new=0` (#459).
        logger.warning("[%s] %s", source_id, message)
        result.warnings.append(message)

    return candidates


def _run_single_source(
    source: dict[str, Any],
    storage: Storage,
    notifier: Notifier,
    enricher: Enricher | None,
) -> PipelineResult:
    source_id = source["id"]
    result = PipelineResult(source_id=source_id)
    metrics = SourceMetrics()
    result.metrics = metrics

    tab = source["sheet_tab"]
    limit = int(source["limit"])
    # Read once per run, before paging: the known-key set does not change while
    # we page, and re-reading it per page would be one Sheets call per request.
    existing = storage.get_existing_keys(tab)

    candidates = _collect_candidates(source, existing, limit, result, metrics)
    if candidates is None:
        return result

    result.items = candidates
    if not candidates:
        message = f"[{source_id}] extraction produced zero items"
        logger.error("%s", message)
        result.errors.append(message)
        return result

    new_items, metrics.existing, metrics.new = select_new_items(candidates, existing, limit)
    if not new_items:
        logger.info("[%s] no new items", source_id)
        return result

    enrich_config = source.get("enrich")
    if enrich_config and enricher is not None:
        _enrich_new_items(new_items, enrich_config, enricher, source_id)

    template = source["message_template"]
    notifications = [build_notification(item, template) for item in new_items]
    sent, failed = notifier.send_items(notifications)
    metrics.sent = len(sent)

    if sent:
        sent_ids = {n.id for n in sent}
        items_to_store = [i for i in new_items if i.dedupe_key in sent_ids]
        storage.append_rows(tab, ROW_HEADERS, [i.to_row() for i in items_to_store])
        metrics.stored = len(items_to_store)

    if failed:
        message = f"{len(failed)} notification(s) failed, will retry next run"
        logger.error("[%s] %s", source_id, message)
        result.errors.append(message)

    return result


if __name__ == "__main__":
    import json
    import os
    import sys

    import gspread

    from kinozal_scraper.gemini_enricher import build_default_enricher
    from kinozal_scraper.sheets_storage import SheetsStorage
    from kinozal_scraper.telegram_notifier import TelegramNotifier

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    gc = gspread.service_account_from_dict(json.loads(os.environ["CREDENTIALS"]))
    prod_storage = SheetsStorage(gc, os.environ["SPREADSHEET_URL"])
    prod_notifier = TelegramNotifier(
        bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        chat_id=os.environ["TELEGRAM_CHAT_ID"],
    )

    prod_enricher = build_default_enricher(os.environ.get("GOOGLE_API_KEY", ""), logger)

    prod_results = run_github_popular_pipeline(prod_storage, prod_notifier, enricher=prod_enricher)

    from kinozal_scraper.alerting import (
        alert_config_rejections,
        publish_run_summary,
        report_failures,
    )

    # Before the exit-code branch below: a failed run is exactly when the counters
    # are worth reading, and publishing after `sys.exit(1)` would drop them (#459).
    publish_run_summary(prod_results)

    # Evaluate both (no short-circuit) so a config-reject alert fires even when
    # sources all succeeded; either reddens the job (#340).
    rejected = alert_config_rejections(prod_notifier, prod_enricher)
    failures = report_failures(prod_notifier, prod_results)
    if rejected or failures:
        sys.exit(1)
