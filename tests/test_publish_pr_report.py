"""Tests for the fixed-path issue-PR report publisher."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import Mock

import pytest


def _module():
    return importlib.import_module("scripts.publish_pr_report")


def test_report_path_is_fixed_inside_repository() -> None:
    module = _module()
    assert module.REPORT_PATH == module.REPO_ROOT / ".codex" / "pr-body.md"
    assert module.REPORT_PATH.is_absolute()


def test_report_path_rejects_symlink() -> None:
    link = Mock(spec=Path)
    link.is_file.return_value = True
    link.is_symlink.return_value = True
    with pytest.raises(ValueError, match="regular non-symlink"):
        _module().read_report(link)


def test_command_line_rejects_caller_supplied_paths() -> None:
    with pytest.raises(SystemExit, match="2"):
        _module().main(["--body-file", "C:/secret.txt"])
