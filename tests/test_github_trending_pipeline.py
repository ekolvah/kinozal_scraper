from __future__ import annotations

import logging
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

from gemini_enricher import FALLBACK_MARKER, QuotaExhausted
from generic_pipeline import extract_from_html
from github_trending_pipeline import (
    _normalize_items,
    run_github_trending_pipeline,
)
from sheets_storage import InMemoryStorage
from telegram_notifier import InMemoryNotifier

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "github_trending" / "trending_daily.html"


_TRENDING_SOURCE: dict[str, Any] = {
    "id": "github_trending",
    "enabled": True,
    "type": "html",
    "url": "https://github.com/trending?since=daily",
    "base_url": "https://github.com",
    "row_selector": "article.Box-row",
    "limit": 25,
    "sheet_tab": "github_projects",
    "dedupe_key": "h2 a@href",
    "fields": {
        "title": "h2 a@href",
        "url": "h2 a@href",
        "description": "p",
        "metric": 'a[href$="/stargazers"]',
        "image_url": None,
    },
    "message_template": "<b>{title}</b>\n{description}\n⭐ {metric} (+{stars_today} today)\n{url}",
}

_SOURCES_CONFIG: dict[str, Any] = {"version": 1, "sources": [_TRENDING_SOURCE]}


def _fixture_html() -> str:
    return _FIXTURE_PATH.read_text(encoding="utf-8")


def _run(
    html: str | None = None,
    existing_keys: set[str] | None = None,
    sources_config: dict[str, Any] | None = None,
) -> tuple[InMemoryStorage, InMemoryNotifier]:
    storage = InMemoryStorage()
    if existing_keys:
        storage.seed_existing("github_projects", existing_keys)
    notifier = InMemoryNotifier()
    config = sources_config or _SOURCES_CONFIG

    with unittest.mock.patch(
        "github_trending_pipeline._fetch_html",
        return_value=html if html is not None else _fixture_html(),
    ):
        run_github_trending_pipeline(storage, notifier, sources_config=config)

    return storage, notifier


# ── US1: extraction ──────────────────────────────────────────────────────────


class TestUS1Extraction(unittest.TestCase):
    def test_extracts_rows_from_fixture(self) -> None:
        result = extract_from_html(_fixture_html(), _TRENDING_SOURCE)
        self.assertTrue(result.items, result.errors)
        items = _normalize_items(result.items)

        self.assertGreaterEqual(len(items), 1)
        for item in items:
            self.assertTrue(item.title)
            self.assertTrue(item.url.startswith("https://github.com/"), item.url)
            self.assertTrue(item.dedupe_key)
            self.assertRegex(item.dedupe_key, r"^[\w.\-]+/[\w.\-]+$")

    def test_partial_row_emits_with_warning(self) -> None:
        # Row without <p> description.
        html = """
        <html><body>
          <article class="Box-row">
            <h2><a href="/foo/bar">foo / bar</a></h2>
            <span class="d-inline-block float-sm-right">5 stars today</span>
          </article>
        </body></html>
        """
        with self.assertLogs("github_trending_pipeline", level="WARNING") as caplog:
            _, notifier = _run(html=html)
        self.assertEqual(len(notifier.sent), 1)
        joined = "\n".join(caplog.output)
        # warning may be either about empty description OR fired below; we only
        # require something logged at warning for this dedupe_key
        self.assertIn("foo/bar", joined)

    def test_respects_limit(self) -> None:
        source = {**_TRENDING_SOURCE, "limit": 5}
        result = extract_from_html(_fixture_html(), source)
        items = _normalize_items(result.items)
        self.assertEqual(len(items), 5)


# ── US2: cross-source dedupe ─────────────────────────────────────────────────


