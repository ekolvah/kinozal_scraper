"""Guard tests for the copier template payload built from the export manifest (#570).

`docs/architecture/agent-process-export.md` is the single home of the Layer 0/2 file
list (§Manifest scope); these tests parse its tables directly rather than hard-coding
a second copy, mirroring `tests/test_agent_process.py`'s `_documented_change_class_matrix`
pattern for `change-classes.yaml`.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MANIFEST = _REPO / "docs" / "architecture" / "agent-process-export.md"
_TEMPLATE = _REPO / "templates" / "agent-process"

_CITATION = re.compile(r"#\d{2,4}\b")
_LINKED_CITATION = re.compile(r"\[#\d{2,4}\]\([^)]*\)")
_ABS_ISSUE_URL = re.compile(r"https://github\.com/ekolvah/kinozal_scraper/issues/\d+")
_SOURCE_REPOSITORY_URL = re.compile(r"https://github\.com/ekolvah/kinozal_scraper(?:/|\b)")

# Cells whose secondary backtick token is relative to the primary path's own directory,
# not repo-root-relative (the manifest's skill "metadata sidecar" rows, e.g.
# "`.agents/skills/plan-issue/SKILL.md` (+ `agents/openai.yaml` metadata sidecar)").
_ROOTED_PREFIXES = ("docs/", "scripts/", ".agents/", ".github/", ".claude/", ".codex/", "tests/")


def _manifest_text() -> str:
    return _MANIFEST.read_text(encoding="utf-8")


def _section(text: str, start_heading: str, end_heading: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


def _manifest_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[0] in ("File",) or set(cells[0]) <= {"-"}:
            continue
        rows.append(cells)
    return rows


def _expand_file_cell(cell: str) -> list[str]:
    tokens = re.findall(r"`([^`]+)`", cell)
    if not tokens:
        return []
    paths = [tokens[0]]
    base_dir = tokens[0].rsplit("/", 1)[0] if "/" in tokens[0] else ""
    for extra in tokens[1:]:
        if extra.startswith(_ROOTED_PREFIXES) or extra == "AGENTS.md":
            paths.append(extra)
        else:
            paths.append(f"{base_dir}/{extra}" if base_dir else extra)
    return paths


def _manifest_layer0_and_layer2_entries() -> list[tuple[str, str]]:
    """Return (repo_relative_path, export_status) for every Layer 0 + Layer 2 file."""
    text = _manifest_text()
    entries: list[tuple[str, str]] = []
    layer0 = _section(text, "## Layer 0", "## Layer 1")
    layer2 = _section(text, "## Layer 2", "## Not exported")
    for section in (layer0, layer2):
        for cells in _manifest_rows(section):
            status = cells[1]
            for path in _expand_file_cell(cells[0]):
                entries.append((path, status))
    return entries


def _exported_core_size_budget() -> int:
    match = re.search(
        r"cap the exported combined size of `agent-process\.md` \+ `principles\.md` at (\d+) KB",
        _manifest_text(),
    )
    assert match is not None, "export manifest no longer declares its core-size budget"
    return int(match.group(1)) * 1024


def _template_counterpart_exists(path: str, status: str) -> bool:
    target = _TEMPLATE / path
    if status == "generic as-is":
        return target.is_file()
    if status == "generic templated":
        jinja_target = target.with_name(target.name + ".jinja")
        return jinja_target.is_file() or target.is_file()
    raise AssertionError(f"unrecognised export status {status!r} for {path}")


class TestTemplateManifestParity:
    """No second hand-maintained copy of the manifest's file list (architect review B4)."""

    def test_manifest_lists_at_least_one_layer0_and_one_layer2_file(self) -> None:
        entries = _manifest_layer0_and_layer2_entries()
        assert len(entries) > 30, "manifest section parsing likely broke (#570 test bug, not RED)"

    def test_every_layer0_and_layer2_file_has_a_template_counterpart(self) -> None:
        missing = [
            f"{path} ({status})"
            for path, status in _manifest_layer0_and_layer2_entries()
            if not _template_counterpart_exists(path, status)
        ]
        assert not missing, "missing template counterpart for:\n" + "\n".join(sorted(missing))

    def test_copier_yml_pins_templates_suffix(self) -> None:
        copier_yml = _TEMPLATE / "copier.yml"
        assert copier_yml.is_file(), "templates/agent-process/copier.yml does not exist yet"
        content = copier_yml.read_text(encoding="utf-8")
        assert '_templates_suffix: ".jinja"' in content


def _run_copier_copy(dest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python", "-m", "copier", "copy", "-f", str(_TEMPLATE), str(dest)],
        cwd=_REPO,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def rendered_payload(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("agent_process_template") / "out"
    result = _run_copier_copy(dest)
    assert result.returncode == 0, result.stderr
    return dest


class TestTemplateRenders:
    """Render check (architect review B2): copier must not corrupt f-string literals."""

    def test_gh_endpoint_fstring_braces_survive_rendering(self, rendered_payload: Path) -> None:
        rendered = rendered_payload / "scripts" / "check_branch_protection.py"
        assert rendered.is_file(), "rendered payload is missing check_branch_protection.py"
        content = rendered.read_text(encoding="utf-8")
        assert "{{owner}}" in content
        assert "{{repo}}" in content

    def test_architecture_docs_fit_the_manifest_size_budget(self, rendered_payload: Path) -> None:
        rendered_size = sum(
            (rendered_payload / "docs" / "architecture" / name).stat().st_size
            for name in ("agent-process.md", "principles.md")
        )
        assert rendered_size <= _exported_core_size_budget()

    def test_copier_records_answers_for_a_future_update(self, rendered_payload: Path) -> None:
        answers = rendered_payload / ".copier-answers.yml"
        assert answers.is_file(), "Copier output must retain its update metadata"
        assert "_src_path:" in answers.read_text(encoding="utf-8")

    def test_no_citation_survives_in_rendered_payload(self, rendered_payload: Path) -> None:
        offenders = []
        for path in rendered_payload.rglob("*"):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if (
                _CITATION.search(content)
                or _LINKED_CITATION.search(content)
                or _ABS_ISSUE_URL.search(content)
                or _SOURCE_REPOSITORY_URL.search(content)
            ):
                offenders.append(str(path.relative_to(rendered_payload)))
        assert not offenders, f"citation survived export in: {offenders}"

    def test_exported_adrs_do_not_contain_stripped_citation_holes(
        self, rendered_payload: Path
    ) -> None:
        malformed = []
        for path in (rendered_payload / "docs" / "adr").glob("*.md"):
            content = path.read_text(encoding="utf-8")
            if "Issue: []." in content or "on PR ." in content or " in ." in content:
                malformed.append(path.name)
        assert not malformed, f"citation stripping left malformed ADR prose: {malformed}"

    def test_no_relative_markdown_link_dangles_in_rendered_payload(
        self, rendered_payload: Path
    ) -> None:
        link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
        dangling = []
        for path in rendered_payload.rglob("*.md"):
            content = path.read_text(encoding="utf-8")
            for target in link_re.findall(content):
                if target.startswith(("http://", "https://")):
                    continue
                target_path = target.split("#", 1)[0]
                if not target_path:
                    continue
                resolved = (path.parent / target_path).resolve()
                if not resolved.is_file():
                    dangling.append(f"{path.relative_to(rendered_payload)} -> {target}")
        assert not dangling, f"dangling relative links in rendered payload: {dangling}"
