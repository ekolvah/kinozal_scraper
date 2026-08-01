"""Гарды вокруг required status checks ветки `main` (#436).

Настоящий enforcement живёт в конфигурации GitHub **вне репо**: до #436 воркфлоу `pr-link`
существовал, краснел в UI и при этом ничего не блокировал, потому что не входил в
`required_status_checks.contexts`. Класс дефекта — presence ≠ correctness.

Здесь три независимых слоя:

* `TestDriftDetection` / `TestProtectionFetch` — чистая половина `scripts/check_branch_protection.py`
  (сравнение «объявлено ↔ фактически» и разделение «дрейф» vs «отказ инструмента», §IV).
  Сетевой прогон в CI недостижим: у `GITHUB_TOKEN` нет скоупа `administration`, а classic branch
  protection не виден через ruleset-эндпоинт — запись AD в `coverage-gaps.md`.
* `TestDeclarationMatchesWorkflows` — оффлайн-половина: объявление в скрипте сверяется с реальными
  воркфлоу. Сравнение идёт с **эффективным именем check-run'а** (`name:` джоба, иначе его ключ):
  переименование джоба иначе оставило бы required-контекст навсегда в статусе «Expected», а при
  `enforce_admins: true` это заперло бы мёрдж, включая PR с починкой.
* `TestPrePushHook` — поведенческие тесты хука: он исполняется через `bash` во временном дереве
  со считающими вызовы заглушками. Grep по тексту здесь бесполезен — исходный дефект был в
  семантике `||`, а не в наличии строк.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.check_branch_protection import (
    NOT_REQUIRED,
    REQUIRED_CONTEXTS,
    contexts_from_protection,
    declaration_problems,
    fetch_protection,
    load_workflows,
    protection_drift,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_HOOK = _REPO_ROOT / ".githooks" / "pre-push"


class TestDriftDetection:
    """Чистое сравнение объявленного состава контекстов с фактическим."""

    def test_missing_required_context_is_drift(self) -> None:
        """Ровно дефект #436: объявлен `pr-link`, в GitHub его нет."""
        missing, unexpected = protection_drift(("quality",), ("quality", "pr-link"))
        assert missing == ["pr-link"]
        assert unexpected == []

    def test_extra_context_in_github_is_drift(self) -> None:
        """Незаявленный контекст — тоже расхождение: канон состава живёт в репо."""
        missing, unexpected = protection_drift(("quality", "review"), ("quality",))
        assert missing == []
        assert unexpected == ["review"]

    def test_exact_match_is_clean(self) -> None:
        """Совпадение — пустой вердикт по обоим направлениям."""
        assert protection_drift(("quality", "pr-link"), ("quality", "pr-link")) == ([], [])

    def test_order_does_not_matter(self) -> None:
        """GitHub не гарантирует порядок в `checks`; сравнение — по множеству."""
        assert protection_drift(("pr-link", "quality"), ("quality", "pr-link")) == ([], [])


