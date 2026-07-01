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

    def test_strips_conventional_type_prefix(self) -> None:
        # Type moved out of the title into a label (#256); a leftover conventional
        # prefix must not leak into the branch slug (feat/ci/scope are noise).
        assert slugify("feat(ci): add complexity ratchet") == "add-complexity-ratchet"
        assert slugify("refactor: unify extraction guard") == "unify-extraction-guard"


class TestBuildBranchName:
    def test_concatenates_with_issue_number(self) -> None:
        assert build_branch_name(114, "[feat] add commands") == "issue-114-add-commands"

    def test_falls_back_when_slug_empty(self) -> None:
        assert build_branch_name(42, "русский тайтл") == "issue-42-task"

    def test_prefix_matches_new_branch_constant(self) -> None:
        # Canonical-home guard (#162): build_branch_name must derive its prefix
        # from new_branch.BRANCH_PREFIX, so a future prefix change can't drift
        # past new_branch.py's guard and break the issue_branch→new_branch pipe.
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "scripts.new_branch",
            Path(__file__).resolve().parent.parent / "scripts" / "new_branch.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert build_branch_name(1, "x").startswith(mod.BRANCH_PREFIX)


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
