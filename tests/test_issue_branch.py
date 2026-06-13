from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.issue_branch import _fetch_title, build_branch_name, slugify


class TestSlugify:
    def test_ascii_title_lowercased_and_dashed(self) -> None:
        assert slugify("Fix Telegram Notifier Bug") == "fix-telegram-notifier-bug"

    def test_strips_type_prefix_tag(self) -> None:
        assert slugify("[bug] gemini truncates summary") == "gemini-truncates-summary"

    def test_strips_feat_prefix_tag(self) -> None:
        assert slugify("[feat] Add github trending source") == "add-github-trending-source"

    def test_caps_at_four_words(self) -> None:
        assert slugify("one two three four five six") == "one-two-three-four"

    def test_drops_non_ascii_falls_back_to_task(self) -> None:
        assert slugify("починить геминай") == "task"

    def test_mixed_ascii_and_cyrillic_keeps_ascii(self) -> None:
        assert slugify("[bug] gemini обрезает summary") == "gemini-summary"

    def test_empty_title_falls_back_to_task(self) -> None:
        assert slugify("") == "task"

    def test_special_chars_dropped(self) -> None:
        assert slugify("Add /plan + /implement commands!") == "add-plan-implement-commands"


class TestBuildBranchName:
    def test_concatenates_with_issue_number(self) -> None:
        assert build_branch_name(114, "[feat] add commands") == "issue-114-add-commands"

    def test_falls_back_when_slug_empty(self) -> None:
        assert build_branch_name(42, "русский тайтл") == "issue-42-task"


class TestFetchTitleEncoding:
    def test_cyrillic_title_decodes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cyrillic_title = "[bug] /plan и /implement не работают после PR #121"
        payload = json.dumps({"state": "OPEN", "title": cyrillic_title}, ensure_ascii=False)

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            assert kwargs.get("encoding") == "utf-8", (
                "subprocess must request utf-8 to avoid cp1252 on Windows"
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=payload, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _fetch_title(122) == cyrillic_title
