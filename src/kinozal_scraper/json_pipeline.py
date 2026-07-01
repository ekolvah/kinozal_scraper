"""Generic JSON-источники (run_json_pipeline)."""

from __future__ import annotations

import logging
from typing import Any

import requests

from kinozal_scraper.gemini_enricher import FALLBACK_MARKER, Enricher, QuotaExhausted
from kinozal_scraper.generic_pipeline import (
    ROW_HEADERS,
    PipelineResult,
    build_notification,
    extract_from_json,
)
from kinozal_scraper.pipeline_config import load_sources_config
from kinozal_scraper.sheets_storage import Storage
from kinozal_scraper.telegram_notifier import Notifier

logger = logging.getLogger(__name__)


def _fetch_json(url: str, params: dict[str, str], headers: dict[str, str]) -> Any:
    clean_headers = {k: v for k, v in headers.items() if v and not v.endswith(" ")}
    resp = requests.get(url, params=params, headers=clean_headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


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


def run_json_pipeline(
    storage: Storage,
    notifier: Notifier,
    enricher: Enricher | None = None,
    sources_config: dict[str, Any] | None = None,
) -> list[PipelineResult]:
    results: list[PipelineResult] = []
    config = sources_config or load_sources_config()
    json_sources = [s for s in config["sources"] if s.get("enabled") and s["type"] == "json"]
    if not json_sources:
        logger.info("no enabled json sources found")
        return results

    for source in json_sources:
        try:
            result = _run_single_source(source, storage, notifier, enricher)
        except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
            logger.exception("[%s] unhandled error: %s", source["id"], exc)
            result = PipelineResult(source_id=source["id"])
            result.errors.append(f"unhandled error: {exc}")
        results.append(result)

    return results


def _run_single_source(  # noqa: C901
    source: dict[str, Any],
    storage: Storage,
    notifier: Notifier,
    enricher: Enricher | None,
) -> PipelineResult:
    source_id = source["id"]
    result = PipelineResult(source_id=source_id)

    try:
        data = _fetch_json(
            source["url"],
            source.get("params", {}),
            source.get("headers", {}),
        )
    except Exception as exc:  # noqa: BLE001 — per-source isolation: logged + surfaced via result.errors
        logger.exception("[%s] fetch failed: %s", source_id, exc)
        result.errors.append(f"fetch failed: {exc}")
        return result

    records = _unwrap_records(data, source.get("json_path"))

    sort_key = source.get("sort_by")
    if sort_key:
        records.sort(
            key=lambda r: int(r.get(sort_key) or 0), reverse=source.get("sort_reverse", False)
        )

    extracted = extract_from_json(records, source)
    if not extracted.ok:
        logger.error("[%s] extraction errors: %s", source_id, extracted.errors)
        result.errors.extend(extracted.errors)
        return result
    result.items = extracted.items

    tab = source["sheet_tab"]
    existing = storage.get_existing_keys(tab)
    new_items = [i for i in result.items if i.dedupe_key not in existing]
    if not new_items:
        logger.info("[%s] no new items", source_id)
        return result

    enrich_config = source.get("enrich")
    if enrich_config and enricher is not None:
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

    template = source["message_template"]
    notifications = [build_notification(item, template) for item in new_items]
    sent, failed = notifier.send_items(notifications)

    if sent:
        sent_ids = {n.id for n in sent}
        items_to_store = [i for i in new_items if i.dedupe_key in sent_ids]
        storage.append_rows(tab, ROW_HEADERS, [i.to_row() for i in items_to_store])

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

    prod_results = run_json_pipeline(prod_storage, prod_notifier, enricher=prod_enricher)

    if any(not r.ok for r in prod_results):
        sys.exit(1)
