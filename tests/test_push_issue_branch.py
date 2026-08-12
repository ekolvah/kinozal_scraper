"""Tests for the least-privilege issue-branch publication wrapper."""

from __future__ import annotations

import importlib

import pytest


def _module():
    return importlib.import_module("scripts.push_issue_branch")


def test_pushes_only_current_issue_branch_to_origin() -> None:
    assert _module().push_command(
        remote_url="https://github.com/ekolvah/kinozal_scraper.git",
        branch="issue-499-codex-delivery-make-issue",
        worktree_status="",
    ) == [
        "git",
        "push",
        "--set-upstream",
        "origin",
        "issue-499-codex-delivery-make-issue",
    ]


def test_rejects_noncanonical_remote() -> None:
    with pytest.raises(ValueError, match="canonical repository"):
        _module().push_command(
            remote_url="https://github.com/example/other.git",
            branch="issue-499-codex-delivery-make-issue",
            worktree_status="",
        )


def test_rejects_non_issue_branch() -> None:
    with pytest.raises(ValueError, match="issue-N"):
        _module().push_command(
            remote_url="https://github.com/ekolvah/kinozal_scraper.git",
            branch="main",
            worktree_status="",
        )


def test_rejects_dirty_worktree() -> None:
    with pytest.raises(ValueError, match="dirty"):
        _module().push_command(
            remote_url="https://github.com/ekolvah/kinozal_scraper.git",
            branch="issue-499-codex-delivery-make-issue",
            worktree_status=" M src/example.py",
        )
