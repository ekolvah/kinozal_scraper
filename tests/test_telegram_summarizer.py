from __future__ import annotations

import unittest
import unittest.mock
from typing import Any

import google.api_core.exceptions

from kinozal_scraper.telegram_summarizer import (
    deliver_results,
    format_summary_message,
    format_technical_alert,
    send_required_text,
)
from kinozal_scraper.TelegramChannelSummarizer import (
    ChannelMessages,
    ChannelProcessResult,
    ChannelSummary,
    GeminiSummarizer,
    SummarizationFailed,
    Summarizer,
    TelegramReader,
    TelethonReader,
    summarize_channel_results,
    summarize_channels,
)

# ── Test doubles for summarize_channels (Protocol surface) ───────────────────


class _FakeReader:
    def __init__(self, mapping: dict[str, ChannelMessages]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def fetch_channel(self, channel_url: str) -> ChannelMessages:
        self.calls.append(channel_url)
        return self._mapping[channel_url]


class _FakeSummarizer:
    def __init__(self, returns: str = "summary-text") -> None:
        self._returns = returns
        self.calls: list[tuple[str, bool]] = []

    def summarize(self, text: str, is_broadcast: bool) -> str:
        self.calls.append((text, is_broadcast))
        return self._returns


class _FailingSummarizer:
    def summarize(self, text: str, is_broadcast: bool) -> str:
        raise SummarizationFailed("api_error", "model down")


# ── H. Pipeline orchestration ────────────────────────────────────────────────


class TestSummarizeChannelsOrchestration(unittest.TestCase):
    def test_implements_protocols(self) -> None:
        # Anchor that the fakes are actually structural-typed against the
        # Protocols — if anyone tightens the Protocol surface this catches it.
        self.assertIsInstance(_FakeReader({}), TelegramReader)
        self.assertIsInstance(_FakeSummarizer(), Summarizer)

    def test_one_channel_error_does_not_block_others(self) -> None:
        reader = _FakeReader(
            {
                "https://t.me/good_a": ("Канал A", "msg a", True),
                "https://t.me/broken": (None, "", False),  # reader-error tuple
                "https://t.me/good_b": ("Канал B", "msg b", False),
            }
        )
        summarizer = _FakeSummarizer(returns="ok")
        results = summarize_channels(
            reader,
            summarizer,
            ["https://t.me/good_a", "https://t.me/broken", "https://t.me/good_b"],
        )

        self.assertEqual(len(results), 2)
        self.assertEqual([r.channel for r in results], ["Канал A", "Канал B"])
        # Summarizer was NOT called for the broken channel.
        self.assertEqual(len(summarizer.calls), 2)

    def test_empty_text_skips_summarizer(self) -> None:
        reader = _FakeReader({"u": ("Канал X", "", False)})
        summarizer = _FakeSummarizer()
        results = summarize_channels(reader, summarizer, ["u"])

        self.assertEqual(results, [])
        self.assertEqual(summarizer.calls, [])

    def test_empty_summary_not_added_to_results(self) -> None:
        reader = _FakeReader({"u": ("Канал X", "real text", False)})
        summarizer = _FakeSummarizer(returns="")  # model produced nothing
        results = summarize_channels(reader, summarizer, ["u"])

        self.assertEqual(results, [])
        # Summarizer WAS called, but result was dropped because empty.
        self.assertEqual(len(summarizer.calls), 1)

    def test_no_channel_title_falls_back_to_url_as_display_name(self) -> None:
        reader = _FakeReader({"u": (None, "text", False)})
        # title=None but text non-empty is an unusual but legal shape — display
        # name should fall back to the url string.
        summarizer = _FakeSummarizer(returns="sum")
        results = summarize_channels(reader, summarizer, ["u"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].channel, "u")

    def test_results_preserve_summarization_failure_state(self) -> None:
        reader = _FakeReader({"u": ("Channel", "real text", False)})
        results = summarize_channel_results(reader, _FailingSummarizer(), ["u"])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "summarization_failed")
        self.assertEqual(results[0].error_kind, "api_error")
        self.assertGreater(results[0].message_count, 0)

    def test_empty_summary_is_failure_not_no_text(self) -> None:
        reader = _FakeReader({"u": ("Channel", "real text", False)})
        results = summarize_channel_results(reader, _FakeSummarizer(returns=""), ["u"])

        self.assertEqual(results[0].status, "summarization_failed")
        self.assertEqual(results[0].error_kind, "empty_summary")


# ── C. Auth & quota — GeminiSummarizer ──────────────────────────────────────


