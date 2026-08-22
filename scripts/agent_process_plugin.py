"""Build the Claude Code plugin payload from its Layer 1 source files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PLUGIN_ROOT = _REPO / "templates" / "agent-process-plugin"
_SOURCES = (
    ".claude/commands/plan.md",
    ".claude/commands/implement.md",
    ".claude/agents/discovery.md",
    ".claude/agents/architect-reviewer.md",
)
_LINK = re.compile(r"\[([^\]]+)\]\((?!https?://)([^)]+)\)")
_LINKED_CITATION = re.compile(r"\[#\d{2,4}\]\([^)]*\)")
_PARENTHESIZED_CITATION = re.compile(r"\s*\(#\d{2,4}\)")
_CITATION = re.compile(r"#\d{2,4}\b")
_PERSONA = re.compile(r"persona: `\.claude/agents/([^`/]+)\.md`")
_DOC_TITLES = {
    "agent-process.md": "Agent development process",
    "principles.md": "Principles",
    "mindset.md": "Mindset",
}


def _drop_relative_link(match: re.Match[str]) -> str:
    """Replace an inaccessible plugin link with its document title as plain text."""
    target = match.group(2)
    path, _, anchor = target.partition("#")
    title = _DOC_TITLES.get(Path(path).name, match.group(1).strip("`"))
    section = f" §{anchor.replace('-', ' ').title()}" if anchor else ""
    return f"{title}{section}"


def transform_payload(source: str) -> str:
    """Strip repository-only references and apply the plugin's invocation names."""
    payload = _LINKED_CITATION.sub("", source)
    payload = _PARENTHESIZED_CITATION.sub("", payload)
    payload = _CITATION.sub("", payload)
    payload = _LINK.sub(_drop_relative_link, payload)
    payload = _PERSONA.sub(r"persona: \1", payload)
    payload = payload.replace("Read the contract at the link above", "Read the contract named above")
    payload = payload.replace("# /plan N", "# /agent-process:plan N")
    payload = payload.replace("# /implement N", "# /agent-process:implement N")
    return payload.replace("/implement $ARGUMENTS", "/agent-process:implement $ARGUMENTS")


def write_payload(repo_root: Path = _REPO, plugin_root: Path = _PLUGIN_ROOT) -> None:
    """Generate each plugin command and agent from its repository source counterpart."""
    for source_path in _SOURCES:
        source = (repo_root / source_path).read_text(encoding="utf-8")
        target = plugin_root / source_path.removeprefix(".claude/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(transform_payload(source), encoding="utf-8")


def main() -> None:
    """Write the tracked payload after its source commands or agents change."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the generated plugin payload")
    args = parser.parse_args()
    if not args.write:
        parser.error("--write is required")
    write_payload()


if __name__ == "__main__":
    main()
