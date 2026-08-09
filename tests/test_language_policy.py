"""Tests for the English-only documentation and Python commentary policy."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import check_language
from scripts.check_language import (
    LanguageCheckError,
    markdown_violations,
    missing_expected_areas,
    python_violations,
    scoped_files,
    tracked_files,
)
from scripts.ci_check import CHECKS


class TestMarkdownPolicy:
    def test_cyrillic_prose_is_reported(self) -> None:
        violations = markdown_violations("# Title\n\nРусская проза.\n", path="guide.md")

        assert [(item.path, item.line, item.kind) for item in violations] == [
            ("guide.md", 3, "Markdown prose")
        ]

    def test_code_spans_and_fences_are_ignored(self) -> None:
        text = """English prose with `русские данные`.

```python
message = "русские данные"
```

Русская проза outside code.
"""

        violations = markdown_violations(text)

        assert [(item.line, item.kind) for item in violations] == [(7, "Markdown prose")]


class TestPythonPolicy:
    def test_cyrillic_comments_and_docstrings_are_reported(self) -> None:
        text = '''"""Русский module docstring."""

# Русский комментарий.
def f() -> None:
    """Русский function docstring."""
'''

        violations = python_violations(text, path="module.py")

        assert [(item.path, item.line, item.kind) for item in violations] == [
            ("module.py", 1, "Python docstring"),
            ("module.py", 3, "Python comment"),
            ("module.py", 5, "Python docstring"),
        ]

    def test_string_data_is_ignored(self) -> None:
        text = '''"""English module documentation."""

TELEGRAM_TEXT = "Русское сообщение пользователю"

def prompt() -> str:
    return "Русский Gemini prompt"

# Русский комментарий.
'''

        violations = python_violations(text)

        assert [(item.line, item.kind) for item in violations] == [(7, "Python comment")]


class TestTrackedScope:
    def test_scope_comes_from_git_ls_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def git_listing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=b"tracked.md\0notes.txt\0", stderr=b""
            )

        monkeypatch.setattr(check_language.subprocess, "run", git_listing)

        assert tracked_files(Path("repo")) == [Path("tracked.md"), Path("notes.txt")]
        assert scoped_files(tracked_files(Path("repo"))) == [Path("tracked.md")]

    def test_every_expected_area_contributes(self) -> None:
        paths = scoped_files(tracked_files(Path(".")))

        assert paths, "tracked Markdown/Python scope must not collapse to empty"
        assert missing_expected_areas(paths) == []

    def test_capture_failure_is_not_an_empty_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failed_capture(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=None, stderr=None)

        monkeypatch.setattr(check_language.subprocess, "run", failed_capture)

        with pytest.raises(LanguageCheckError, match="captured output"):
            tracked_files(Path("."))


def test_migration_allowlist_is_empty() -> None:
    assert check_language.MIGRATION_ALLOWLIST == frozenset()


def test_ci_registry_includes_language_check() -> None:
    assert "language" in CHECKS