class _FakeResponse:
    def __init__(self, text: str, has_candidates: bool = True) -> None:
        self.text = text
        self.candidates = [object()] if has_candidates else []


class TestGeminiSummarizerQuota(unittest.TestCase):
    def test_empty_text_short_circuits(self) -> None:
        summ = GeminiSummarizer(models=["m1"], broadcast_prompt="b", chat_prompt="c")
        with unittest.mock.patch(
            "kinozal_scraper.TelegramChannelSummarizer.genai.GenerativeModel"
        ) as mock_model:
            result = summ.summarize("", False)
        self.assertEqual(result, "")
        mock_model.assert_not_called()

    def test_first_model_quota_falls_back_to_next(self) -> None:
        summ = GeminiSummarizer(models=["m-a", "m-b"], broadcast_prompt="b", chat_prompt="c")

        # First model raises ResourceExhausted, second returns a response.
        def factory(name: str) -> Any:
            instance = unittest.mock.MagicMock()
            if name == "m-a":
                instance.generate_content.side_effect = (
                    google.api_core.exceptions.ResourceExhausted("quota")
                )
            else:
                instance.generate_content.return_value = _FakeResponse("from-b")
            return instance

        with unittest.mock.patch(
            "kinozal_scraper.TelegramChannelSummarizer.genai.GenerativeModel", side_effect=factory
        ):
            result = summ.summarize("text", is_broadcast=False)
        self.assertEqual(result, "from-b")

    def test_first_model_not_found_falls_back_to_next(self) -> None:
        summ = GeminiSummarizer(models=["m-a", "m-b"], broadcast_prompt="b", chat_prompt="c")

        def factory(name: str) -> Any:
            instance = unittest.mock.MagicMock()
            if name == "m-a":
                instance.generate_content.side_effect = google.api_core.exceptions.NotFound(
                    "404 model no longer available"
                )
            else:
                instance.generate_content.return_value = _FakeResponse("from-b")
            return instance

        with unittest.mock.patch(
            "kinozal_scraper.TelegramChannelSummarizer.genai.GenerativeModel", side_effect=factory
        ):
            result = summ.summarize("text", is_broadcast=False)
        self.assertEqual(result, "from-b")

    def test_all_models_exhausted_raises_failure(self) -> None:
        summ = GeminiSummarizer(models=["m1", "m2"], broadcast_prompt="b", chat_prompt="c")
        with unittest.mock.patch(
            "kinozal_scraper.TelegramChannelSummarizer.genai.GenerativeModel"
        ) as mock_model:
            mock_model.return_value.generate_content.side_effect = (
                google.api_core.exceptions.ResourceExhausted("quota")
            )
            with self.assertRaises(SummarizationFailed) as ctx:
                summ.summarize("text", is_broadcast=True)
        self.assertEqual(ctx.exception.error_kind, "all_models_failed")
        # Both models were tried.
        self.assertEqual(mock_model.call_count, 2)

    def test_non_quota_exception_raises_without_fallback(self) -> None:
        """Behaviour pinned from TelegramChannelSummarizer.py:86-87 — any
        non-`ResourceExhausted` exception aborts the loop. We don't try the
        next model on a generic failure (different from `ResourceExhausted`
        which is per-model)."""
        summ = GeminiSummarizer(models=["m1", "m2"], broadcast_prompt="b", chat_prompt="c")
        with unittest.mock.patch(
            "kinozal_scraper.TelegramChannelSummarizer.genai.GenerativeModel"
        ) as mock_model:
            mock_model.return_value.generate_content.side_effect = RuntimeError("net down")
            with self.assertRaises(SummarizationFailed) as ctx:
                summ.summarize("text", False)
        self.assertEqual(ctx.exception.error_kind, "api_error")
        # Only the first model was tried.
        self.assertEqual(mock_model.call_count, 1)

    def test_no_candidates_raises_failure(self) -> None:
        summ = GeminiSummarizer(models=["m1"], broadcast_prompt="b", chat_prompt="c")
        with unittest.mock.patch(
            "kinozal_scraper.TelegramChannelSummarizer.genai.GenerativeModel"
        ) as mock_model:
            mock_model.return_value.generate_content.return_value = _FakeResponse(
                "", has_candidates=False
            )
            with self.assertRaises(SummarizationFailed) as ctx:
                summ.summarize("text", False)
        self.assertEqual(ctx.exception.error_kind, "empty_response")

    def test_broadcast_uses_broadcast_prompt(self) -> None:
        summ = GeminiSummarizer(models=["m1"], broadcast_prompt="BROADCAST", chat_prompt="CHAT")
        with unittest.mock.patch(
            "kinozal_scraper.TelegramChannelSummarizer.genai.GenerativeModel"
        ) as mock_model:
            mock_model.return_value.generate_content.return_value = _FakeResponse("ok")
            summ.summarize("payload", is_broadcast=True)
        called_request = mock_model.return_value.generate_content.call_args.args[0]
        self.assertIn("BROADCAST", called_request)
        self.assertNotIn("CHAT", called_request)

    def test_chat_uses_chat_prompt(self) -> None:
        summ = GeminiSummarizer(models=["m1"], broadcast_prompt="BROADCAST", chat_prompt="CHAT")
        with unittest.mock.patch(
            "kinozal_scraper.TelegramChannelSummarizer.genai.GenerativeModel"
        ) as mock_model:
            mock_model.return_value.generate_content.return_value = _FakeResponse("ok")
            summ.summarize("payload", is_broadcast=False)
        called_request = mock_model.return_value.generate_content.call_args.args[0]
        self.assertIn("CHAT", called_request)
        self.assertNotIn("BROADCAST", called_request)