class TestUS2CrossSourceDedupe(unittest.TestCase):
    def test_skips_repo_already_in_shared_tab(self) -> None:
        result = extract_from_html(_fixture_html(), _TRENDING_SOURCE)
        items = _normalize_items(result.items)
        n = len(items)
        skip_key = items[0].dedupe_key

        storage, notifier = _run(existing_keys={skip_key})
        self.assertEqual(len(notifier.sent), n - 1)
        sent_ids = {n_.id for n_ in notifier.sent}
        self.assertNotIn(skip_key, sent_ids)

    def test_dedupe_key_normalised_to_owner_repo(self) -> None:
        result = extract_from_html(_fixture_html(), _TRENDING_SOURCE)
        items = _normalize_items(result.items)
        for item in items:
            self.assertRegex(item.dedupe_key, r"^[\w.\-]+/[\w.\-]+$")
            self.assertFalse(item.dedupe_key.startswith("/"))
            self.assertFalse(item.dedupe_key.startswith("http"))

    def test_intra_run_overlap_uses_storage_state(self) -> None:
        """Pre-seed storage with the trending fixture's first item's key (as
        json_pipeline would after a prior workflow step). Trending pipeline must
        then filter that key out."""
        result = extract_from_html(_fixture_html(), _TRENDING_SOURCE)
        items = _normalize_items(result.items)
        seeded = {items[0].dedupe_key}

        storage = InMemoryStorage()
        storage.seed_existing("github_projects", seeded)
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "github_trending_pipeline._fetch_html",
            return_value=_fixture_html(),
        ):
            run_github_trending_pipeline(storage, notifier, sources_config=_SOURCES_CONFIG)

        sent_ids = {n_.id for n_ in notifier.sent}
        self.assertNotIn(items[0].dedupe_key, sent_ids)


# ── US3: visibility on zero rows ─────────────────────────────────────────────


