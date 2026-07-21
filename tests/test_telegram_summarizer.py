from __future__ import annotations

import unittest
import unittest.mock
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from google.genai import errors

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
    def __init__(self, text: str, has_candidates: bool = True, usage_metadata: Any = None) -> None:
        self.text = text
        self.candidates = [object()] if has_candidates else []
        self.usage_metadata = usage_metadata


def _api_error(code: int) -> errors.ClientError:
    """google.genai APIError double carrying only `.code` (the taxonomy field)."""
    e = errors.ClientError.__new__(errors.ClientError)
    e.code = code
    e.status = "RESOURCE_EXHAUSTED" if code == 429 else "NOT_FOUND"
    e.message = str(code)
    return e


class _FakeModels:
    """Stand-in for `client.models`: per-model canned response/error. `outcomes`
    maps a model name (or "*" default) to a `_FakeResponse` or an `Exception`."""

    def __init__(self, outcomes: dict[str, Any]) -> None:
        self._outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: Any, config: Any = None) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self._outcomes.get(model, self._outcomes.get("*"))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, outcomes: dict[str, Any] | None = None) -> None:
        self.models = _FakeModels(outcomes or {})


class TestGeminiSummarizerQuota(unittest.TestCase):
    def test_empty_text_short_circuits(self) -> None:
        client = _FakeClient()
        summ = GeminiSummarizer(models=["m1"], client=client, broadcast_prompt="b", chat_prompt="c")
        result = summ.summarize("", False)
        self.assertEqual(result, "")
        self.assertEqual(client.models.calls, [])

    def test_quota_advances_to_next_model(self) -> None:
        # #107 BLOCKING-1: new-SDK 429 on model N must advance to N+1, not abort.
        client = _FakeClient({"m-a": _api_error(429), "m-b": _FakeResponse("from-b")})
        summ = GeminiSummarizer(
            models=["m-a", "m-b"], client=client, broadcast_prompt="b", chat_prompt="c"
        )
        result = summ.summarize("text", is_broadcast=False)
        self.assertEqual(result, "from-b")

    def test_unavailable_advances_to_next_model(self) -> None:
        # #107 BLOCKING-1: new-SDK 404 on model N must advance to N+1, not abort.
        client = _FakeClient({"m-a": _api_error(404), "m-b": _FakeResponse("from-b")})
        summ = GeminiSummarizer(
            models=["m-a", "m-b"], client=client, broadcast_prompt="b", chat_prompt="c"
        )
        result = summ.summarize("text", is_broadcast=False)
        self.assertEqual(result, "from-b")

    def test_all_models_exhausted_raises_failure(self) -> None:
        client = _FakeClient({"*": _api_error(429)})
        summ = GeminiSummarizer(
            models=["m1", "m2"], client=client, broadcast_prompt="b", chat_prompt="c"
        )
        with self.assertRaises(SummarizationFailed) as ctx:
            summ.summarize("text", is_broadcast=True)
        self.assertEqual(ctx.exception.error_kind, "all_models_failed")
        # Both models were tried.
        self.assertEqual(len(client.models.calls), 2)

    def test_non_quota_exception_raises_without_fallback(self) -> None:
        """Any non-quota / non-unavailable exception aborts the loop — we don't
        try the next model on a generic failure (only 429/404 are per-model)."""
        client = _FakeClient({"*": RuntimeError("net down")})
        summ = GeminiSummarizer(
            models=["m1", "m2"], client=client, broadcast_prompt="b", chat_prompt="c"
        )
        with self.assertRaises(SummarizationFailed) as ctx:
            summ.summarize("text", False)
        self.assertEqual(ctx.exception.error_kind, "api_error")
        # Only the first model was tried.
        self.assertEqual(len(client.models.calls), 1)

    def test_no_candidates_raises_failure(self) -> None:
        client = _FakeClient({"m1": _FakeResponse("", has_candidates=False)})
        summ = GeminiSummarizer(models=["m1"], client=client, broadcast_prompt="b", chat_prompt="c")
        with self.assertRaises(SummarizationFailed) as ctx:
            summ.summarize("text", False)
        self.assertEqual(ctx.exception.error_kind, "empty_response")

    def test_broadcast_uses_broadcast_prompt(self) -> None:
        client = _FakeClient({"m1": _FakeResponse("ok")})
        summ = GeminiSummarizer(
            models=["m1"], client=client, broadcast_prompt="BROADCAST", chat_prompt="CHAT"
        )
        summ.summarize("payload", is_broadcast=True)
        called_request = client.models.calls[-1]["contents"]
        self.assertIn("BROADCAST", called_request)
        self.assertNotIn("CHAT", called_request)

    def test_chat_uses_chat_prompt(self) -> None:
        client = _FakeClient({"m1": _FakeResponse("ok")})
        summ = GeminiSummarizer(
            models=["m1"], client=client, broadcast_prompt="BROADCAST", chat_prompt="CHAT"
        )
        summ.summarize("payload", is_broadcast=False)
        called_request = client.models.calls[-1]["contents"]
        self.assertIn("CHAT", called_request)
        self.assertNotIn("BROADCAST", called_request)


