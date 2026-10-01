"""Tests for `scripts/check_fixture_ratchet.py` — new external-data parsing tests use fixtures."""

from __future__ import annotations

import importlib
from pathlib import Path


def test_new_external_data_parsing_test_with_inline_markup_is_reported() -> None:
    ratchet = importlib.import_module("scripts.check_fixture_ratchet")
    source = """
def test_parses_external_page():
    html = "<html><img class='cat_img_r' src='/pic/cat/6.gif'></html>"
    assert parse_page(html) == 6
"""
    assert ratchet.inline_external_data_test_nodes(
        source, path=Path("tests/test_new_source.py")
    ) == ["tests/test_new_source.py::test_parses_external_page"]

    repo_root = Path(__file__).resolve().parent.parent
    assert ratchet.scan_repository(repo_root) == []
