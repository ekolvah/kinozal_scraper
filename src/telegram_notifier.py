"""Notifier Protocol: отправка в Telegram + InMemoryNotifier."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

import requests

from generic_pipeline import Notification
from http_fetch import fetch_bytes

logger = logging.getLogger(__name__)

_TG_TEXT_LIMIT = 4096
_TG_CAPTION_LIMIT = 1024
_TRUNCATION_SUFFIX = "\n… (truncated)"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


@runtime_checkable
class Notifier(Protocol):
    def send_items(
        self, notifications: list[Notification]
    ) -> tuple[list[Notification], list[Notification]]: ...


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        inter_message_delay: float = 0.5,
        max_retries: int = 3,
        max_retry_sleep: float = 60.0,
        http_timeout: float = 30.0,
        session: requests.Session | None = None,
        image_fetcher: Callable[[str], bytes] = fetch_bytes,
    ) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._photo_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        self._chat_id = chat_id
        self._inter_message_delay = inter_message_delay
        self._max_retries = max_retries
        self._max_retry_sleep = max_retry_sleep
        self._http_timeout = http_timeout
        self._session = session or requests.Session()
        self._image_fetcher = image_fetcher

    def send_text(self, text: str) -> bool:
        return self._send_one(text)

    def send_items(
        self, notifications: list[Notification]
    ) -> tuple[list[Notification], list[Notification]]:
        sent: list[Notification] = []
        failed: list[Notification] = []
        for i, notif in enumerate(notifications):
            if self._send_one(notif.text, notif.image_url, notif.id):
                sent.append(notif)
            else:
                failed.append(notif)
            if i < len(notifications) - 1:
                time.sleep(self._inter_message_delay)
        return sent, failed

    def _send_one(self, text: str, image_url: str = "", notif_id: str = "") -> bool:
        # Caption limit (1024) is much tighter than text limit (4096); if the
        # message wouldn't fit as a caption, skip sendPhoto entirely.
        use_caption = bool(image_url) and len(text) <= _TG_CAPTION_LIMIT
        message_text = _truncate(text, _TG_TEXT_LIMIT)

        # Download the poster ONCE, before the retry loop (#225): sendPhoto-by-URL
        # is fetched by Telegram's own servers, which Cloudflare-fronted hosts 403
        # — so we fetch the bytes our side (curl_cffi, like #217) and upload them
        # as a multipart file. Fetching inside the loop would re-download on every
        # 429/5xx retry. A failed download degrades to text WITH a visible WARNING
        # (§IV: the dropped poster reaches the operator as a marker, not silently).
        image_bytes: bytes | None = None
        if use_caption:
            try:
                image_bytes = self._image_fetcher(image_url)
            except Exception as exc:  # noqa: BLE001 — any fetch failure degrades to text, not crash
                logger.warning(
                    "[telegram] poster dropped (image fetch failed) for %s: %s: %s",
                    notif_id,
                    image_url,
                    exc,
                )
                use_caption = False

        for _ in range(self._max_retries):
            try:
                if image_bytes is not None:  # ⟺ use_caption stayed True (poster downloaded)
                    # `bytes` (not a one-shot stream): requests re-encodes the body
                    # on each POST, so the same poster survives a 429 retry (#225).
                    resp = self._session.post(
                        self._photo_url,
                        data={
                            "chat_id": self._chat_id,
                            "caption": text,
                            "parse_mode": "HTML",
                        },
                        files={"photo": ("poster.jpg", image_bytes)},
                        timeout=self._http_timeout,
                    )
                    if resp.status_code == 400:
                        # fallback: broken image or caption issue → plain text.
                        logger.warning(
                            "[telegram] poster dropped (sendPhoto 400) for %s: %s",
                            notif_id,
                            image_url,
                        )
                        resp = self._session.post(
                            self._url,
                            json={
                                "chat_id": self._chat_id,
                                "text": message_text,
                                "parse_mode": "HTML",
                            },
                            timeout=self._http_timeout,
                        )
                else:
                    resp = self._session.post(
                        self._url,
                        json={
                            "chat_id": self._chat_id,
                            "text": message_text,
                            "parse_mode": "HTML",
                        },
                        timeout=self._http_timeout,
                    )
            except requests.RequestException:
                return False

            if resp.status_code == 200:
                return True
            if resp.status_code == 429:
                retry_after: float = 30
                with contextlib.suppress(Exception):
                    retry_after = resp.json().get("parameters", {}).get("retry_after") or int(
                        resp.headers.get("Retry-After", 30)
                    )
                if retry_after > self._max_retry_sleep:
                    return False
                time.sleep(retry_after)
                continue
            if resp.status_code == 400:
                return False
            # 5xx и прочие неожиданные статусы: ретрай с паузой
            time.sleep(1)
        return False


class InMemoryNotifier:
    """Test double. Контролируемые сбои через fail_ids."""

    def __init__(self, fail_ids: set[str] | None = None) -> None:
        self.sent: list[Notification] = []
        self.failed: list[Notification] = []
        self._fail_ids: set[str] = fail_ids or set()

    def send_items(
        self, notifications: list[Notification]
    ) -> tuple[list[Notification], list[Notification]]:
        for notif in notifications:
            if notif.id in self._fail_ids:
                self.failed.append(notif)
            else:
                self.sent.append(notif)
        return list(self.sent), list(self.failed)