class TestGeminiSummarizerObservability(unittest.TestCase):
    """A live summarization call must emit a structured `llm_call` breadcrumb with
    token usage and latency, mirroring the enricher, so channel-summary token spend
    is visible in cron logs (#145)."""

    def test_summarize_logs_token_usage(self) -> None:
        response = _FakeResponse(
            "summary text",
            usage_metadata=SimpleNamespace(
                prompt_token_count=200, candidates_token_count=30, total_token_count=230
            ),
        )
        client = _FakeClient({"m1": response})
        summ = GeminiSummarizer(models=["m1"], client=client, broadcast_prompt="b", chat_prompt="c")
        with self.assertLogs("kinozal_scraper.TelegramChannelSummarizer", level="INFO") as cm:
            result = summ.summarize("channel payload", is_broadcast=True)
        self.assertEqual(result, "summary text")
        line = "\n".join(cm.output)
        self.assertIn("llm_call", line)
        self.assertIn("total_tokens=230", line)
        self.assertIn("latency_ms=", line)

    def test_summarize_logs_empty_outcome_when_no_candidates(self) -> None:
        # No candidates → the call is logged with outcome=empty before it raises
        # SummarizationFailed, so a blocked/empty response still shows up in cron.
        response = _FakeResponse(
            "",
            has_candidates=False,
            usage_metadata=SimpleNamespace(
                prompt_token_count=5, candidates_token_count=0, total_token_count=5
            ),
        )
        client = _FakeClient({"m1": response})
        summ = GeminiSummarizer(models=["m1"], client=client, broadcast_prompt="b", chat_prompt="c")
        with (
            self.assertLogs("kinozal_scraper.TelegramChannelSummarizer", level="INFO") as cm,
            self.assertRaises(SummarizationFailed),
        ):
            summ.summarize("channel payload", is_broadcast=True)
        self.assertIn("outcome=empty", "\n".join(cm.output))


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


# ── C2. Happy-path rendering — TelethonReader._fetch_channel_async ───────────
#
# Characterization guard for the render path (broadcast / sender-name fallbacks /
# day-cutoff / empty-text) written BEFORE the `_format_messages` split (#294), so
# the extraction is caught if it drifts. Telethon `TelegramClient` is an external
# boundary (§II) — mocked. Fakes are duck-typed data holders.


def _msg(text: str, sender_id: int | None = None, *, recent: bool = True) -> SimpleNamespace:
    when = datetime.now(UTC) - (timedelta(hours=1) if recent else timedelta(days=2))
    return SimpleNamespace(message=text, sender_id=sender_id, date=when)


def _user(uid: int, first: str = "", last: str = "", username: str = "") -> SimpleNamespace:
    return SimpleNamespace(id=uid, first_name=first, last_name=last, username=username)


def _entity(title: str = "Chan", *, broadcast: bool = False) -> SimpleNamespace:
    return SimpleNamespace(title=title, broadcast=broadcast)


