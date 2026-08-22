"""Guard the Claude plugin payload, which ``test_doc_links.py`` excludes under ``templates/``.

This is therefore the plugin payload's only structural, citation, and relative-link coverage.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from test_agent_process_template import (
    _CITATION,
    _LINKED_CITATION,
    _expand_file_cell,
    _manifest_rows,
    _section,
)

from scripts.agent_process_plugin import transform_payload

_REPO = Path(__file__).resolve().parent.parent
_MANIFEST = _REPO / "docs" / "architecture" / "agent-process-export.md"
_PLUGIN = _REPO / "templates" / "agent-process-plugin"
_RELATIVE_LINK = re.compile(r"\[[^\]]*\]\((?!https?://)([^)]+)\)")


def _plugin_entries() -> list[str]:
    """Return the Layer 1 plugin-marketplace sources from the export manifest."""
    text = _MANIFEST.read_text(encoding="utf-8")
    layer1 = _section(text, "## Layer 1", "### Install order")
    return [
        path
        for cells in _manifest_rows(layer1)
        if len(cells) > 3 and cells[2] == "plugin marketplace"
        for path in _expand_file_cell(cells[0])
    ]


def test_plugin_metadata_is_structurally_safe() -> None:
    """The plugin metadata must not pretend to ship unavailable settings or hooks."""
    metadata = json.loads((_PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert metadata["name"] == "agent-process"
    assert {"displayName", "description", "version", "author"} <= metadata.keys()
    assert not {"permissions", "rules", "hooks"} & metadata.keys()


def test_marketplace_points_to_this_plugin_directory() -> None:
    """The local marketplace entry resolves to the exact package under test."""
    marketplace_path = _PLUGIN / ".claude-plugin" / "marketplace.json"
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    plugins = marketplace["plugins"]
    assert len(plugins) == 1
    assert plugins[0]["name"] == "agent-process"
    marketplace_root = marketplace_path.parent.parent
    assert (marketplace_root / plugins[0]["source"]).resolve() == _PLUGIN.resolve()


def test_manifest_plugin_entries_are_transformed_payloads() -> None:
    """Every manifest-selected source has the mapped, generated plugin counterpart."""
    entries = _plugin_entries()
    assert entries, "Layer 1 must declare plugin-marketplace payload sources"
    for source_path in entries:
        payload_path = _PLUGIN / source_path.removeprefix(".claude/")
        source = (_REPO / source_path).read_text(encoding="utf-8")
        assert payload_path.is_file(), f"missing plugin payload: {payload_path.relative_to(_REPO)}"
        assert transform_payload(source) == payload_path.read_text(encoding="utf-8")


def test_plugin_payload_has_no_repository_citations_or_dangling_links() -> None:
    """Plugin transforms strip citations and drop cross-layer relative Markdown links."""
    payloads = (*(_PLUGIN / "commands").glob("*.md"), *(_PLUGIN / "agents").glob("*.md"))
    assert payloads
    for payload in payloads:
        content = payload.read_text(encoding="utf-8")
        assert not _CITATION.search(content)
        assert not _LINKED_CITATION.search(content)
        assert not _RELATIVE_LINK.search(content)
        assert "../../docs/" not in content


def test_plugin_specific_rewrites_preserve_claude_builtins() -> None:
    """Only plugin commands and agent personas change names during the export."""
    plan = (_PLUGIN / "commands" / "plan.md").read_text(encoding="utf-8")
    implement = (_PLUGIN / "commands" / "implement.md").read_text(encoding="utf-8")
    assert "# /agent-process:plan N" in plan
    assert "`/agent-process:implement $ARGUMENTS`" in plan
    assert "persona: discovery" in plan
    assert "persona: architect-reviewer" in plan
    assert "Agent development process §Planner Runbook" in plan
    assert "# /agent-process:implement N" in implement
    assert "/compact" in implement
    for agent in ("discovery.md", "architect-reviewer.md"):
        content = (_PLUGIN / "agents" / agent).read_text(encoding="utf-8")
        assert "Read the contract at the link above" not in content
        assert "Read the contract named above" in content