# ── C. Auth & quota — TelethonReader error swallow ──────────────────────────


class TestTelethonReaderErrorSwallow(unittest.TestCase):
    def test_fetch_channel_swallows_exception_returns_error_tuple(self) -> None:
        """If anything goes wrong inside `_fetch_channel_async`'s
        try-block (session expired, network down, channel not found), the
        reader MUST return `(None, "", False)` so `summarize_channels`
        keeps processing the rest of the channels. Losing this swallow
        would let one bad channel kill the whole cron.

        Caveat (preserved from pre-refactor): `TelegramClient(...)` and
        `StringSession(...)` are constructed OUTSIDE the try-block, so a
        malformed session-string raises before the swallow fires. That's
        intentional (PR1 was byte-identical). Tests of the swallow path
        use `session=None` (the `'anon'` filename branch) and inject a
        mock client whose `start()` raises.
        """
        reader = TelethonReader(api_id="x", api_hash="y", session=None, phone="p")
        mock_client = unittest.mock.MagicMock()
        mock_client.start = unittest.mock.AsyncMock(side_effect=RuntimeError("session expired"))
        mock_client.disconnect = unittest.mock.AsyncMock()

        with unittest.mock.patch(
            "kinozal_scraper.TelegramChannelSummarizer.TelegramClient",
            return_value=mock_client,
        ):
            result = reader.fetch_channel("https://t.me/whatever")

        self.assertEqual(result, (None, "", False))
        mock_client.disconnect.assert_awaited_once()


# ── F. Message rendering — format_summary_message ───────────────────────────


class TestFormatSummaryMessage(unittest.TestCase):
    def test_http_url_wraps_channel_in_anchor(self) -> None:
        s = ChannelSummary(channel="Канал X", url="https://t.me/x", summary="темы дня")
        text = format_summary_message(s)
        self.assertIn('<a href="https://t.me/x">', text)
        self.assertIn("Канал X</a>", text)
        self.assertIn("📢 Канал:", text)
        self.assertIn("темы дня", text)

    def test_https_url_also_anchored(self) -> None:
        s = ChannelSummary(channel="Z", url="http://example.com/c", summary="sum")
        self.assertIn('<a href="http://example.com/c">', format_summary_message(s))

    def test_non_http_url_renders_plain_text(self) -> None:
        # When the input was something like "-1001537004903" — no anchor.
        s = ChannelSummary(channel="Канал Z", url="-1001537004903", summary="sum")
        text = format_summary_message(s)
        self.assertNotIn("<a href", text)
        self.assertIn("Канал Z", text)

    def test_empty_url_renders_plain_text(self) -> None:
        s = ChannelSummary(channel="C", url="", summary="sum")
        text = format_summary_message(s)
        self.assertNotIn("<a href", text)

    def test_html_special_chars_in_summary_escaped(self) -> None:
        s = ChannelSummary(channel="C", url="", summary="<script>alert(1)</script>")
        text = format_summary_message(s)
        # Raw <script> would let Telegram render it as HTML — escape it.
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)

    def test_html_special_chars_in_channel_name_escaped(self) -> None:
        s = ChannelSummary(channel="A & B <3", url="", summary="sum")
        text = format_summary_message(s)
        self.assertNotIn("A & B <3", text)
        self.assertIn("A &amp; B &lt;3", text)

    def test_html_chars_escaped_even_with_http_url(self) -> None:
        # URL goes inside href untouched (it's a URL — http-only path);
        # channel name still escaped.
        s = ChannelSummary(channel="A & B", url="https://t.me/c", summary="t")
        text = format_summary_message(s)
        self.assertIn("A &amp; B</a>", text)


