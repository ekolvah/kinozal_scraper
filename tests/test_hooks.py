"""Unit tests for the session-level PostToolUse hook (`scripts/hooks.py`, #281).

The hook fires after every Edit/Write and dispatches two cheap checks in one
process: ruff (check-only) on `*.py`, and a pip-compile reminder on
`requirements*.in`. The deterministic decision logic lives in pure functions
(`plan_checks`, `classify_ruff_result`, `pipcompile_signal`, `exit_code`) so it
can be tested without spawning real ruff — the subprocess call is a thin I/O
wrapper (mirrors the `scripts/check_red.py` pure-function + thin-`main` split).

§IV note: a malformed/empty payload is a silent no-op (do not red every edit on
a payload bug), but a *ruff exec failure* (not installed / internal error) must
be a VISIBLE marker — otherwise the agent believes instant-lint is running when
it is not (a silent setup degradation).
"""

from __future__ import annotations

from scripts.hooks import (
    classify_ruff_result,
    exit_code,
    memory_write_signal,
    pipcompile_signal,
    plan_checks,
    run_on_edit,
)


def _payload(path: str | None) -> dict:
    if path is None:
        return {"tool_input": {}}
    return {"tool_input": {"file_path": path}}


class TestOnEditDispatch:
    def test_python_file_plans_ruff_check(self) -> None:
        assert plan_checks(_payload("src/kinozal_scraper/soldout_pipeline.py")) == ["ruff"]

    def test_non_python_skips_ruff(self) -> None:
        assert plan_checks(_payload("docs/architecture/ci.md")) == []
        assert plan_checks(_payload(".claude/settings.json")) == []

    def test_malformed_payload_silent_noop(self) -> None:
        # No file_path (and empty payload) → nothing planned, exit 0, no stderr.
        assert plan_checks(_payload(None)) == []
        assert plan_checks({}) == []
        code, stderr = run_on_edit({}, ruff_runner=_never_called)
        assert code == 0
        assert stderr == ""

    def test_run_on_edit_python_wires_dispatch_classify_exit(self) -> None:
        # End-to-end seam: a .py edit + a stubbed ruff run flows
        # plan_checks → ruff_runner → classify_ruff_result → exit_code as a whole.
        calls: list[str] = []

        def _stub(file_path: str) -> tuple[int, str]:
            calls.append(file_path)
            return 1, f"{file_path}:1:1: F401 unused import"

        code, stderr = run_on_edit(_payload("src/x.py"), ruff_runner=_stub)
        assert calls == ["src/x.py"]  # dispatch reached the runner with the edited path
        assert code == 2  # lint finding surfaces
        assert "F401" in stderr

    def test_run_on_edit_python_clean_is_silent(self) -> None:
        code, stderr = run_on_edit(_payload("src/x.py"), ruff_runner=lambda _f: (0, ""))
        assert code == 0
        assert stderr == ""


class TestRuffSignal:
    def test_lint_findings_surface_exit_2(self) -> None:
        # ruff returncode 1 = lint findings → visible marker, exit 2 (feedback to agent).
        sig = classify_ruff_result(1, "src/x.py:1:1: F401 unused import")
        assert sig is not None
        assert sig.kind == "lint"
        assert exit_code([sig]) == 2

    def test_ruff_exec_failure_is_visible_not_silent(self) -> None:
        # ruff returncode >=2 = ruff itself broke (bad config / not runnable).
        # Must be a VISIBLE, DISTINCT marker — not swallowed as "lint clean".
        sig = classify_ruff_result(2, "error: unknown option")
        assert sig is not None
        assert sig.kind == "setup_broken"
        assert sig.kind != "lint"
        assert exit_code([sig]) == 2

    def test_clean_returns_no_marker(self) -> None:
        assert classify_ruff_result(0, "") is None
        assert exit_code([]) == 0


class TestPipCompileGuard:
    def test_requirements_in_flagged(self) -> None:
        assert plan_checks(_payload("requirements.in")) == ["pipcompile"]
        assert plan_checks(_payload("requirements-dev.in")) == ["pipcompile"]
        sig = pipcompile_signal("requirements.in")
        assert "pip-compile" in sig.message
        assert exit_code([sig]) == 2

    def test_requirements_txt_ignored(self) -> None:
        # .txt is the generated lockfile, not the source — no reminder.
        assert plan_checks(_payload("requirements.txt")) == []
        assert plan_checks(_payload("requirements-dev.txt")) == []


class TestMemoryWriteGuard:
    """#353: запись в out-of-repo agent-память (`.claude/projects/<slug>/memory/`) —
    детерминируемый governance-триггер политики Memory↔repo, вынесенный из прозы в
    pure-предикат по пути (как `_is_python`/`_is_requirements_in`). Сигнал —
    checkpoint-вопрос (reminder, exit 2), НЕ PreToolUse-блок: false-positive на
    легит машинно-специфичную память допустим by-design (семантику не скриптуем)."""

    _MEM = (
        "C:/Users/jadow/.claude/projects/"
        "C--Users-jadow-PycharmProjects-kinozal-scraper/memory/some_fact.md"
    )

    def test_memory_path_flags_memory_write(self) -> None:
        assert plan_checks(_payload(self._MEM)) == ["memory_write"]

    def test_memory_write_surfaces_exit_2(self) -> None:
        # Сигнал = видимая аномалия (§IV), exit 2 → stderr доходит до агента; ruff
        # не зовётся (memory-ветка до _is_python), поэтому _never_called безопасен.
        code, stderr = run_on_edit(_payload(self._MEM), ruff_runner=_never_called)
        assert code == 2
        assert stderr != ""
        sig = memory_write_signal(self._MEM)
        assert sig.kind == "memory_write"

    def test_windows_backslash_path(self) -> None:
        # Грабля путей Windows: payload может нести backslash-путь — нормализуется.
        p = r"C:\Users\jadow\.claude\projects\slug\memory\bar.md"
        assert plan_checks(_payload(p)) == ["memory_write"]

    def test_memory_index_root_file_flagged(self) -> None:
        # MEMORY.md в корне memory-каталога — trailing-`/` не отсекает корневой файл.
        p = "C:/Users/jadow/.claude/projects/slug/memory/MEMORY.md"
        assert plan_checks(_payload(p)) == ["memory_write"]

    def test_non_memory_subdir_of_projects_not_flagged(self) -> None:
        # Специфичность `/memory/`, а не просто `projects/`: каталог projects несёт
        # и другое (сессионные логи). Страхует границу от ослабления регекса.
        p = "C:/Users/jadow/.claude/projects/slug/other/f.md"
        assert plan_checks(_payload(p)) == []

    def test_repo_paths_not_memory(self) -> None:
        # Repo-файлы (в т.ч. repo-`.claude/`) не триггерят memory-сигнал — dispatch цел.
        assert plan_checks(_payload("src/x.py")) == ["ruff"]
        assert plan_checks(_payload("docs/architecture/project-map.md")) == []
        assert plan_checks(_payload(".claude/rules/mindset.md")) == []


def _never_called(_file: str) -> tuple[int, str]:
    raise AssertionError("ruff_runner must not run when nothing is planned")
