"""Tests for the secrets gate in `scripts/ci_check.py` (`check_secrets`).

Covers the scanned target set, that a planted secret exits non-zero in both
ASCII and non-ASCII files, and the §IV no-op guards: outside a git repo or with
an empty file set the gate must fail, never pass quietly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.ci_check import _secrets_cmd, _secrets_targets, _tracked_files, check_secrets

# AWS's own documentation example key — a canonical detect-secrets hit, and the
# `pragma` is the documented escape hatch for a genuine false positive (here: the
# gate's own test data). Without it the repo-wide scan would flag this file.
_PLANTED_SECRET = 'aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'  # pragma: allowlist secret


class TestTargets:
    """Captured HTML fixtures are excluded in *our* code, not via a tool regex whose
    semantics depend on the OS path separator (#389)."""

    def test_excludes_captured_html_fixtures(self) -> None:
        tracked = [
            "tests/fixtures/cloudflare_block_403.html",
            "tests/fixtures/github_trending/trending_daily.html",
            "tests/fixtures/summary_golden.json",
            "src/kinozal_scraper/youtube.py",
            "docs/architecture/ci.md",
        ]

        assert _secrets_targets(tracked) == [
            "tests/fixtures/summary_golden.json",
            "src/kinozal_scraper/youtube.py",
            "docs/architecture/ci.md",
        ]


class TestGateFires:
    """The gate config existed for months while executing nothing and, with the empty
    baseline it carried, would have exited 0 on a planted key anyway (#389)."""

    def test_planted_secret_exits_nonzero(self, tmp_path: Path) -> None:
        leaked = tmp_path / "leak.py"
        leaked.write_text(_PLANTED_SECRET, encoding="utf-8")

        assert subprocess.run(_secrets_cmd([str(leaked)])).returncode != 0

    def test_planted_secret_in_a_non_ascii_file_exits_nonzero(self, tmp_path: Path) -> None:
        # `detect_secrets/core/scan.py:261` opens files with the *platform default*
        # encoding and silently skips a UnicodeDecodeError. On Windows (cp1252) that
        # skipped every file carrying a Russian comment — most of this repo — so the
        # gate passed locally and failed in CI on the same commit (#389).
        leaked = tmp_path / "leak_cyrillic.py"
        leaked.write_text(
            f"# комментарий по-русски — не ASCII\n{_PLANTED_SECRET}", encoding="utf-8"
        )

        assert subprocess.run(_secrets_cmd([str(leaked)])).returncode != 0

    def test_clean_file_exits_zero(self, tmp_path: Path) -> None:
        clean = tmp_path / "clean.py"
        clean.write_text("GREETING = 'hello'\n", encoding="utf-8")

        assert subprocess.run(_secrets_cmd([str(clean)])).returncode == 0


class TestNoSilentPass:
    """`detect_secrets.pre_commit_hook` returns 0 when handed an empty file list, so a
    broken `git ls-files` would turn the gate green in silence — the very defect class
    this issue fixes, one layer deeper (§IV)."""

    def test_outside_git_repo_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc:
            _tracked_files()

        assert exc.value.code != 0

    def test_empty_file_set_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc:
            check_secrets()

        assert exc.value.code != 0
