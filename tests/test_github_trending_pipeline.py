"""Tests for the `github_trending` source, driven end-to-end through doubles.

Covers extraction, cross-source dedupe against the shared tab, the `metric`
column semantics pinned by `storage.md`, and visibility when the Russian
enrichment degrades or the run yields zero rows.
"""

from __future__ import annotations

import logging
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

from kinozal_scraper.gemini_enricher import FALLBACK_MARKER, QuotaExhausted
from kinozal_scraper.generic_pipeline import extract_from_html
from kinozal_scraper.github_trending_pipeline import (
    _normalize_items,
    run_github_trending_pipeline,
)
from kinozal_scraper.sheets_storage import InMemoryStorage
from kinozal_scraper.telegram_notifier import InMemoryNotifier

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
        "kinozal_scraper.github_trending_pipeline.fetch_html",
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
        with self.assertLogs("kinozal_scraper.github_trending_pipeline", level="WARNING") as caplog:
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
        github_popular_pipeline would after a prior workflow step). Trending pipeline must
        then filter that key out."""
        result = extract_from_html(_fixture_html(), _TRENDING_SOURCE)
        items = _normalize_items(result.items)
        seeded = {items[0].dedupe_key}

        storage = InMemoryStorage()
        storage.seed_existing("github_projects", seeded)
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.github_trending_pipeline.fetch_html",
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
            "kinozal_scraper.github_trending_pipeline.fetch_html",
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
        with self.assertLogs("kinozal_scraper.github_trending_pipeline", level="WARNING") as caplog:
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
                # BOTH halves of the decoding contract (#364). `encoding` below fixes
                # the parent; this fixes the child, which would otherwise write to the
                # pipe in the OS code page. Without it a Cyrillic byte in the child's
                # traceback kills the reader thread — and `proc.stderr`, the assertion
                # message on the line below, is exactly what would be lost.
                "PYTHONUTF8": "1",
            }
            # `-m` runs the package entry point; PYTHONPATH=src (set in env above)
            # puts the package on sys.path without depending on an editable install.
            proc = subprocess.run(
                [sys.executable, "-m", "kinozal_scraper.github_trending_pipeline"],
                cwd=str(Path(__file__).resolve().parents[1]),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
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
        # Row columns (generic_pipeline.ROW_HEADERS): dedupe_key, title, url,
        # metric, source_id, notified_at
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
            "kinozal_scraper.github_trending_pipeline.fetch_html",
            return_value=_fixture_html(),
        ):
            results = run_github_trending_pipeline(
                storage, notifier, sources_config=_SOURCES_CONFIG
            )

        stored_keys = [row[0] for row in storage.stored_rows("github_projects")]
        self.assertNotIn(failed_key, stored_keys)
        self.assertFalse(results[0].ok)
        self.assertTrue(any("notification(s) failed" in err for err in results[0].errors))

    def test_fetch_failure_recorded_in_result_errors(self) -> None:
        # fetch-fail branch: fetch raises → the source's result stays in
        # `results` with a "fetch failed" error and not-ok (#271 guard: the
        # refactor rewrites this branch's exit from `append+continue` to
        # `return result`, and it had zero coverage before).
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.github_trending_pipeline.fetch_html",
            side_effect=Exception("boom"),
        ):
            results = run_github_trending_pipeline(
                storage, notifier, sources_config=_SOURCES_CONFIG
            )

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertTrue(any("fetch failed" in err for err in results[0].errors))
        self.assertEqual(storage.stored_rows("github_projects"), [])
        self.assertEqual(notifier.sent, [])

    def test_no_url_source_skipped_and_absent_from_results(self) -> None:
        # no-url branch: an enabled source with empty url is silently skipped —
        # a WARNING is logged and the source produces NO result entry (#271
        # guard: the refactor rewrites this branch's `continue` to `return None`
        # with `if r is not None` in the parent; it had zero coverage before).
        config: dict[str, Any] = {
            "version": 1,
            "sources": [{**_TRENDING_SOURCE, "url": ""}],
        }
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with (
            unittest.mock.patch(
                "kinozal_scraper.github_trending_pipeline.fetch_html",
                return_value=_fixture_html(),
            ),
            self.assertLogs(
                "kinozal_scraper.github_trending_pipeline", level="WARNING"
            ) as captured,
        ):
            results = run_github_trending_pipeline(storage, notifier, sources_config=config)

        self.assertEqual(results, [])
        self.assertTrue(any("no URL configured" in line for line in captured.output))
        self.assertEqual(storage.stored_rows("github_projects"), [])
        self.assertEqual(notifier.sent, [])


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
        "kinozal_scraper.github_trending_pipeline.fetch_html",
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


# ── #459: the whole page is searched, `limit` caps only what is delivered ────


def _row(name: str, *, description: str = "desc", stars: str = "1,000") -> str:
    return (
        '<article class="Box-row">'
        f'<h2><a href="/{name}">{name.replace("/", " / ")}</a></h2>'
        f"<p>{description}</p>"
        f'<a href="/{name}/stargazers">{stars}</a>'
        '<span class="d-inline-block float-sm-right">5 stars today</span>'
        "</article>"
    )


def _page_html(*names: str) -> str:
    return "<html><body>" + "".join(_row(n) for n in names) + "</body></html>"


class TestTrendingSearchesWholePage(unittest.TestCase):
    """#459: the trending page IS the whole candidate set — there is no upstream
    pagination — so `limit` must not cut the page down before dedup runs, or a
    new repo sitting below position `limit` stays invisible forever."""

    def test_new_repo_below_limit_is_notified(self) -> None:
        source = {**_TRENDING_SOURCE, "limit": 2}
        config = {"version": 1, "sources": [source]}
        _, notifier = _run(
            html=_page_html("a/1", "a/2", "b/new"),
            existing_keys={"a/1", "a/2"},
            sources_config=config,
        )
        self.assertEqual([n.id for n in notifier.sent], ["b/new"])

    def test_delivers_at_most_limit_new_items(self) -> None:
        source = {**_TRENDING_SOURCE, "limit": 2}
        config = {"version": 1, "sources": [source]}
        _, notifier = _run(html=_page_html("b/1", "b/2", "b/3"), sources_config=config)
        self.assertEqual(len(notifier.sent), 2)

    def test_drift_warning_survives_a_fully_known_page(self) -> None:
        # The steady state is the whole point: when every row is already in
        # `github_projects` there is nothing to deliver, and a drift check scoped
        # to delivered items would go silent exactly then — selector rot invisible
        # on precisely the quiet days #459 exists to make readable.
        source = {**_TRENDING_SOURCE, "limit": 5}
        config = {"version": 1, "sources": [source]}
        rows = "".join(_row(f"a/{i}", stars="") for i in range(3))
        html = f"<html><body>{rows}</body></html>"
        with self.assertLogs("kinozal_scraper.github_trending_pipeline", level="WARNING") as caplog:
            _, notifier = _run(
                html=html,
                existing_keys={"a/0", "a/1", "a/2"},
                sources_config=config,
            )
        self.assertEqual(notifier.sent, [])
        joined = "\n".join(caplog.output)
        self.assertIn("3 of 3 rows have an empty metric", joined)
        self.assertIn("may have drifted", joined)

    def test_drift_reaches_the_run_summary_not_only_the_log(self) -> None:
        # Drift leaves no gap between `fetched` and `extracted`, so a summary reader
        # has no cue to go looking for it — the warning has to travel on the result.
        source = {**_TRENDING_SOURCE, "limit": 5}
        config = {"version": 1, "sources": [source]}
        rows = "".join(_row(f"a/{i}", stars="") for i in range(3))
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.github_trending_pipeline.fetch_html",
            return_value=f"<html><body>{rows}</body></html>",
        ):
            results = run_github_trending_pipeline(storage, notifier, sources_config=config)
        self.assertTrue(
            any("may have drifted" in w for w in results[0].warnings),
            f"expected a drift warning on the result, got: {results[0].warnings}",
        )

    def test_drift_warning_is_bounded_not_one_line_per_row(self) -> None:
        # Searching the whole page must not turn the §IV channel into per-row spam;
        # drift is a property of the page, so the warning is aggregated.
        source = {**_TRENDING_SOURCE, "limit": 5}
        config = {"version": 1, "sources": [source]}
        html = (
            "<html><body>"
            + "".join(_row(f"b/{i}", description="", stars="") for i in range(20))
            + "</body></html>"
        )
        with self.assertLogs("kinozal_scraper.github_trending_pipeline", level="WARNING") as caplog:
            _run(html=html, sources_config=config)
        drift_lines = [line for line in caplog.output if "may have drifted" in line]
        self.assertEqual(len(drift_lines), 2)  # one per affected field, not per row
        self.assertIn("more", "\n".join(drift_lines))  # evidence collapsed into a count

    def test_partially_blank_column_does_not_claim_drift(self) -> None:
        # Repos without a description are routine on the trending page. Asserting
        # layout drift on them would fire a WARNING nearly every run, and a channel
        # that cries drift daily stops being read (§IV desensitisation).
        source = {**_TRENDING_SOURCE, "limit": 5}
        config = {"version": 1, "sources": [source]}
        rows = _row("a/described") + _row("b/blank", description="")
        with self.assertLogs("kinozal_scraper.github_trending_pipeline", level="INFO") as caplog:
            _run(html=f"<html><body>{rows}</body></html>", sources_config=config)
        joined = "\n".join(caplog.output)
        self.assertNotIn("may have drifted", joined)
        self.assertIn("1 of 2 rows have an empty description", joined)

    def test_mostly_blank_metric_column_still_claims_drift(self) -> None:
        # Unlike `description`, every trending row carries a stargazers link, so a
        # blank `metric` is never routine: requiring a 100%-blank column would let
        # "24 of 25 rows lost their metric" through at INFO.
        source = {**_TRENDING_SOURCE, "limit": 5}
        config = {"version": 1, "sources": [source]}
        rows = _row("a/kept") + _row("b/lost", stars="") + _row("c/lost", stars="")
        with self.assertLogs("kinozal_scraper.github_trending_pipeline", level="WARNING") as caplog:
            _run(html=f"<html><body>{rows}</body></html>", sources_config=config)
        joined = "\n".join(caplog.output)
        self.assertIn("2 of 3 rows have an empty metric", joined)
        self.assertIn("may have drifted", joined)

    def test_partial_extraction_failure_is_visible_but_green(self) -> None:
        # `fetched=2 extracted=1` used to be reachable with an entirely clean
        # result: the per-row extraction errors were dropped on the floor, so the
        # metrics gap the run summary now prints had nothing behind it (§IV).
        source = {**_TRENDING_SOURCE, "limit": 5}
        config = {"version": 1, "sources": [source]}
        broken_row = '<article class="Box-row"><p>no link at all</p></article>'
        storage = InMemoryStorage()
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.github_trending_pipeline.fetch_html",
            return_value=f"<html><body>{_row('a/good')}{broken_row}</body></html>",
        ):
            results = run_github_trending_pipeline(storage, notifier, sources_config=config)

        result = results[0]
        self.assertTrue(result.ok, result.errors)  # one bad row must not kill the source
        self.assertTrue(result.warnings, "partial extraction failure must be surfaced")
        self.assertEqual([n.id for n in notifier.sent], ["a/good"])
        metrics = result.metrics
        assert metrics is not None
        self.assertEqual((metrics.fetched, metrics.extracted), (2, 1))

    def test_metrics_cover_whole_page_not_just_delivered(self) -> None:
        source = {**_TRENDING_SOURCE, "limit": 1}
        config = {"version": 1, "sources": [source]}
        storage = InMemoryStorage()
        storage.seed_existing("github_projects", {"a/1"})
        notifier = InMemoryNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.github_trending_pipeline.fetch_html",
            return_value=_page_html("a/1", "b/2", "b/3"),
        ):
            results = run_github_trending_pipeline(storage, notifier, sources_config=config)
        metrics = results[0].metrics
        assert metrics is not None
        self.assertEqual(
            (metrics.fetched, metrics.extracted, metrics.existing, metrics.new),
            (3, 3, 1, 2),
        )
        self.assertEqual((metrics.sent, metrics.stored), (1, 1))


class TestCanonicalRepoKey(unittest.TestCase):
    """Both GitHub sources write into the shared `github_projects` tab, so one
    repository must produce one key from either side or cross-source dedupe is a
    no-op. The code is already correct (`_normalize_items` strips the leading
    slash); this is the regression guard the invariant never had — it is expected
    to be GREEN at the RED step (characterization guard, cf. #251)."""

    def test_popular_and_trending_agree_on_owner_repo(self) -> None:
        from kinozal_scraper.generic_pipeline import extract_from_json

        popular_source = {
            "id": "github_new_popular",
            "type": "github_popular",
            "limit": 10,
            "dedupe_key": "full_name",
            "fields": {"title": "full_name", "url": "html_url"},
        }
        record = {"full_name": "owner/repo", "html_url": "https://github.com/owner/repo"}
        popular_key = extract_from_json([record], popular_source).items[0].dedupe_key

        trending = extract_from_html(_page_html("owner/repo"), _TRENDING_SOURCE)
        trending_key = _normalize_items(trending.items)[0].dedupe_key

        self.assertEqual(popular_key, trending_key)
        self.assertEqual(trending_key, "owner/repo")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
