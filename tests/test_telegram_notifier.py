import unittest
from typing import Any
from unittest.mock import MagicMock, patch

import requests

from generic_pipeline import NormalizedItem, Notification, _format_field, build_notification
from telegram_notifier import InMemoryNotifier, Notifier, TelegramNotifier


def _item(
    dedupe_key: str = "k1",
    title: str = "Title",
    url: str = "https://example.com",
    description: str = "",
    metric: str = "42",
) -> NormalizedItem:
    return NormalizedItem(
        dedupe_key=dedupe_key,
        title=title,
        source_id="src",
        url=url,
        description=description,
        metric=metric,
    )


def _make_session(*responses: tuple) -> MagicMock:
    """responses: (status_code, json_body, headers_dict) per request."""
    session = MagicMock()
    mocks = []
    for status_code, body, headers in responses:
        r = MagicMock()
        r.status_code = status_code
        r.json.return_value = body
        r.headers = headers
        mocks.append(r)
    session.post.side_effect = mocks
    return session


def _notifier(session: MagicMock, **kwargs: Any) -> TelegramNotifier:
    return TelegramNotifier("token", "chat123", session=session, inter_message_delay=0, **kwargs)


class TestFormatField(unittest.TestCase):
    def test_text_field_escapes_html(self) -> None:
        result = _format_field("title", "<script>alert(1)</script>")
        self.assertEqual(result, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_text_field_does_not_escape_quotes(self) -> None:
        result = _format_field("title", 'say "hello"')
        self.assertEqual(result, 'say "hello"')

    def test_url_field_valid_returned_unchanged(self) -> None:
        url = "https://example.com/path?a=1&b=2"
        self.assertEqual(_format_field("url", url), url)

    def test_url_field_invalid_escaped_not_dropped(self) -> None:
        result = _format_field("url", "www.example.com")
        self.assertEqual(result, "www.example.com")
        self.assertNotEqual(result, "")

    def test_metric_field_no_escaping(self) -> None:
        self.assertEqual(_format_field("metric", "8.5"), "8.5")

    def test_none_value_returns_empty_string(self) -> None:
        self.assertEqual(_format_field("title", None), "")
        self.assertEqual(_format_field("url", None), "")
        self.assertEqual(_format_field("metric", None), "")


class TestBuildNotification(unittest.TestCase):
    def test_template_substituted_correctly(self) -> None:
        item = _item(title="Test Film", url="https://x.com", metric="99")
        notif = build_notification(item, "<b>{title}</b>\nScore: {metric}\n{url}")
        self.assertEqual(notif.id, item.dedupe_key)
        self.assertIn("<b>Test Film</b>", notif.text)
        self.assertIn("Score: 99", notif.text)
        self.assertIn("https://x.com", notif.text)

    def test_html_chars_in_title_escaped(self) -> None:
        item = _item(title="Film & <More>")
        notif = build_notification(item, "{title}")
        self.assertIn("Film &amp; &lt;More&gt;", notif.text)

    def test_notification_id_equals_dedupe_key(self) -> None:
        item = _item(dedupe_key="unique-key-123")
        notif = build_notification(item, "{title}")
        self.assertEqual(notif.id, "unique-key-123")


class TestTelegramNotifierSuccess(unittest.TestCase):
    def test_http_200_goes_to_sent(self) -> None:
        session = _make_session((200, {"ok": True}, {}))
        notifier = _notifier(session)
        notif = Notification(id="k1", text="hello")
        sent, failed = notifier.send_items([notif])
        self.assertEqual(sent, [notif])
        self.assertEqual(failed, [])

    def test_http_400_goes_to_failed(self) -> None:
        session = _make_session((400, {"ok": False, "description": "Bad Request"}, {}))
        notifier = _notifier(session)
        notif = Notification(id="k1", text="hello")
        sent, failed = notifier.send_items([notif])
        self.assertEqual(sent, [])
        self.assertEqual(failed, [notif])

    def test_connection_error_goes_to_failed(self) -> None:
        session = MagicMock()
        session.post.side_effect = requests.ConnectionError("timeout")
        notifier = _notifier(session)
        notif = Notification(id="k1", text="hello")
        sent, failed = notifier.send_items([notif])
        self.assertEqual(sent, [])
        self.assertEqual(failed, [notif])

    def test_mixed_results_partitioned_correctly(self) -> None:
        session = _make_session(
            (200, {"ok": True}, {}),
            (400, {"ok": False}, {}),
            (200, {"ok": True}, {}),
        )
        notifier = _notifier(session)
        n1 = Notification(id="a", text="first")
        n2 = Notification(id="b", text="second")
        n3 = Notification(id="c", text="third")
        sent, failed = notifier.send_items([n1, n2, n3])
        self.assertEqual(sent, [n1, n3])
        self.assertEqual(failed, [n2])


class TestTelegramNotifierRetry(unittest.TestCase):
    @patch("telegram_notifier.time.sleep")
    def test_429_with_json_retry_after_retries_and_succeeds(self, mock_sleep: MagicMock) -> None:
        session = _make_session(
            (429, {"parameters": {"retry_after": 5}}, {}),
            (200, {"ok": True}, {}),
        )
        notifier = _notifier(session)
        notif = Notification(id="k1", text="hello")
        sent, failed = notifier.send_items([notif])
        self.assertEqual(sent, [notif])
        self.assertEqual(failed, [])
        mock_sleep.assert_any_call(5)

    @patch("telegram_notifier.time.sleep")
    def test_429_with_retry_after_header_retries_and_succeeds(self, mock_sleep: MagicMock) -> None:
        session = _make_session(
            (429, {}, {"Retry-After": "10"}),
            (200, {"ok": True}, {}),
        )
        notifier = _notifier(session)
        notif = Notification(id="k1", text="hello")
        sent, failed = notifier.send_items([notif])
        self.assertEqual(sent, [notif])
        self.assertEqual(failed, [])
        mock_sleep.assert_any_call(10)

    @patch("telegram_notifier.time.sleep")
    def test_429_retry_after_exceeds_max_retry_sleep_returns_failed(
        self, mock_sleep: MagicMock
    ) -> None:
        session = _make_session((429, {"parameters": {"retry_after": 3600}}, {}))
        notifier = _notifier(session, max_retry_sleep=60.0)
        notif = Notification(id="k1", text="hello")
        sent, failed = notifier.send_items([notif])
        self.assertEqual(sent, [])
        self.assertEqual(failed, [notif])
        mock_sleep.assert_not_called()


class TestTelegramNotifierImageFallback(unittest.TestCase):
    def test_photo_400_falls_back_to_text_send(self) -> None:
        """Broken image URL: sendPhoto → 400 must fall back to sendMessage and succeed."""
        session = _make_session(
            (400, {"ok": False, "description": "wrong file identifier"}, {}),
            (200, {"ok": True}, {}),
        )
        notifier = _notifier(session)
        notif = Notification(id="k1", text="caption", image_url="https://broken.example/404.jpg")
        sent, failed = notifier.send_items([notif])
        self.assertEqual(sent, [notif])
        self.assertEqual(failed, [])
        self.assertEqual(session.post.call_count, 2)
        first_url = session.post.call_args_list[0].args[0]
        second_url = session.post.call_args_list[1].args[0]
        self.assertIn("sendPhoto", first_url)
        self.assertIn("sendMessage", second_url)


class TestTelegramNotifierKnownBugs(unittest.TestCase):
    """Tests that document current (buggy) behaviour. Linked to follow-up issues.

    These tests will need to be updated when the underlying bugs are fixed; until
    then they pin the contract so we notice regressions in the wrong direction.
    """

    def test_message_over_4096_chars_lost_as_failed(self) -> None:
        """Telegram rejects text >4096 chars with 400; we currently drop the notification.

        Expected future fix: split the message or truncate. Until then, the
        notification ends up in `failed` and is never delivered.
        """
        long_text = "x" * 5000
        session = _make_session(
            (400, {"ok": False, "description": "message is too long"}, {}),
        )
        notifier = _notifier(session)
        notif = Notification(id="k1", text=long_text)
        sent, failed = notifier.send_items([notif])
        self.assertEqual(sent, [])
        self.assertEqual(failed, [notif])

    def test_session_post_called_with_explicit_timeout(self) -> None:
        """session.post is invoked with `timeout=30` to bound a hung Telegram API."""
        session = _make_session((200, {"ok": True}, {}))
        notifier = _notifier(session)
        notifier.send_items([Notification(id="k1", text="hello")])
        self.assertEqual(session.post.call_count, 1)
        kwargs = session.post.call_args.kwargs
        self.assertEqual(kwargs.get("timeout"), 30.0)

    def test_requests_timeout_routes_to_failed(self) -> None:
        """If the HTTP layer raises Timeout, the notification is marked failed.

        Combined with `test_session_post_called_with_explicit_timeout` above:
        we now bound the call with `timeout=30`, but a raised Timeout still
        drops the notification with no retry (any `RequestException` returns
        False from `_send_one`).
        """
        session = MagicMock()
        session.post.side_effect = requests.Timeout("read timeout")
        notifier = _notifier(session)
        notif = Notification(id="k1", text="hello")
        sent, failed = notifier.send_items([notif])
        self.assertEqual(sent, [])
        self.assertEqual(failed, [notif])


class TestInMemoryNotifier(unittest.TestCase):
    def test_implements_notifier_protocol(self) -> None:
        self.assertIsInstance(InMemoryNotifier(), Notifier)

    def test_all_sent_when_no_fail_ids(self) -> None:
        notifier = InMemoryNotifier()
        notifs = [Notification(id="a", text=""), Notification(id="b", text="")]
        sent, failed = notifier.send_items(notifs)
        self.assertEqual(sent, notifs)
        self.assertEqual(failed, [])

    def test_fail_ids_routes_to_failed(self) -> None:
        notifier = InMemoryNotifier(fail_ids={"b"})
        n1 = Notification(id="a", text="")
        n2 = Notification(id="b", text="")
        sent, failed = notifier.send_items([n1, n2])
        self.assertEqual(sent, [n1])
        self.assertEqual(failed, [n2])


if __name__ == "__main__":
    unittest.main()
