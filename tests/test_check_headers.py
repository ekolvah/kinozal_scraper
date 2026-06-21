from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_headers import missing_docstrings

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


class TestMissingDocstrings:
    def test_file_with_docstring_ok(self, tmp_path: Path) -> None:
        _write(tmp_path, "good.py", '"""Answers a question."""\n\nx = 1\n')
        assert missing_docstrings(tmp_path) == []

    def test_file_without_docstring_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, "bad.py", "x = 1\n")
        assert "bad.py" in [Path(p).name for p in missing_docstrings(tmp_path)]

    def test_empty_docstring_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, "empty.py", '""\n\nx = 1\n')
        assert "empty.py" in [Path(p).name for p in missing_docstrings(tmp_path)]

    def test_whitespace_only_docstring_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, "blank.py", '"""   """\n\nx = 1\n')
        assert "blank.py" in [Path(p).name for p in missing_docstrings(tmp_path)]

    def test_test_and_conftest_excluded(self, tmp_path: Path) -> None:
        _write(tmp_path, "test_foo.py", "x = 1\n")
        _write(tmp_path, "conftest.py", "x = 1\n")
        assert missing_docstrings(tmp_path) == []

    def test_unparseable_file_flagged_not_swallowed(self, tmp_path: Path) -> None:
        # A syntactically broken file is a loud anomaly (§IV), not "missing
        # docstring" and never silently skipped: the scan raises, not returns.
        _write(tmp_path, "broken.py", "def (:\n")
        with pytest.raises(SyntaxError):
            missing_docstrings(tmp_path)

    def test_repo_clean(self) -> None:
        # Contract guard: every source .py under src/ must carry a module docstring.
        assert missing_docstrings(_REPO_ROOT / "src") == []
