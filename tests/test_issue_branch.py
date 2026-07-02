from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest

import scripts.issue_branch as issue_branch
from scripts.issue_branch import _fetch_title, build_branch_name, slugify


class TestSlugify:
    def test_ascii_title_lowercased_and_dashed(self) -> None:
        assert slugify("Fix Telegram Notifier Bug") == "fix-telegram-notifier-bug"

    def test_caps_at_four_words(self) -> None:
        assert slugify("one two three four five six") == "one-two-three-four"

    def test_drops_non_ascii_falls_back_to_task(self) -> None:
        assert slugify("починить геминай") == "task"

    def test_mixed_ascii_and_cyrillic_keeps_ascii(self) -> None:
        assert slugify("gemini обрезает summary") == "gemini-summary"

    def test_empty_title_falls_back_to_task(self) -> None:
        assert slugify("") == "task"

    def test_special_chars_dropped(self) -> None:
        assert slugify("Add /plan + /implement commands!") == "add-plan-implement-commands"


class TestBuildBranchName:
    def test_concatenates_with_issue_number(self) -> None:
        assert build_branch_name(114, "add commands") == "issue-114-add-commands"

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
        cyrillic_title = "/plan и /implement не работают после PR #121"
        payload = json.dumps({"state": "OPEN", "title": cyrillic_title}, ensure_ascii=False)

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            assert kwargs.get("encoding") == "utf-8", (
                "subprocess must request utf-8 to avoid cp1252 on Windows"
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=payload, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _fetch_title(122) == cyrillic_title


class TestDirectDelegation:
    """`issue_branch.main()` must build the branch in-process via
    `new_branch.create_branch`, not by re-spawning a second interpreter
    (`subprocess.run([sys.executable, ...])`, #254). This is also the first
    coverage of `main()`'s orchestration.
    """

    def test_main_delegates_to_create_branch_in_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        class _FakeNewBranch:
            BRANCH_PREFIX = "issue-"

            @staticmethod
            def create_branch(name: str) -> None:
                calls.append(name)

        monkeypatch.setattr(issue_branch, "_fetch_title", lambda n: "add commands")
        # Single seam for both the prefix (build_branch_name) and the branch
        # creation, so patching it fully isolates the git side-effects.
        monkeypatch.setattr(
            issue_branch, "_new_branch_module", lambda: _FakeNewBranch, raising=False
        )
        # Spy so the pre-refactor re-spawn path cannot shell out to real git
        # during RED; the contract asserted below is the delegate call, not this.
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, "", ""),
        )
        monkeypatch.setattr(sys, "argv", ["issue_branch.py", "254"])

        issue_branch.main()

        assert calls == ["issue-254-add-commands"]