class TestTechnicalAlert(unittest.TestCase):
    def test_format_technical_alert_includes_failed_channel(self) -> None:
        text = format_technical_alert(
            [
                ChannelProcessResult(
                    channel="A & B",
                    url="u",
                    status="summarization_failed",
                    message_count=1,
                    text_chars=4,
                    error_kind="api_error",
                    error_message="down",
                )
            ]
        )
        self.assertIn("A &amp; B", text)
        self.assertIn("api_error", text)

    def test_send_required_text_returns_false_on_delivery_failure(self) -> None:
        notifier = unittest.mock.MagicMock()
        notifier.send_text.return_value = False
        self.assertFalse(send_required_text(notifier, "x"))


class _RecordingNotifier:
    def __init__(self, fail_all: bool = False) -> None:
        self.sent: list[str] = []
        self._fail_all = fail_all

    def send_text(self, text: str) -> bool:
        if self._fail_all:
            return False
        self.sent.append(text)
        return True


def _summarized(channel: str) -> ChannelProcessResult:
    return ChannelProcessResult(
        channel=channel, url=f"http://{channel}", status="summarized", summary=f"sum-{channel}"
    )


def _failed(channel: str) -> ChannelProcessResult:
    return ChannelProcessResult(
        channel=channel, url=f"http://{channel}", status="summarization_failed", error_kind="api"
    )


def _no_text(channel: str) -> ChannelProcessResult:
    return ChannelProcessResult(channel=channel, url=f"http://{channel}", status="no_text")


class TestDeliverResults(unittest.TestCase):
    def test_partial_failure_still_delivers_summaries_then_alerts(self) -> None:
        """Product decision: a single failing channel must not discard the
        summaries of the channels that succeeded. Summaries are sent first,
        the technical alert last, and the run exits non-zero."""
        notifier = _RecordingNotifier()
        results = [_summarized("a"), _summarized("b"), _failed("c")]

        with unittest.mock.patch("kinozal_scraper.telegram_summarizer.mark_technical_alert_sent"):
            code = deliver_results(notifier, results)

        self.assertEqual(code, 1)
        joined = "\n".join(notifier.sent)
        self.assertIn("sum-a", joined)
        self.assertIn("sum-b", joined)
        alert_idx = next(i for i, t in enumerate(notifier.sent) if t.startswith("⚠️"))
        last_summary_idx = max(i for i, t in enumerate(notifier.sent) if "sum-" in t)
        self.assertLess(last_summary_idx, alert_idx, "alert must come after all summaries")

    def test_all_summarized_no_alert_exit_zero(self) -> None:
        notifier = _RecordingNotifier()
        code = deliver_results(notifier, [_summarized("a"), _summarized("b")])
        self.assertEqual(code, 0)
        self.assertFalse(any(t.startswith("⚠️") for t in notifier.sent))

    def test_all_failed_sends_alert_no_no_news(self) -> None:
        notifier = _RecordingNotifier()
        with unittest.mock.patch("kinozal_scraper.telegram_summarizer.mark_technical_alert_sent"):
            code = deliver_results(notifier, [_failed("a"), _failed("b")])
        self.assertEqual(code, 1)
        self.assertTrue(any(t.startswith("⚠️") for t in notifier.sent))
        self.assertFalse(any("не было новых сообщений" in t for t in notifier.sent))

    def test_no_summaries_no_failures_sends_no_news(self) -> None:
        notifier = _RecordingNotifier()
        code = deliver_results(notifier, [_no_text("a")])
        self.assertEqual(code, 0)
        self.assertTrue(any("не было новых сообщений" in t for t in notifier.sent))

    def test_marker_write_failure_does_not_double_raise(self) -> None:
        """Item 6: if the marker write throws after the alert was sent, the
        exception is swallowed (logged) so the step still exits 1 cleanly
        instead of crashing and double-firing the workflow fallback alert."""
        notifier = _RecordingNotifier()
        with unittest.mock.patch(
            "kinozal_scraper.telegram_summarizer.mark_technical_alert_sent",
            side_effect=OSError("no .run/"),
        ):
            code = deliver_results(notifier, [_failed("a")])
        self.assertEqual(code, 1)
        self.assertTrue(any(t.startswith("⚠️") for t in notifier.sent))

    def test_summary_delivery_failure_exits_nonzero(self) -> None:
        notifier = _RecordingNotifier(fail_all=True)
        code = deliver_results(notifier, [_summarized("a")])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