class TestUS3Visibility(unittest.TestCase):
    def test_zero_row_extraction_signals_failure(self) -> None:
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "github_trending_pipeline._fetch_html",
            return_value="<html><body></body></html>",
        ):
            results = run_github_trending_pipeline(
                storage, notifier, sources_config=_SOURCES_CONFIG
            )
        self.assertEqual(notifier.sent, [])
        self.assertEqual(storage.stored_rows("github_projects"), [])
        self.assertTrue(any(not r.ok for r in results))

    def test_partial_row_logs_warning_for_missing_metric(self) -> None:
        html = """
        <html><body>
          <article class="Box-row">
            <h2><a href="/owner/repo">owner / repo</a></h2>
            <p>desc</p>
          </article>
        </body></html>
        """
        with self.assertLogs("github_trending_pipeline", level="WARNING") as caplog:
            _, notifier = _run(html=html)
        self.assertEqual(len(notifier.sent), 1)
        joined = "\n".join(caplog.output)
        self.assertIn("owner/repo", joined)
        self.assertIn("metric", joined.lower())

    def test_main_exits_nonzero_on_zero_rows(self) -> None:
        """The __main__ block must exit 1 when extraction returns zero items."""
        import os
        import subprocess
        import sys
        import tempfile

        # Write a minimal sources.json with the trending entry pointed at file://
        # an empty-body fixture, plus a stub html file the pipeline will fetch.
        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "empty.html"
            html_path.write_text("<html><body></body></html>", encoding="utf-8")

            sources_path = Path(tmp) / "sources.json"
            sources_path.write_text(
                '{"version": 1, "sources": ['
                + '{"id": "github_trending", "enabled": true, "type": "html",'
                + f' "url": "file:///{html_path.as_posix()}",'
                + ' "base_url": "https://github.com",'
                + ' "row_selector": "article.Box-row",'
                + ' "limit": 25, "sheet_tab": "github_projects",'
                + ' "dedupe_key": "h2 a@href",'
                + ' "fields": {"title": "h2 a@href", "url": "h2 a@href"},'
                + ' "message_template": "{title}"}'
                + "]}",
                encoding="utf-8",
            )

            env = {
                **os.environ,
                "CREDENTIALS": "{}",  # never used because we'll fail before storage
                "SPREADSHEET_URL": "https://docs.google.com/spreadsheets/d/x",
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_CHAT_ID": "x",
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                "GITHUB_TRENDING_SOURCES_PATH": str(sources_path),
                "GITHUB_TRENDING_DRY_RUN": "1",
            }
            proc = subprocess.run(
                [sys.executable, "src/github_trending_pipeline.py"],
                cwd=str(Path(__file__).resolve().parents[1]),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(proc.returncode, 1, proc.stderr)


# ── #86: metric column semantics ─────────────────────────────────────────────


class TestMetricColumnSemantics(unittest.TestCase):
    """Pin-tests for #86: `github_projects.metric` MUST be total stargazers
    count as a digit-only string for both github_new_popular and
    github_trending — the two GitHub sources share one column.
    """

    def test_metric_is_total_stars_not_daily(self) -> None:
        storage, _ = _run()
        rows = storage.stored_rows("github_projects")
        self.assertGreaterEqual(len(rows), 1)
        # Row schema (generic_pipeline.ROW_HEADERS):
        # [dedupe_key, title, url, metric, source_id, notified_at]
        for row in rows:
            metric = str(row[3])
            with self.subTest(metric=metric):
                self.assertRegex(metric, r"^\d+$", f"metric must be digit-only, got {metric!r}")
        # Sanity floor — first fixture row has 14,113 total stars (well above
        # any plausible daily delta which is typically ≤ a few thousand).
        first_metric = int(str(rows[0][3]))
        self.assertGreaterEqual(
            first_metric,
            100,
            "first metric looks like a daily delta, not a total — too small",
        )

    def test_stars_today_available_in_raw(self) -> None:
        result = extract_from_html(_fixture_html(), _TRENDING_SOURCE)
        items = _normalize_items(result.items)
        # Need pipeline-level enrichment, not just normalize — use the full
        # run so the helper that fills `raw["stars_today"]` runs.
        _, notifier = _run()
        # We assert via notification content because raw is not exposed by
        # the notifier; the next test covers the notification path. Here we
        # just check the in-process item population stayed in lockstep.
        self.assertGreaterEqual(len(items), 1)
        # After full pipeline run, the produced Notification must have been
        # built from items whose raw contained stars_today digits.
        first_text = notifier.sent[0].text
        # stars_today digits appear in the rendered notification — regex
        # against "(+\d+ today)" is the tightest assertion.
        self.assertRegex(
            first_text,
            r"\+\d+ today",
            f"expected '(+N today)' in notification, got: {first_text!r}",
        )

    def test_notification_shows_total_and_today(self) -> None:
        storage, notifier = _run()
        self.assertGreaterEqual(len(notifier.sent), 1)
        rows = storage.stored_rows("github_projects")
        for sent, row in zip(notifier.sent, rows, strict=False):
            total = str(row[3])
            with self.subTest(total=total, text=sent.text):
                # Total must appear in the notification (the stored value
                # equals what was rendered).
                self.assertIn(
                    total,
                    sent.text,
                    f"total {total!r} missing from notification",
                )
                # And a "+N today" velocity marker must accompany it.
                self.assertRegex(sent.text, r"\+\d+ today")


# ── pipeline mechanics ───────────────────────────────────────────────────────


class TestPipelineMechanics(unittest.TestCase):
    def test_new_items_stored_and_notified(self) -> None:
        storage, notifier = _run()
        self.assertEqual(len(storage.stored_rows("github_projects")), len(notifier.sent))
        self.assertGreaterEqual(len(notifier.sent), 1)

    def test_no_enabled_sources_does_nothing(self) -> None:
        config: dict[str, Any] = {
            "version": 1,
            "sources": [{**_TRENDING_SOURCE, "enabled": False}],
        }
        storage, notifier = _run(sources_config=config)
        self.assertEqual(storage.stored_rows("github_projects"), [])
        self.assertEqual(notifier.sent, [])

    def test_url_in_notification(self) -> None:
        _, notifier = _run()
        self.assertTrue(notifier.sent[0].text)
        self.assertIn("github.com/", notifier.sent[0].text)

    def test_failed_notifications_are_not_stored_and_mark_result_not_ok(self) -> None:
        result = extract_from_html(_fixture_html(), _TRENDING_SOURCE)
        items = _normalize_items(result.items)
        failed_key = items[0].dedupe_key

        storage = InMemoryStorage()
        notifier = InMemoryNotifier(fail_ids={failed_key})
        with unittest.mock.patch(
            "github_trending_pipeline._fetch_html",
            return_value=_fixture_html(),
        ):
            results = run_github_trending_pipeline(
                storage, notifier, sources_config=_SOURCES_CONFIG
            )

        stored_keys = [row[0] for row in storage.stored_rows("github_projects")]
        self.assertNotIn(failed_key, stored_keys)
        self.assertFalse(results[0].ok)
        self.assertTrue(any("notification(s) failed" in err for err in results[0].errors))


# ── #88: Russian who/pain enrichment for trending ────────────────────────────


_TRENDING_SOURCE_WITH_ENRICH: dict[str, Any] = {
    **_TRENDING_SOURCE,
    "enrich": {
        "field": "summary_ru",
        "prompt": "Для кого и Зачем: $title — $description",
        "parameters": {"temperature": 0.2, "max_tokens": 150},
        "on_error": "",
    },
    "message_template": ("<b>{title}</b>\n{summary_ru}\n⭐ {metric} (+{stars_today} today)\n{url}"),
}

_ENRICH_SOURCES_CONFIG: dict[str, Any] = {
    "version": 1,
    "sources": [_TRENDING_SOURCE_WITH_ENRICH],
}


def _run_with_enricher(
    enricher: Any,
    html: str | None = None,
    existing_keys: set[str] | None = None,
) -> tuple[InMemoryStorage, InMemoryNotifier]:
    storage = InMemoryStorage()
    if existing_keys:
        storage.seed_existing("github_projects", existing_keys)
    notifier = InMemoryNotifier()

    with unittest.mock.patch(
        "github_trending_pipeline._fetch_html",
        return_value=html if html is not None else _fixture_html(),
    ):
        run_github_trending_pipeline(
            storage,
            notifier,
            enricher=enricher,
            sources_config=_ENRICH_SOURCES_CONFIG,
        )

    return storage, notifier


class _FakeRuEnricher:
    def enrich(self, item: Any, enrich_config: dict[str, Any]) -> str:
        return "Для кого: ML-инженер\nЗачем: ускоряет inference"


class _FlakyEnricher:
    """Returns real text once, then raises QuotaExhausted for every subsequent call."""

    def __init__(self) -> None:
        self.call_count = 0

    def enrich(self, item: Any, enrich_config: dict[str, Any]) -> str:
        self.call_count += 1
        if self.call_count == 1:
            return "Для кого: разработчик\nЗачем: что-то полезное"
        raise QuotaExhausted


class TestRussianEnrichment(unittest.TestCase):
    def test_enricher_field_in_notification(self) -> None:
        _, notifier = _run_with_enricher(_FakeRuEnricher())
        self.assertGreaterEqual(len(notifier.sent), 1)
        first_text = notifier.sent[0].text
        self.assertIn("Для кого: ML-инженер", first_text)
        self.assertIn("Зачем: ускоряет inference", first_text)

    def test_no_enricher_no_summary_ru_in_text(self) -> None:
        # enricher=None path — summary_ru placeholder resolves to "" (no crash,
        # no literal placeholder leakage, no who/pain marker text).
        _, notifier = _run_with_enricher(None)
        self.assertGreaterEqual(len(notifier.sent), 1)
        first_text = notifier.sent[0].text
        self.assertNotIn("{summary_ru}", first_text)
        self.assertNotIn("Для кого", first_text)

    def test_quota_exhausted_falls_back_but_still_sends(self) -> None:
        flaky = _FlakyEnricher()
        _, notifier = _run_with_enricher(flaky)
        self.assertGreaterEqual(len(notifier.sent), 2)
        # First item got the real enrichment text.
        self.assertIn("Для кого: разработчик", notifier.sent[0].text)
        # Subsequent items have empty summary_ru (on_error fallback) — no
        # "Для кого" marker present.
        for sent in notifier.sent[1:]:
            self.assertNotIn("Для кого", sent.text)
        # Enricher was called once for the first item, then once more (which
        # raised) — the loop must short-circuit after the raise without
        # calling for the remaining items.
        self.assertEqual(flaky.call_count, 2)


class _AlwaysQuotaEnricher:
    """Mimics rotator after all models are exhausted: every call raises
    QuotaExhausted. Used to pin the degraded-notification shape (#128)."""

    def enrich(self, item: Any, enrich_config: dict[str, Any]) -> str:
        raise QuotaExhausted


class TestDegradedEnrichmentVisibility(unittest.TestCase):
    """Pin-test for #128 second-order bug: when every Gemini model is
    unavailable, the pipeline currently substitutes `on_error` (which is
    "" for github_trending) into `summary_ru`, producing a blank line in
    Telegram instead of a visible tripwire. Must use FALLBACK_MARKER
    whenever `on_error` is empty (Constitution Principle IV).
    """

    def test_empty_on_error_falls_back_to_marker_when_quota_exhausted(self) -> None:
        _, notifier = _run_with_enricher(_AlwaysQuotaEnricher())
        self.assertGreaterEqual(len(notifier.sent), 1)
        for sent in notifier.sent:
            with self.subTest(text=sent.text):
                self.assertIn(
                    FALLBACK_MARKER,
                    sent.text,
                    "degraded notification must carry the visible marker, not blank",
                )


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
