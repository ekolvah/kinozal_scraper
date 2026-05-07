from __future__ import annotations

import contextlib
import time
from typing import Protocol, runtime_checkable

import requests

from generic_pipeline import Notification


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
        session: requests.Session | None = None,
    ) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._photo_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        self._chat_id = chat_id
        self._inter_message_delay = inter_message_delay
        self._max_retries = max_retries
        self._max_retry_sleep = max_retry_sleep
        self._session = session or requests.Session()

    def send_text(self, text: str) -> bool:
        return self._send_one(text)

    def send_items(
        self, notifications: list[Notification]
    ) -> tuple[list[Notification], list[Notification]]:
        sent: list[Notification] = []
        failed: list[Notification] = []
        for i, notif in enumerate(notifications):
            if self._send_one(notif.text, notif.image_url):
                sent.append(notif)
            else:
                failed.append(notif)
            if i < len(notifications) - 1:
                time.sleep(self._inter_message_delay)
        return sent, failed

    def _send_one(self, text: str, image_url: str = "") -> bool:
        for _ in range(self._max_retries):
            try:
                if image_url:
                    resp = self._session.post(
                        self._photo_url,
                        json={
                            "chat_id": self._chat_id,
                            "photo": image_url,
                            "caption": text,
                            "parse_mode": "HTML",
                        },
                    )
                    if resp.status_code == 400:
                        # fallback: broken image URL or caption issue → plain text
                        resp = self._session.post(
                            self._url,
                            json={
                                "chat_id": self._chat_id,
                                "text": text,
                                "parse_mode": "HTML",
                            },
                        )
                else:
                    resp = self._session.post(
                        self._url,
                        json={
                            "chat_id": self._chat_id,
                            "text": text,
                            "parse_mode": "HTML",
                        },
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
