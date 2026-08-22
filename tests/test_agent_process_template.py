"""Guard tests for the copier template payload built from the export manifest (#570).

`docs/architecture/agent-process-export.md` is the single home of the Layer 0/2 file
list (§Manifest scope); these tests parse its tables directly rather than hard-coding
a second copy, mirroring `tests/test_agent_process.py`'s `_documented_change_class_matrix`
pattern for `change-classes.yaml`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_language import markdown_violations

_REPO = Path(__file__).resolve().parent.parent
_MANIFEST = _REPO / "docs" / "architecture" / "agent-process-export.md"
_TEMPLATE = _REPO / "templates" / "agent-process"

_CITATION = re.compile(r"#\d{2,4}\b")
_LINKED_CITATION = re.compile(r"\[#\d{2,4}\]\([^)]*\)")
_ABS_ISSUE_URL = re.compile(r"https://github\.com/ekolvah/kinozal_scraper/issues/\d+")
_SOURCE_REPOSITORY_URL = re.compile(r"https://github\.com/ekolvah/kinozal_scraper(?:/|\b)")
_SOURCE_PROVENANCE_ID = re.compile(r"(?<!/runs/)\b\d{8,}\b")
_STRIPPED_PROSE_HOLE = re.compile(
    r"(?:\([ \t]*,|,[ \t]+\)|\([ \t]*'s\b|"
    r"\b(?:in|on|by|from|with|after)[ \t]+\.(?:[ \t\r\n]|$)|"
    r"\bPR[ \t]+(?:\)|,)|\bthe[ \t]+/[ \t]+\w+|\"\"\":[ \t]*|"
    r"\([^()\r\n]*\b(?:see|root[ \t]+cause)[ \t]*\)|"
    r"\b(?:in|on|by|from|with|after)[ \t]+[,)]|"
    r"(?:^|\n)[ \t]*#[ \t]*(?:\)[ \t]*,|[).,:;](?=[ \t]|$)))"
)

# Cells whose secondary backtick token is relative to the primary path's own directory,
# not repo-root-relative (the manifest's skill "metadata sidecar" rows, e.g.
# "`.agents/skills/plan-issue/SKILL.md` (+ `agents/openai.yaml` metadata sidecar)").
_ROOTED_PREFIXES = ("docs/", "scripts/", ".agents/", ".github/", ".claude/", ".codex/", "tests/")
_TARGET_WIDE_LANGUAGE_POLICY_ARTIFACTS = (
    "scripts/check_language.py",
    "tests/test_language_policy.py",
    "docs/adr/0005-english-repository-documentation.md",
)


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


def _manifest_layer1_copier_entries() -> list[tuple[str, str]]:
    """Return (repo_relative_path, export_status) for Layer 1 copier files."""
    text = _manifest_text()
    entries: list[tuple[str, str]] = []
    layer1 = _section(text, "## Layer 1", "### Install order")
    for cells in _manifest_rows(layer1):
        channel = cells[2] if len(cells) > 3 else ""
        if channel == "copier":
            for path in _expand_file_cell(cells[0]):
                entries.append((path, cells[1]))
    return entries


def _exported_core_size_budget() -> int:
    match = re.search(
        r"cap the exported combined size of `agent-process\.md` \+ `principles\.md` at (\d+) KB",
        _manifest_text(),
    )
    assert match is not None, "export manifest no longer declares its core-size budget"
    return int(match.group(1)) * 1024


def _template_counterpart_exists(path: str, status: str, *, channel: str | None = None) -> bool:
    target = _TEMPLATE / path
    if channel == "copier" and path.startswith(".claude/"):
        conditional_claude_dir = "{% if claude_adapter_installed %}.claude{% endif %}"
        target = _TEMPLATE / conditional_claude_dir / path.removeprefix(".claude/")
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

    def test_layer1_copier_channel_files_have_template_counterparts(self) -> None:
        entries = _manifest_layer1_copier_entries()
        assert entries, "Layer 1 must declare at least one copier-channel file"
        missing = [
            f"{path} ({status})"
            for path, status in entries
            if not _template_counterpart_exists(path, status, channel="copier")
        ]
        assert not missing, "missing Layer 1 copier template counterpart for:\n" + "\n".join(
            sorted(missing)
        )

    def test_copier_yml_pins_templates_suffix(self) -> None:
        copier_yml = _TEMPLATE / "copier.yml"
        assert copier_yml.is_file(), "templates/agent-process/copier.yml does not exist yet"
        content = copier_yml.read_text(encoding="utf-8")
        assert '_templates_suffix: ".jinja"' in content


def _run_copier_copy(
    dest: Path, *, data_file: Path | None = None
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "copier", "copy", "-f"]
    if data_file is not None:
        command.extend(["--data-file", str(data_file)])
    command.extend([str(_TEMPLATE), str(dest)])
    return subprocess.run(
        command,
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


def _initialize_git_repository(path: Path) -> None:
    for command in (["git", "init", "--quiet"], ["git", "add", "--all"]):
        result = subprocess.run(command, cwd=path, capture_output=True, encoding="utf-8")
        assert result.returncode == 0, result.stderr


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

    def test_copier_records_source_and_answers(self, rendered_payload: Path) -> None:
        answers = rendered_payload / ".copier-answers.yml"
        assert answers.is_file(), "Copier output must retain its source and selected answers"
        content = answers.read_text(encoding="utf-8")
        assert "_src_path:" in content
        assert "template revision" not in content

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

    def test_citation_strip_leaves_no_malformed_prose(self, rendered_payload: Path) -> None:
        malformed = []
        for path in rendered_payload.rglob("*"):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if (
                "Issue: []." in content
                or _STRIPPED_PROSE_HOLE.search(content)
                or _SOURCE_PROVENANCE_ID.search(content)
            ):
                malformed.append(str(path.relative_to(rendered_payload)))
        assert not malformed, f"citation stripping left malformed prose: {malformed}"

    def test_exported_markdown_has_balanced_parentheses(self, rendered_payload: Path) -> None:
        unbalanced = []
        for path in rendered_payload.rglob("*.md"):
            content = path.read_text(encoding="utf-8")
            if content.count("(") != content.count(")"):
                unbalanced.append(str(path.relative_to(rendered_payload)))
        assert not unbalanced, f"exported Markdown has unbalanced parentheses: {unbalanced}"

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

    def test_default_render_omits_claude_adapter_files(self, rendered_payload: Path) -> None:
        assert not (rendered_payload / ".claude").exists()
        assert not [
            path for path in rendered_payload.iterdir() if "claude_adapter_installed" in path.name
        ]

    def test_default_render_omits_target_wide_language_policy(
        self, rendered_payload: Path
    ) -> None:
        present = [
            path for path in _TARGET_WIDE_LANGUAGE_POLICY_ARTIFACTS if (rendered_payload / path).exists()
        ]
        assert not present, f"target-wide language-policy artifacts were exported: {present}"

        ci_check = (rendered_payload / "scripts" / "ci_check.py").read_text(encoding="utf-8")
        assert '"language": check_language,' not in ci_check

    def test_rendered_markdown_remains_english_only(self, rendered_payload: Path) -> None:
        violations = [
            violation
            for path in rendered_payload.rglob("*.md")
            for violation in markdown_violations(
                path.read_text(encoding="utf-8"), path=str(path.relative_to(rendered_payload))
            )
        ]
        assert not violations, f"exported Markdown is not English-only: {violations}"

    def test_claude_adapter_rendered_markdown_is_export_safe(self, tmp_path: Path) -> None:
        data_file = tmp_path / "answers.yml"
        data_file.write_text("claude_adapter_installed: true\n", encoding="utf-8")
        rendered = tmp_path / "claude-adapter"
        copied = _run_copier_copy(rendered, data_file=data_file)
        assert copied.returncode == 0, copied.stderr

        present = [
            path for path in _TARGET_WIDE_LANGUAGE_POLICY_ARTIFACTS if (rendered / path).exists()
        ]
        assert not present, f"target-wide language-policy artifacts were exported: {present}"
        ci_check = (rendered / "scripts" / "ci_check.py").read_text(encoding="utf-8")
        assert '"language": check_language,' not in ci_check

        link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
        dangling = []
        for path in (rendered / ".claude").rglob("*.md"):
            content = path.read_text(encoding="utf-8")
            assert not _CITATION.search(content)
            assert content.count("(") == content.count(")")
            for target in link_re.findall(content):
                if target.startswith(("http://", "https://")):
                    continue
                target_path = target.split("#", 1)[0]
                if target_path and not (path.parent / target_path).resolve().is_file():
                    dangling.append(f"{path.relative_to(rendered)} -> {target}")
        assert not dangling, f"dangling relative links in Claude-adapter payload: {dangling}"

    def test_exported_text_does_not_claim_missing_test_guards(self, rendered_payload: Path) -> None:
        missing = []
        test_path_re = re.compile(r"`(tests/test_[\w_]+\.py)(?::[^`]*)?`")
        for path in rendered_payload.rglob("*"):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for test_path in test_path_re.findall(content):
                if not (rendered_payload / test_path).is_file():
                    missing.append(f"{path.relative_to(rendered_payload)} -> {test_path}")
        assert not missing, "exported text names absent test guards:\n" + "\n".join(missing)

    def test_default_rendered_payload_passes_its_quality_gate(
        self, rendered_payload: Path, tmp_path: Path
    ) -> None:
        """The documented default must work without source-only CI or adapters."""
        checked_payload = tmp_path / "checked-payload"
        shutil.copytree(rendered_payload, checked_payload)
        _initialize_git_repository(checked_payload)
        result = subprocess.run(
            [sys.executable, "scripts/ci_check.py"],
            cwd=checked_payload,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize(
        ("fixture_routes", "case_name"),
        (("[github]", "github_only"), ("[stdin]", "stdin_only")),
    )
    def test_non_default_template_branches_compile_and_format(
        self, tmp_path: Path, fixture_routes: str, case_name: str
    ) -> None:
        """Exercise both sides of every current conditional Jinja branch."""
        claude_files = _manifest_layer1_copier_entries()
        assert claude_files, "Layer 1 must declare copier-channel files for this branch"
        data_file = tmp_path / "answers.yml"
        data_file.write_text(
            "claude_adapter_installed: true\n"
            "not_required_contexts:\n"
            "  advisory: target-specific workflow\n"
            f"fixture_routes: {fixture_routes}\n",
            encoding="utf-8",
        )
        rendered = tmp_path / case_name
        copied = _run_copier_copy(rendered, data_file=data_file)
        assert copied.returncode == 0, copied.stderr
        missing = [path for path, _status in claude_files if not (rendered / path).is_file()]
        assert not missing, f"Claude-adapter render is missing: {missing}"
        settings = json.loads((rendered / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert "SessionStart" not in settings["hooks"]
        assert set(settings["hooks"]) == {"PreToolUse", "PostToolUse"}

        for command in (
            [sys.executable, "-m", "compileall", "-q", "scripts", "tests"],
            [sys.executable, "-m", "ruff", "format", "--check", "."],
            [sys.executable, "-m", "ruff", "check", "."],
        ):
            result = subprocess.run(
                command,
                cwd=rendered,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            assert result.returncode == 0, result.stdout + result.stderr
