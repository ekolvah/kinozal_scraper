"""Tests for the fixed-path issue-PR report publisher."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import Mock

import pytest


def _module() -> ModuleType:
    return importlib.import_module("scripts.publish_pr_report")


def test_report_path_is_fixed_inside_repository() -> None:
    module = _module()
    assert module.REPORT_PATH == module.REPO_ROOT / ".codex" / "pr-body.md"
    assert module.REPORT_PATH.is_absolute()


def test_report_path_rejects_symlink() -> None:
    mock_link = Mock(spec=Path)
    mock_link.is_file.return_value = True
    mock_link.is_symlink.return_value = True
    link = cast("Path", mock_link)
    with pytest.raises(ValueError, match="regular non-symlink"):
        _module().read_report(link)


def test_command_line_rejects_caller_supplied_paths() -> None:
    with pytest.raises(SystemExit, match="2"):
        _module().main(["--body-file", "C:/secret.txt"])
