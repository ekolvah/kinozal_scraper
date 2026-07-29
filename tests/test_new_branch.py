"""Tests for `scripts/new_branch.py`.

Loaded by absolute path via `importlib.util` (rather than
`import scripts.new_branch`) so the test targets the module by file location
regardless of `sys.path` — the same way the production `issue_branch.py` loads
it under the `python scripts/issue_branch.py <N>` CLI.
"""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

_NEW_BRANCH_PY = Path(__file__).resolve().parent.parent / "scripts" / "new_branch.py"


def _load_new_branch_module() -> Any:
    spec = importlib.util.spec_from_file_location("scripts.new_branch", _NEW_BRANCH_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRunReturnsString(unittest.TestCase):
    """`stdout is None` при запрошенном захвате — видимая аномалия, не пустая строка.

    **Инверсия пин-теста #109 (#410).** Раньше `_run` нормализовал `None` в `""`,
    потому что причина была неизвестна и `None` считался причудой платформы. #364
    показал, что это симптом: поток-читатель умирал на `UnicodeDecodeError`,
    оставляя буфер пустым. Причина закрыта явным `encoding` + гардом, поэтому
    `None` теперь означает настоящий отказ захвата — и нормализация превратилась в
    подмену отказа пустой строкой (§IV) в скрипте, который сам является гейтом.
    """

    def test_none_stdout_raises_with_command_in_message(self) -> None:
        new_branch = _load_new_branch_module()
        fake_proc: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout=None, stderr=None
        )
        with (
            unittest.mock.patch("subprocess.run", return_value=fake_proc),
            self.assertRaises(RuntimeError) as caught,
        ):
            new_branch._run(["git", "branch", "-vv"], capture=True)
        self.assertIn("git branch -vv", str(caught.exception))

    def test_none_stdout_is_legitimate_without_capture(self) -> None:
        # `capture=False` — вывод идёт в консоль, `stdout is None` штатный исход.
        # Падение здесь означало бы fail-fast-перекос: аномалии нет.
        new_branch = _load_new_branch_module()
        fake_proc: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout=None, stderr=None
        )
        with unittest.mock.patch("subprocess.run", return_value=fake_proc):
            result = new_branch._run(["git", "fetch", "--prune"], capture=False)
        self.assertIsNone(result.stdout)

    def test_stdout_unchanged_when_subprocess_returns_string(self) -> None:
        new_branch = _load_new_branch_module()
        fake_proc: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout="  feature/x\n* main\n", stderr=""
        )
        with unittest.mock.patch("subprocess.run", return_value=fake_proc):
            result = new_branch._run(["git", "branch", "-vv"], capture=True)
        self.assertEqual(result.stdout, "  feature/x\n* main\n")


class TestPruneGoneBranchesSurfacesNoneStdout(unittest.TestCase):
    """Инверсия #109-пин-теста (#410): молчаливое продолжение → видимая аномалия.

    Раньше тест требовал «must not raise» и отчёт `pruned: 0 merged branches`.
    Проблема ровно в этом отчёте: он неотличим от честного «нечего удалять»,
    хотя на деле список веток не был получен вообще. После #364 `None` при
    запрошенном захвате означает сломанный захват — оператор должен это увидеть,
    а не прочитать успокаивающую цифру.
    """

    def test_none_stdout_from_git_branch_is_visible(self) -> None:
        new_branch = _load_new_branch_module()

        # First call: `git fetch --prune` (capture=False) → stdout None is
        # expected and irrelevant. Second call: `git branch -vv` (capture=True)
        # → stdout=None now means the capture itself failed.
        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=None, stderr=None)

        with (
            unittest.mock.patch("subprocess.run", side_effect=fake_run),
            self.assertRaises(RuntimeError),
        ):
            new_branch._prune_gone_branches()


class TestPruneGoneBranchesParsesGoneEntries(unittest.TestCase):
    """When `git branch -vv` returns real output containing `[gone]` markers,
    the matching branches (except current and protected) are deleted via
    `git branch -d`."""

    def test_deletes_gone_branches_skipping_current_and_protected(self) -> None:
        new_branch = _load_new_branch_module()

        branch_listing = (
            "  feature/done    abc123 [origin/feature/done: gone] commit\n"
            "* feature/active  def456 [origin/feature/active: gone] still here\n"
            "  main            789abc [origin/main] up to date\n"
            "  feature/clean   111111 [origin/feature/clean] not gone\n"
        )

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            if cmd[:2] == ["git", "fetch"]:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=None, stderr=None)
            if cmd[:3] == ["git", "branch", "-vv"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=branch_listing, stderr=""
                )
            if cmd[:3] == ["git", "branch", "-d"]:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected command: {cmd}")

        with unittest.mock.patch("subprocess.run", side_effect=fake_run):
            new_branch._prune_gone_branches()

        deletions = [c for c in calls if c[:3] == ["git", "branch", "-d"]]
        # feature/active was current (`* `) → skipped; main is in PROTECTED.
        self.assertEqual(deletions, [["git", "branch", "-d", "feature/done"]])


class TestBranchNameGuard(unittest.TestCase):
    """`is_valid_branch_name` must accept the project prefix and reject others.

    Pin-test for #162 (rename `codex-` → `issue-`): the guard gates branch
    creation in `main()`; only `issue-…` names are valid.
    """

    def test_accepts_issue_prefix(self) -> None:
        new_branch = _load_new_branch_module()
        self.assertTrue(new_branch.is_valid_branch_name("issue-162-rename-codex"))

    def test_rejects_codex_prefix(self) -> None:
        new_branch = _load_new_branch_module()
        self.assertFalse(new_branch.is_valid_branch_name("codex-issue-1-x"))

    def test_rejects_unprefixed(self) -> None:
        new_branch = _load_new_branch_module()
        self.assertFalse(new_branch.is_valid_branch_name("foo-bar"))


class TestCreateBranchVisibility(unittest.TestCase):
    """The `create_branch` helper extracted from `main()` (#254) must keep the
    dirty-tree guard a hard failure (non-zero exit), so a dirty working tree
    cannot be silently skipped when `create_branch` is driven in-process from
    `issue_branch` (§IV visibility).
    """

    def test_dirty_tree_exits_nonzero(self) -> None:
        new_branch = _load_new_branch_module()

        def fake_run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess[str]:
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=" M dirty.py\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with (
            unittest.mock.patch.object(new_branch, "_run", side_effect=fake_run),
            self.assertRaises(SystemExit) as ctx,
        ):
            new_branch.create_branch("issue-254-x")
        self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
