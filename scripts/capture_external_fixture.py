#!/usr/bin/env python3
"""Capture repository-safe evidence through read-only external-system routes."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import BinaryIO, Protocol, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TelegramReader(Protocol):
    def fetch_channel(self, channel_url: str) -> tuple[str | None, str, bool]: ...


class Summarizer(Protocol):
    def summarize(self, text: str, is_broadcast: bool) -> str: ...


ApiGet = Callable[[str], bytes]
RowsReader = Callable[[str, str], list[list[str]]]


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _write_bytes(path, encoded)


def capture_stdin(path: Path, *, stream: BinaryIO | None = None) -> None:
    """Persist bytes emitted by an existing read-only source command."""
    active_stream = stream if stream is not None else sys.stdin.buffer
    _write_bytes(path, active_stream.read())


def _gh_api_get(endpoint: str) -> bytes:
    completed = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    if completed.stdout is None or completed.stderr is None:
        raise RuntimeError(
            f"gh api output capture failed: stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"gh api {endpoint} failed with exit {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout.encode("utf-8")


def capture_github(endpoint: str, path: Path, *, api_get: ApiGet | None = None) -> None:
    """Capture one GitHub REST GET without accepting arbitrary subprocess arguments."""
    active_get = api_get if api_get is not None else _gh_api_get
    _write_bytes(path, active_get(endpoint))


def _telegram_reader_from_env() -> TelegramReader:
    from kinozal_scraper.TelegramChannelSummarizer import TelethonReader

    return cast(
        TelegramReader,
        TelethonReader(
            api_id=os.environ["TELEGRAM_API_ID"],
            api_hash=os.environ["API_HASH"],
            session=os.environ["TELETHON_SESSION"],
        ),
    )


def capture_telegram(
    channel_url: str,
    path: Path,
    *,
    reader: TelegramReader | None = None,
) -> None:
    """Read one Telegram channel without invoking Gemini or a notifier."""
    active_reader = reader if reader is not None else _telegram_reader_from_env()
    channel, messages, is_broadcast = active_reader.fetch_channel(channel_url)
    if channel is None:
        raise RuntimeError(
            "Telegram channel capture failed; inspect the preceding Telethon error output"
        )
    _write_json(
        path,
        {
            "channel": channel,
            "is_broadcast": is_broadcast,
            "messages": messages,
            "url": channel_url,
        },
    )


def _gemini_summarizer_from_env() -> Summarizer:
    from google import genai

    from kinozal_scraper.gemini_enricher import get_generation_models
    from kinozal_scraper.TelegramChannelSummarizer import GeminiSummarizer

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    models = get_generation_models(client)
    if not models:
        raise RuntimeError("Gemini capture found no available generation models")
    return cast(
        Summarizer,
        GeminiSummarizer(
            models=models,
            client=client,
            broadcast_prompt=os.getenv("BROADCAST_PROMPT"),
            chat_prompt=os.getenv("CHAT_PROMPT"),
        ),
    )


def capture_gemini(
    input_path: Path,
    path: Path,
    *,
    summarizer: Summarizer | None = None,
    is_broadcast: bool,
) -> None:
    """Replay one saved Telegram input through the production summarizer only."""
    text = input_path.read_text(encoding="utf-8")
    active_summarizer = summarizer if summarizer is not None else _gemini_summarizer_from_env()
    summary = active_summarizer.summarize(text, is_broadcast)
    _write_json(
        path,
        {
            "input": input_path.as_posix(),
            "mode": "broadcast" if is_broadcast else "chat",
            "summary": summary,
        },
    )


def _read_sheets_rows(spreadsheet_url: str, worksheet: str) -> list[list[str]]:
    import gspread

    credentials = json.loads(os.environ["CREDENTIALS"])
    client = gspread.service_account_from_dict(credentials)
    return client.open_by_url(spreadsheet_url).worksheet(worksheet).get_all_values()


def capture_sheets(
    spreadsheet_url: str,
    worksheet: str,
    path: Path,
    *,
    rows_reader: RowsReader | None = None,
) -> None:
    """Read an existing worksheet without creating tabs or writing rows."""
    active_reader = rows_reader if rows_reader is not None else _read_sheets_rows
    rows = active_reader(spreadsheet_url, worksheet)
    _write_json(
        path,
        {
            "rows": rows,
            "spreadsheet_url": spreadsheet_url,
            "worksheet": worksheet,
        },
    )


def _add_safety_confirmation(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--confirm-repository-safe",
        action="store_true",
        help="confirm that captured content contains no secrets or private data",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    routes = parser.add_subparsers(dest="source", required=True)

    stdin = routes.add_parser("stdin", help="persist stdout from another read-only tool")
    stdin.add_argument("path", type=Path)
    _add_safety_confirmation(stdin)

    github = routes.add_parser("github", help="capture one GitHub REST GET through gh api")
    github.add_argument("endpoint")
    github.add_argument("path", type=Path)
    _add_safety_confirmation(github)

    telegram = routes.add_parser("telegram", help="read one Telegram channel through Telethon")
    telegram.add_argument("channel_url")
    telegram.add_argument("path", type=Path)
    _add_safety_confirmation(telegram)

    gemini = routes.add_parser("gemini", help="replay a saved input through Gemini summarization")
    gemini.add_argument("input_path", type=Path)
    gemini.add_argument("path", type=Path)
    mode = gemini.add_mutually_exclusive_group(required=True)
    mode.add_argument("--broadcast", dest="is_broadcast", action="store_true")
    mode.add_argument("--chat", dest="is_broadcast", action="store_false")
    _add_safety_confirmation(gemini)

    sheets = routes.add_parser("sheets", help="read one existing Google Sheets worksheet")
    sheets.add_argument("spreadsheet_url")
    sheets.add_argument("worksheet")
    sheets.add_argument("path", type=Path)
    _add_safety_confirmation(sheets)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.confirm_repository_safe:
        parser.error("--confirm-repository-safe is required before writing external data")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if args.source == "stdin":
        capture_stdin(args.path)
    elif args.source == "github":
        capture_github(args.endpoint, args.path)
    elif args.source == "telegram":
        capture_telegram(args.channel_url, args.path)
    elif args.source == "gemini":
        capture_gemini(
            args.input_path,
            args.path,
            is_broadcast=args.is_broadcast,
        )
    elif args.source == "sheets":
        capture_sheets(args.spreadsheet_url, args.worksheet, args.path)
    print(f"captured {args.source} evidence -> {args.path}")


if __name__ == "__main__":
    main()
