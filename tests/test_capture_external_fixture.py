"""Read-only evidence capture routes for external systems."""

from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
from types import ModuleType

import pytest


def _capture_module() -> ModuleType:
    return importlib.import_module("scripts.capture_external_fixture")


def test_stdin_route_preserves_output_bytes(tmp_path: Path) -> None:
    capture = _capture_module()
    target = tmp_path / "unknown-source" / "response.bin"

    capture.capture_stdin(target, stream=io.BytesIO(b"first\r\nsecond\n"))

    assert target.read_bytes() == b"first\r\nsecond\n"


def test_github_route_uses_only_the_requested_api_endpoint(tmp_path: Path) -> None:
    capture = _capture_module()
    seen: list[str] = []
    target = tmp_path / "github" / "issue.json"

    def api_get(endpoint: str) -> bytes:
        seen.append(endpoint)
        return b'{"number":509}\n'

    capture.capture_github("repos/ekolvah/kinozal_scraper/issues/509", target, api_get=api_get)

    assert seen == ["repos/ekolvah/kinozal_scraper/issues/509"]
    assert target.read_bytes() == b'{"number":509}\n'


def test_telegram_route_reads_without_summarizing_or_notifying(tmp_path: Path) -> None:
    capture = _capture_module()
    target = tmp_path / "telegram" / "channel.json"

    class StubReader:
        def fetch_channel(self, channel_url: str) -> tuple[str | None, str, bool]:
            assert channel_url == "https://t.me/example"
            return "Example", "observed post", True

    capture.capture_telegram("https://t.me/example", target, reader=StubReader())

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "channel": "Example",
        "is_broadcast": True,
        "messages": "observed post",
        "url": "https://t.me/example",
    }


def test_gemini_route_replays_a_saved_input_without_telegram_delivery(tmp_path: Path) -> None:
    capture = _capture_module()
    source = tmp_path / "telegram" / "input.txt"
    source.parent.mkdir(parents=True)
    source.write_text("captured channel text", encoding="utf-8")
    target = tmp_path / "gemini" / "summary.json"
    seen: list[tuple[str, bool]] = []

    class StubSummarizer:
        def summarize(self, text: str, is_broadcast: bool) -> str:
            seen.append((text, is_broadcast))
            return "captured summary"

    capture.capture_gemini(source, target, summarizer=StubSummarizer(), is_broadcast=True)

    assert seen == [("captured channel text", True)]
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "input": source.as_posix(),
        "mode": "broadcast",
        "summary": "captured summary",
    }


def test_sheets_route_is_read_only(tmp_path: Path) -> None:
    capture = _capture_module()
    target = tmp_path / "sheets" / "rows.json"
    seen: list[tuple[str, str]] = []

    def read_rows(spreadsheet_url: str, worksheet: str) -> list[list[str]]:
        seen.append((spreadsheet_url, worksheet))
        return [["dedupe_key", "title"], ["1", "Observed"]]

    capture.capture_sheets(
        "https://docs.google.com/spreadsheets/d/example",
        "kinozal",
        target,
        rows_reader=read_rows,
    )

    assert seen == [("https://docs.google.com/spreadsheets/d/example", "kinozal")]
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "rows": [["dedupe_key", "title"], ["1", "Observed"]],
        "spreadsheet_url": "https://docs.google.com/spreadsheets/d/example",
        "worksheet": "kinozal",
    }


def test_cli_requires_an_explicit_repository_safety_confirmation(tmp_path: Path) -> None:
    capture = _capture_module()

    with pytest.raises(SystemExit) as exc:
        capture.main(["stdin", str(tmp_path / "fixture.txt")])

    assert exc.value.code == 2


def test_cli_dispatches_stdin_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _capture_module()
    target = tmp_path / "stdin.txt"
    seen: list[Path] = []
    monkeypatch.setattr(capture, "capture_stdin", seen.append)

    capture.main(["stdin", str(target), "--confirm-repository-safe"])

    assert seen == [target]


def test_cli_dispatches_github_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _capture_module()
    target = tmp_path / "github.json"
    seen: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        capture, "capture_github", lambda endpoint, path: seen.append((endpoint, path))
    )

    capture.main(
        [
            "github",
            "repos/ekolvah/kinozal_scraper/issues/509",
            str(target),
            "--confirm-repository-safe",
        ]
    )

    assert seen == [("repos/ekolvah/kinozal_scraper/issues/509", target)]


def test_cli_dispatches_telegram_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _capture_module()
    target = tmp_path / "telegram.json"
    seen: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        capture, "capture_telegram", lambda channel, path: seen.append((channel, path))
    )

    capture.main(["telegram", "https://t.me/example", str(target), "--confirm-repository-safe"])

    assert seen == [("https://t.me/example", target)]


def test_cli_dispatches_gemini_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _capture_module()
    source = tmp_path / "input.txt"
    target = tmp_path / "gemini.json"
    seen: list[tuple[Path, Path, bool]] = []
    monkeypatch.setattr(
        capture,
        "capture_gemini",
        lambda input_path, path, *, is_broadcast: seen.append((input_path, path, is_broadcast)),
    )

    capture.main(
        [
            "gemini",
            str(source),
            str(target),
            "--broadcast",
            "--confirm-repository-safe",
        ]
    )

    assert seen == [(source, target, True)]


def test_cli_dispatches_sheets_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _capture_module()
    target = tmp_path / "sheets.json"
    seen: list[tuple[str, str, Path]] = []
    monkeypatch.setattr(
        capture,
        "capture_sheets",
        lambda url, worksheet, path: seen.append((url, worksheet, path)),
    )

    capture.main(
        [
            "sheets",
            "https://docs.google.com/spreadsheets/d/example",
            "kinozal",
            str(target),
            "--confirm-repository-safe",
        ]
    )

    assert seen == [("https://docs.google.com/spreadsheets/d/example", "kinozal", target)]