def _posts(messages: list[SimpleNamespace], users: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(messages=messages, users=users)


def _mock_client(entity: SimpleNamespace, posts: SimpleNamespace) -> unittest.mock.MagicMock:
    client = unittest.mock.MagicMock()
    client.start = unittest.mock.AsyncMock()
    client.is_user_authorized = unittest.mock.AsyncMock(return_value=True)
    client.get_entity = unittest.mock.AsyncMock(return_value=entity)
    client.disconnect = unittest.mock.AsyncMock()

    async def _call(_request: Any) -> SimpleNamespace:  # await client(GetHistoryRequest(...))
        return posts

    client.side_effect = _call
    return client


class TestTelethonReaderFetchRender(unittest.TestCase):
    def _fetch(
        self, url: str, entity: SimpleNamespace, posts: SimpleNamespace
    ) -> tuple[ChannelMessages, unittest.mock.MagicMock]:
        reader = TelethonReader(api_id="x", api_hash="y", session=None, phone="p")
        client = _mock_client(entity, posts)
        with (
            unittest.mock.patch(
                "kinozal_scraper.TelegramChannelSummarizer.TelegramClient", return_value=client
            ),
            unittest.mock.patch("kinozal_scraper.TelegramChannelSummarizer.GetHistoryRequest"),
        ):
            result = reader.fetch_channel(url)
        return result, client

    def test_broadcast_channel_returns_raw_messages(self) -> None:
        # broadcast → raw text, no `sender:` prefix; reverse order; empty `.message`
        # dropped BEFORE the broadcast-append (order of the two early branches, B1).
        posts = _posts([_msg("first"), _msg(""), _msg("second")], users=[])
        result, _ = self._fetch("https://t.me/c", _entity(broadcast=True), posts)
        self.assertEqual(result, ("Chan", "second\nfirst", True))

    def test_non_broadcast_resolves_sender_full_name(self) -> None:
        posts = _posts([_msg("hi", sender_id=5)], [_user(5, first="John", last="Doe")])
        result, client = self._fetch("https://t.me/c", _entity(), posts)
        self.assertEqual(result, ("Chan", "John Doe: hi", False))
        client.disconnect.assert_awaited_once()  # `finally: disconnect()` parity

    def test_non_broadcast_sender_falls_back_to_username(self) -> None:
        posts = _posts([_msg("hi", sender_id=6)], [_user(6, username="jdoe")])
        result, _ = self._fetch("https://t.me/c", _entity(), posts)
        self.assertEqual(result, ("Chan", "jdoe: hi", False))

    def test_non_broadcast_sender_falls_back_to_id(self) -> None:
        posts = _posts([_msg("hi", sender_id=7)], [_user(7)])
        result, _ = self._fetch("https://t.me/c", _entity(), posts)
        self.assertEqual(result, ("Chan", "7: hi", False))

    def test_non_broadcast_unknown_sender_no_sender_id(self) -> None:
        # `if message.sender_id:` false (S1 branch A)
        posts = _posts([_msg("hi", sender_id=None)], [])
        result, _ = self._fetch("https://t.me/c", _entity(), posts)
        self.assertEqual(result, ("Chan", "Unknown: hi", False))

    def test_non_broadcast_sender_not_in_users(self) -> None:
        # `sender_id` truthy but `users.get()` → None → `if sender:` false (S1 branch B)
        posts = _posts([_msg("hi", sender_id=99)], [_user(1, first="Other")])
        result, _ = self._fetch("https://t.me/c", _entity(), posts)
        self.assertEqual(result, ("Chan", "Unknown: hi", False))

    def test_day_cutoff_filters_old_messages(self) -> None:
        posts = _posts([_msg("old", recent=False), _msg("new")], users=[])
        result, _ = self._fetch("https://t.me/c", _entity(broadcast=True), posts)
        self.assertEqual(result, ("Chan", "new", True))

    def test_empty_text_messages_returns_empty_tuple(self) -> None:
        # all messages have empty `.message` (NOT an empty list) → the
        # `if not message.message: continue` branch drops every one (B1).
        posts = _posts([_msg(""), _msg("")], users=[])
        result, _ = self._fetch("https://t.me/c", _entity(broadcast=True), posts)
        self.assertEqual(result, ("Chan", "", True))

    def test_numeric_channel_url_coerced_to_int(self) -> None:
        posts = _posts([_msg("x")], users=[])
        _, client = self._fetch("-100123", _entity(broadcast=True), posts)
        client.get_entity.assert_awaited_once_with(-100123)


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
