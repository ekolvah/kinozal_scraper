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
        # `result` and its counters are built HERE, not inside `_run_single_source`,
        # so the catch-all below cannot throw them away. This is the one failure path
        # where delivery may already have happened (`storage.append_rows` or
        # `notifier.send_items` raising), which makes `sent`/`stored` the numbers an
        # operator most needs before re-running — a red run with no counters at all
        # is the same defect as a red run with zeroed ones (§IV).
        result = PipelineResult(source_id=source["id"])
        metrics = SourceMetrics()
        result.metrics = metrics
        try:
            _run_single_source(source, storage, notifier, enricher, result, metrics)
        except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
            logger.exception("[%s] unhandled error: %s", source["id"], exc)
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


def _extract_candidates(
    source: dict[str, Any],
    result: PipelineResult,
    metrics: SourceMetrics,
) -> list[NormalizedItem] | None:
    """Fetch the source's top-N and normalise it, or `None` on failure.

    One request, `per_page` from the source's own params: the product intent is the
    **top** of `created:>=T-30 stars:>1000` ranked by stars, and that ranking is what
    `limit` selects. Scanning deeper was tried and reverted — it turns the source into
    "any repo above the star floor we have not seen yet", which delivers the bottom of
    the list (measured 2026-08-05: `total_count=77`, positions 60+ sit at ~1000 stars).

    `None` means the reason is already on `result.errors`. Counters are written as we
    go, so a failure still publishes what it managed to measure (§IV)."""
    source_id = source["id"]
    try:
        data = _fetch_json(
            source["url"],
            source.get("params", {}),
            source.get("headers", {}),
        )
    except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
        logger.exception("[%s] fetch failed: %s", source_id, exc)
        result.errors.append(f"fetch failed: {exc}")
        return None

    records = _unwrap_records(data, source.get("json_path"))
    metrics.fetched = len(records)

    extracted = extract_from_json(records, source)
    if not extracted.ok:
        logger.error("[%s] extraction errors: %s", source_id, extracted.errors)
        result.errors.extend(extracted.errors)
        return None

    metrics.extracted = len(extracted.items)
    return extracted.items


def _run_single_source(
    source: dict[str, Any],
    storage: Storage,
    notifier: Notifier,
    enricher: Enricher | None,
    result: PipelineResult,
    metrics: SourceMetrics,
) -> None:
    """Run one source, recording everything on the caller's `result`/`metrics`.

    Owns no result of its own on purpose: the caller's per-source catch-all has to
    be able to publish whatever was counted before an exception escaped."""
    source_id = source["id"]
    tab = source["sheet_tab"]

    # Storage is read BEFORE extraction so nothing can fail between `extracted`
    # being counted and the `existing`/`new` split being written from it. Reading
    # it after left one path — `get_existing_keys` raising `SchemaError` on a tab
    # with missing columns — publishing `extracted=10 existing=0 new=0`, which
    # breaks the invariant the operator reads the line against (§IV: a wrong
    # number is worse than none). Cost of the reorder: a run whose fetch dies now
    # pays one Sheets read it used to skip — one call, on an already-red run.
    existing = storage.get_existing_keys(tab)

    candidates = _extract_candidates(source, result, metrics)
    if candidates is None:
        return
    result.items = candidates

    new_items, metrics.existing, metrics.new = select_new_items(candidates, existing)
    if not new_items:
        logger.info("[%s] no new items", source_id)
        return

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