class TestProtectionFetch:
    """Сетевой слой: отказ инструмента (exit 2) не маскируется под вердикт (exit 0/1)."""

    @staticmethod
    def _fake_run(
        monkeypatch: pytest.MonkeyPatch, *, stdout: str | None, stderr: str | None, rc: int
    ) -> None:
        """Подменить `subprocess.run` фиксированным результатом `gh api`."""

        def _run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["gh"], returncode=rc, stdout=stdout, stderr=stderr
            )

        monkeypatch.setattr(subprocess, "run", _run)

    def test_gh_failure_exits_2_not_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ненулевой rc у `gh` — инфра-сбой, а не «дрейфа нет» и не «дрейф есть»."""
        self._fake_run(monkeypatch, stdout="", stderr="HTTP 403", rc=1)
        with pytest.raises(SystemExit) as exc:
            fetch_protection()
        assert exc.value.code == 2

    def test_none_stdout_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Сломанный захват вывода (#364/#410) — тоже инфра-сбой, не пустой ответ."""
        self._fake_run(monkeypatch, stdout=None, stderr=None, rc=0)
        with pytest.raises(SystemExit) as exc:
            fetch_protection()
        assert exc.value.code == 2

    def test_malformed_json_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Небитый rc, но нераспарсиваемое тело — тоже отказ инструмента."""
        self._fake_run(monkeypatch, stdout="<html>proxy</html>", stderr="", rc=0)
        with pytest.raises(SystemExit) as exc:
            fetch_protection()
        assert exc.value.code == 2

    def test_gh_timeout_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Зависший `gh` не имеет права повесить pre-push без вывода."""

        def _hang(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

        monkeypatch.setattr(subprocess, "run", _hang)
        with pytest.raises(SystemExit) as exc:
            fetch_protection()
        assert exc.value.code == 2

    def test_absent_required_status_checks_is_drift_not_infra(self) -> None:
        """Снесённый protection — самый вероятный реальный сценарий, это дрейф, не сбой."""
        assert contexts_from_protection({}) == ()
        assert contexts_from_protection({"required_status_checks": {}}) == ()

    def test_null_required_status_checks_is_drift_not_crash(self) -> None:
        """Явный JSON `null` — не то же, что отсутствующий ключ; трейсбек дал бы код 1."""
        assert contexts_from_protection({"required_status_checks": None}) == ()
        assert contexts_from_protection({"required_status_checks": {"checks": None}}) == ()

    def test_contexts_are_read_from_the_checks_field(self) -> None:
        """Читаем недеприкейченную форму `checks[*].context`, ту же, что пишем."""
        payload = {
            "required_status_checks": {
                "strict": True,
                "checks": [{"context": "quality", "app_id": 15368}, {"context": "pr-link"}],
            }
        }
        assert contexts_from_protection(payload) == ("quality", "pr-link")

    def test_actual_contexts_are_printed_on_success(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Фактический список печатается всегда — это и есть «воспроизводимый способ увидеть»."""
        from scripts.check_branch_protection import main

        payload = {
            "required_status_checks": {"checks": [{"context": c} for c in REQUIRED_CONTEXTS]}
        }
        self._fake_run(monkeypatch, stdout=json.dumps(payload), stderr="", rc=0)
        main([])
        printed = capsys.readouterr().out
        for context in REQUIRED_CONTEXTS:
            assert context in printed


class TestDeclarationMatchesWorkflows:
    """Оффлайн-половина: объявление в скрипте не разъезжается с воркфлоу репо."""

    def test_every_declared_context_is_a_real_job(self) -> None:
        """Реальные воркфлоу + реальное объявление — расхождений нет.

        Непустота объявления проверяется отдельно: пустой список удовлетворил бы
        всё остальное вакуумно и молча ничего не гарантировал бы (§IV).
        """
        assert REQUIRED_CONTEXTS, "пустое объявление молча не гарантирует ничего"
        assert (
            declaration_problems(load_workflows(_WORKFLOWS), REQUIRED_CONTEXTS, NOT_REQUIRED) == []
        )

    def test_every_pull_request_job_is_declared_or_excluded(self) -> None:
        """Новый PR-джоб обязан быть либо required, либо исключённым с причиной."""
        workflows = {
            "new.yml": {"on": {"pull_request": None}, "jobs": {"fresh-gate": {}}},
        }
        problems = declaration_problems(workflows, ("fresh-gate",), {})
        assert problems == []

        problems = declaration_problems(workflows, (), {})
        assert len(problems) == 1
        assert "fresh-gate" in problems[0]

    def test_excluded_job_needs_a_reason(self) -> None:
        """Исключение без причины — забытое решение, а не принятое."""
        workflows = {"new.yml": {"on": {"pull_request": None}, "jobs": {"advisory": {}}}}
        assert declaration_problems(workflows, (), {"advisory": "не блокирует fork-PR"}) == []
        assert declaration_problems(workflows, (), {"advisory": ""}) != []

    def test_declared_context_without_a_job_is_a_problem(self) -> None:
        """Объявленный контекст без джоба — вечный «Expected», мёрдж заперт навсегда."""
        workflows = {"new.yml": {"on": {"pull_request": None}, "jobs": {"gate": {}}}}
        problems = declaration_problems(workflows, ("gate", "ghost"), {})
        assert len(problems) == 1
        assert "ghost" in problems[0]

    def test_job_name_override_defines_the_context(self) -> None:
        """Контекст — это `name:` джоба, если он задан, а не ключ джоба."""
        workflows = {
            "new.yml": {"on": {"pull_request": None}, "jobs": {"gate": {"name": "Gate (strict)"}}}
        }
        assert declaration_problems(workflows, ("Gate (strict)",), {}) == []
        assert declaration_problems(workflows, ("gate",), {}) != []

    def test_matrix_job_cannot_be_declared_as_bare_context(self) -> None:
        """Матрица размножает контекст в `job (value)` — голое имя никогда не отчитается."""
        workflows = {
            "new.yml": {
                "on": {"pull_request": None},
                "jobs": {"gate": {"strategy": {"matrix": {"python": ["3.12", "3.13"]}}}},
            }
        }
        problems = declaration_problems(workflows, ("gate",), {})
        assert len(problems) == 1
        assert "matrix" in problems[0].lower()

    def test_yaml_boolean_on_key_is_understood(self) -> None:
        """YAML 1.1 читает голое `on:` как `True` — джоб не должен из-за этого потеряться."""
        workflows = {"new.yml": {True: {"pull_request": None}, "jobs": {"gate": {}}}}
        assert declaration_problems(workflows, (), {}) != []

    def test_trigger_filter_on_declared_context_is_a_problem(self) -> None:
        """`paths`/`branches` на триггере — контекст отчитается не на каждом PR."""
        for filter_key in ("paths", "paths-ignore", "branches", "branches-ignore"):
            workflows = {
                "new.yml": {
                    "on": {"pull_request": {filter_key: ["src/**"]}},
                    "jobs": {"gate": {}},
                }
            }
            problems = declaration_problems(workflows, ("gate",), {})
            assert len(problems) == 1, filter_key
            assert filter_key in problems[0]

    def test_unfiltered_trigger_is_clean(self) -> None:
        """`types:` фильтром не является — он сужает события, а не набор PR."""
        workflows = {
            "new.yml": {
                "on": {"pull_request": {"types": ["opened", "edited"]}},
                "jobs": {"gate": {}},
            }
        }
        assert declaration_problems(workflows, ("gate",), {}) == []

    def test_stale_exclusion_is_a_problem(self) -> None:
        """Исключение, переживающее свой удалённый джоб, молча ничего не значит."""
        workflows = {"new.yml": {"on": {"pull_request": None}, "jobs": {"gate": {}}}}
        problems = declaration_problems(workflows, ("gate",), {"ghost": "причина есть"})
        assert len(problems) == 1
        assert "ghost" in problems[0]

    def test_context_in_both_lists_is_a_problem(self) -> None:
        """Контекст и required, и исключённый — противоречие, а не уточнение."""
        workflows = {"new.yml": {"on": {"pull_request": None}, "jobs": {"gate": {}}}}
        problems = declaration_problems(workflows, ("gate",), {"gate": "причина есть"})
        assert len(problems) == 1
        assert "gate" in problems[0]

    def test_duplicate_effective_job_name_is_a_problem(self) -> None:
        """Одно имя check-run'а на два джоба — неопределённость, не деталь."""
        workflows = {
            "a.yml": {"on": {"pull_request": None}, "jobs": {"gate": {}}},
            "b.yml": {"on": {"pull_request": None}, "jobs": {"other": {"name": "gate"}}},
        }
        problems = declaration_problems(workflows, ("gate",), {})
        assert any("более чем одному" in p for p in problems)

    def test_yaml_extension_workflow_is_loaded(self, tmp_path: Path) -> None:
        """GitHub принимает и `.yaml`; гард, слепой к нему, зеленел бы вакуумно."""
        (tmp_path / "a.yml").write_text(
            "on:\n  pull_request:\njobs:\n  one: {}\n", encoding="utf-8"
        )
        (tmp_path / "b.yaml").write_text(
            "on:\n  pull_request:\njobs:\n  two: {}\n", encoding="utf-8"
        )
        loaded = load_workflows(tmp_path)
        assert set(loaded) == {"a.yml", "b.yaml"}
        problems = declaration_problems(loaded, (), {})
        assert len(problems) == 2

    def test_empty_workflow_file_does_not_crash(self, tmp_path: Path) -> None:
        """Пустой файл `safe_load` отдаёт как `None` — обход обязан пережить это."""
        (tmp_path / "empty.yml").write_text("", encoding="utf-8")
        assert load_workflows(tmp_path) == {"empty.yml": {}}
        assert declaration_problems(load_workflows(tmp_path), (), {}) == []

    def test_non_pull_request_workflow_is_ignored(self) -> None:
        """Cron-джоб не может быть required-контекстом PR — он и не обязан объявляться."""
        workflows = {"cron.yml": {"on": {"schedule": [{"cron": "0 5 * * *"}]}, "jobs": {"run": {}}}}
        assert declaration_problems(workflows, (), {}) == []


class TestPrePushHook:
    """Хук исполняется по-настоящему: заглушки считают вызовы, порядок и stderr."""

    _STUB = (
        "#!/usr/bin/env bash\n"
        'printf "%s|%s\\n" "$STUB_ID" "$*" >> "$PWD/hook-calls.log"\n'
        'if [ "$1" = "-c" ]; then exit 0; fi\n'
        'name=$(basename "$1")\n'
        'echo "stderr-from-$name" >&2\n'
        'if [ -f "rc-$name" ]; then exit "$(cat "rc-$name")"; fi\n'
        "exit 0\n"
    )

    @staticmethod
    def _stub(path: Path, stub_id: str) -> None:
        """Положить исполняемую заглушку интерпретатора, помечающую себя `stub_id`."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            TestPrePushHook._STUB.replace("$STUB_ID", stub_id), encoding="utf-8", newline="\n"
        )
        path.chmod(0o755)

    @staticmethod
    def _bash() -> str:
        """Абсолютный путь к `bash` — тесту ниже нужно урезать PATH, не потеряв оболочку."""
        bash = shutil.which("bash")
        assert bash, "bash недоступен: хук нечем исполнить, гард выродился бы в grep"
        return bash

    @classmethod
    def _run(
        cls, tmp_path: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Исполнить реальный `.githooks/pre-push` во временном дереве."""
        return subprocess.run(
            [cls._bash(), _HOOK.as_posix()],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )

    @staticmethod
    def _gate_calls(tmp_path: Path) -> list[str]:
        """Вызовы гейтов (проба интерпретатора `-c` в лог не попадает)."""
        log = tmp_path / "hook-calls.log"
        if not log.exists():
            return []
        return [line for line in log.read_text(encoding="utf-8").splitlines() if ".py" in line]

    def test_runs_each_gate_exactly_once(self, tmp_path: Path) -> None:
        """Оба гейта вызываются по одному разу — без повторов от старой `||`-цепочки."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        result = self._run(tmp_path)
        calls = self._gate_calls(tmp_path)
        assert result.returncode == 0, result.stderr
        assert sum("check_branch_protection.py" in c for c in calls) == 1
        assert sum("ci_check.py" in c for c in calls) == 1

    def test_protection_probe_runs_before_ci_check(self, tmp_path: Path) -> None:
        """Дешёвая сетевая проверка идёт первой: дрейф не должен стоить прогона ci_check."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        self._run(tmp_path)
        calls = self._gate_calls(tmp_path)
        assert "check_branch_protection.py" in calls[0]
        assert "ci_check.py" in calls[1]

    def test_drift_blocks_push_without_running_ci_check(self, tmp_path: Path) -> None:
        """Дрейф роняет push сразу, не оплачивая минуты ci_check."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        (tmp_path / "rc-check_branch_protection.py").write_text("1", encoding="utf-8")
        result = self._run(tmp_path)
        assert result.returncode != 0
        assert not any("ci_check.py" in c for c in self._gate_calls(tmp_path))

    def test_failing_gate_is_not_rerun_under_the_next_interpreter(self, tmp_path: Path) -> None:
        """Красный гейт — это вердикт, а не «интерпретатор не тот» (root cause #436)."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        self._stub(tmp_path / ".venv" / "bin" / "python", "bin")
        (tmp_path / "rc-ci_check.py").write_text("1", encoding="utf-8")
        result = self._run(tmp_path)
        calls = self._gate_calls(tmp_path)
        assert result.returncode != 0
        assert not any(c.startswith("bin|") for c in calls)
        assert sum("ci_check.py" in c for c in calls) == 1

    def test_gate_exit_code_is_propagated(self, tmp_path: Path) -> None:
        """`2` (инструмент не сработал) и `1` (дрейф) снаружи должны различаться."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        (tmp_path / "rc-check_branch_protection.py").write_text("2", encoding="utf-8")
        assert self._run(tmp_path).returncode == 2

    def test_gate_stderr_is_not_swallowed(self, tmp_path: Path) -> None:
        """`2>/dev/null` остаётся только на пробе интерпретатора, не на прогоне гейта."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        (tmp_path / "rc-ci_check.py").write_text("1", encoding="utf-8")
        result = self._run(tmp_path)
        assert "stderr-from-ci_check.py" in result.stderr

    def test_hook_has_no_crlf_in_the_working_tree(self) -> None:
        """CRLF в shebang'е убивает хук целиком — `.gitattributes` держит его LF-only.

        С `core.autocrlf=true` (дефолт Git for Windows) и без атрибута свежий клон получил бы
        `#!/usr/bin/env bash\\r`, bash упал бы с «bad interpreter», и pre-push гейт молча
        перестал бы запускаться.
        """
        assert b"\r\n" not in _HOOK.read_bytes()

    def test_missing_interpreter_fails_loudly(self, tmp_path: Path) -> None:
        """Ни один кандидат не найден — видимый отказ, а не тихо пропущенный гейт (§IV).

        **Preservation-гард**: сегодняшний хук это свойство уже держит (несуществующий
        интерпретатор роняет `bash` с ненулевым кодом), поэтому в RED-набор #436 тест не
        входит — он фиксирует то, что рефакторинг обязан не сломать.
        """
        env = {"PATH": str(Path(self._bash()).parent)}
        result = self._run(tmp_path, env=env)
        assert result.returncode != 0
        assert result.stderr.strip(), "молчаливый отказ хука неотличим от зелёного прогона"
