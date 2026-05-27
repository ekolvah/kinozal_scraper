from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

import google.api_core.exceptions
import google.generativeai as genai
from telethon.sessions import StringSession
from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest

logger = logging.getLogger(__name__)


@dataclass
class ChannelSummary:
    channel: str
    url: str
    summary: str


@dataclass
class ChannelProcessResult:
    channel: str
    url: str
    status: str
    message_count: int = 0
    text_chars: int = 0
    summary: str = ""
    error_kind: str = ""
    error_message: str = ""


class SummarizationFailed(Exception):
    def __init__(self, error_kind: str, message: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.message = message


# Tuple shape returned by readers: (channel_title, joined_messages_text, is_broadcast).
# title=None signals a fetch error — orchestrator falls back to the raw url for display.
ChannelMessages = tuple[str | None, str, bool]


@runtime_checkable
class TelegramReader(Protocol):
    def fetch_channel(self, channel_url: str) -> ChannelMessages: ...


@runtime_checkable
class Summarizer(Protocol):
    def summarize(self, text: str, is_broadcast: bool) -> str: ...


_DEFAULT_BROADCAST_PROMPT = (
    " Это текст постов из телеграм канала. "
    "Проанализируй этот текст и выдели ключевые темы. "
    "Будь лаконичным."
)
_DEFAULT_CHAT_PROMPT = (
    " Это текст сообщений из чата в формате 'Имя: Сообщение'. "
    "Проанализируй этот текст и выдели только основные обсуждаемые темы. "
    "Не пиши детали, кто что сказал, не указывай имена. "
    "Просто перечисли заголовки обсуждаемых тем. Будь максимально лаконичным."
)


def _is_model_unavailable_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "404" in message and (
        "model" in message and ("no longer available" in message or "not found" in message)
    )


class GeminiSummarizer:
    """Sequential Gemini model fallback for recoverable per-model failures.
    Quota and unavailable-model errors advance to the next model. Unknown
    API failures raise `SummarizationFailed` so callers cannot confuse them
    with "no channel messages."
    """

    def __init__(
        self,
        models: list[str],
        broadcast_prompt: str | None = None,
        chat_prompt: str | None = None,
    ) -> None:
        self._models = models
        self._broadcast_prompt = broadcast_prompt or _DEFAULT_BROADCAST_PROMPT
        self._chat_prompt = chat_prompt or _DEFAULT_CHAT_PROMPT

    def summarize(self, text: str, is_broadcast: bool) -> str:
        if not text:
            return ""

        prompt = self._broadcast_prompt if is_broadcast else self._chat_prompt
        request = text + prompt
        failures: list[str] = []

        for model_name in self._models:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(request)

                logger.info("------Оригинальный текст------")
                logger.info(text)
                logger.info("------Саммаризация------")

                if not response.candidates:
                    raise SummarizationFailed(
                        "empty_response", f"{model_name} returned no candidates"
                    )

                logger.info(response.text)
                summary: str = (response.text or "").strip()
                if not summary:
                    raise SummarizationFailed("empty_response", f"{model_name} returned empty text")
                return summary
            except google.api_core.exceptions.ResourceExhausted:
                failures.append(f"{model_name}: quota exhausted")
                logger.warning("model %s quota exhausted, trying next", model_name)
                continue
            except google.api_core.exceptions.NotFound as exc:
                failures.append(f"{model_name}: unavailable: {exc}")
                logger.warning("model %s unavailable, trying next: %s", model_name, exc)
                continue
            except SummarizationFailed:
                raise
            except Exception as exc:
                if _is_model_unavailable_error(exc):
                    failures.append(f"{model_name}: unavailable: {exc}")
                    logger.warning("model %s unavailable, trying next: %s", model_name, exc)
                    continue
                logger.error("Error with model %s: %s", model_name, exc)
                raise SummarizationFailed("api_error", f"{model_name}: {exc}") from exc

        logger.error("all models exhausted, could not summarize")
        detail = "; ".join(failures) if failures else "no models configured"
        raise SummarizationFailed("all_models_failed", detail)


class TelethonReader:
    """Reads the last 24 hours of messages from a Telegram channel via
    Telethon, renders them as a single text payload. Preserves the
    pre-refactor behaviour 1:1: same day-cutoff, same sender-name format,
    same broadcast detection, same swallowed-error path returning
    `(None, "", False)`.
    """

    def __init__(
        self,
        api_id: str | None,
        api_hash: str | None,
        session: str | None,
        phone: str | None,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session = session
        self._phone = phone

    def fetch_channel(self, channel_url: str) -> ChannelMessages:
        return asyncio.run(self._fetch_channel_async(channel_url))

    async def _fetch_channel_async(self, channel_url: str) -> ChannelMessages:
        target: str | int = channel_url
        if isinstance(channel_url, str) and channel_url.lstrip("-").isdigit():
            target = int(channel_url)

        if self._session:
            client = TelegramClient(StringSession(self._session), self._api_id, self._api_hash)
        else:
            client = TelegramClient("anon", self._api_id, self._api_hash)

        try:
            await client.start()
            if not await client.is_user_authorized():
                await client.send_code_request(self._phone)
                await client.sign_in(self._phone, input("Enter the code: "))

            entity = await client.get_entity(target)
            channel_title: str = getattr(entity, "title", str(target))
            posts = await client(
                GetHistoryRequest(
                    peer=entity,
                    limit=100,
                    offset_date=None,
                    offset_id=0,
                    max_id=0,
                    min_id=0,
                    add_offset=0,
                    hash=0,
                )
            )

            one_day_ago = datetime.now(UTC) - timedelta(days=1)
            recent_messages = [m for m in posts.messages if m.date > one_day_ago]
            recent_messages.reverse()

            users = {user.id: user for user in posts.users}

            formatted_messages: list[str] = []
            is_broadcast: bool = getattr(entity, "broadcast", False)
            logger.info("Channel '%s' is_broadcast: %s", channel_title, is_broadcast)
            for message in recent_messages:
                if not message.message:
                    continue
                if is_broadcast:
                    formatted_messages.append(message.message)
                    continue

                sender_name = "Unknown"
                if message.sender_id:
                    sender = users.get(message.sender_id)
                    if sender:
                        first = sender.first_name or ""
                        last = sender.last_name or ""
                        sender_name = f"{first} {last}".strip()
                        if not sender_name and sender.username:
                            sender_name = sender.username
                        if not sender_name:
                            sender_name = str(sender.id)
                formatted_messages.append(f"{sender_name}: {message.message}")

            if not formatted_messages:
                logger.info("No text messages found in channel: %s", channel_url)
                return channel_title, "", is_broadcast

            result = "\n".join(formatted_messages)
            return channel_title, result, is_broadcast
        except Exception as exc:
            logger.error("Error processing channel %s: %s", channel_url, exc)
            return None, "", False
        finally:
            await client.disconnect()


def summarize_channel_results(
    reader: TelegramReader,
    summarizer: Summarizer,
    channel_urls: list[str],
) -> list[ChannelProcessResult]:
    """Fetch and summarize channels while preserving explicit per-channel state."""
    results: list[ChannelProcessResult] = []
    for url in channel_urls:
        channel_title, text, is_broadcast = reader.fetch_channel(url)
        display_name = channel_title if channel_title else url
        message_count = len([line for line in text.splitlines() if line.strip()])
        text_chars = len(text)

        logger.info("-----Telegram channel: %s -----", display_name)
        logger.info(text)
        if channel_title is None and not text:
            results.append(
                ChannelProcessResult(
                    channel=display_name,
                    url=url,
                    status="fetch_failed",
                    message_count=message_count,
                    text_chars=text_chars,
                    error_kind="fetch_failed",
                    error_message="reader could not fetch channel",
                )
            )
            continue
        if not text:
            results.append(
                ChannelProcessResult(
                    channel=display_name,
                    url=url,
                    status="no_text",
                    message_count=0,
                    text_chars=0,
                )
            )
            continue

        try:
            summary = summarizer.summarize(text, is_broadcast)
        except SummarizationFailed as exc:
            results.append(
                ChannelProcessResult(
                    channel=display_name,
                    url=url,
                    status="summarization_failed",
                    message_count=message_count,
                    text_chars=text_chars,
                    error_kind=exc.error_kind,
                    error_message=exc.message,
                )
            )
            continue
        if summary:
            results.append(
                ChannelProcessResult(
                    channel=display_name,
                    url=url,
                    status="summarized",
                    message_count=message_count,
                    text_chars=text_chars,
                    summary=summary,
                )
            )
        else:
            results.append(
                ChannelProcessResult(
                    channel=display_name,
                    url=url,
                    status="summarization_failed",
                    message_count=message_count,
                    text_chars=text_chars,
                    error_kind="empty_summary",
                    error_message="summarizer returned empty text",
                )
            )

    return results


def summarize_channels(
    reader: TelegramReader,
    summarizer: Summarizer,
    channel_urls: list[str],
) -> list[ChannelSummary]:
    """Compatibility wrapper returning only successful summaries."""
    return [
        ChannelSummary(channel=r.channel, url=r.url, summary=r.summary)
        for r in summarize_channel_results(reader, summarizer, channel_urls)
        if r.status == "summarized"
    ]
