"""Гард на вывод subprocess: два правила — кодировка (#364) и дефолты (#410).

`subprocess.run(..., text=True, capture_output=True)` **без** `encoding` под Windows
декодит вывод дочернего процесса ANSI-кодовой страницей (cp1252). На кириллице это
`UnicodeDecodeError` в потоке-читателе — и дальше самое неприятное: поток умирает,
буфер остаётся пустым, а `Popen._communicate` возвращает `stdout[0] if stdout else
None`, то есть **`stdout is None`**. Отсюда грабля #109 («может вернуть `stdout=None`
несмотря на `text=True`») — она не отдельное явление Windows+git-bash, а симптом
этого дефекта; рассыпанные по репозиторию `(result.stdout or "")` были
маскировавшими его workaround'ами и **сняты в #410** — теперь их запрещает второе
правило этого файла (`find_output_defaults`), а проверка на `None` живёт в
`_run`-seam'е каждого скрипта.

Живой случай 28.07.2026: PostToolUse-хук отрапортовал «ruff found issues», но **текст
находки потерялся вместе с умершим потоком**. Инструмент видимости ослеп — §IV внутри
того, что §IV и обеспечивает.

**Почему гард, а не «дописать аргумент».** 7 call-site из 9 про флаг вспомнили, 2 —
нет; это третий заход на тот же класс (#109 → #125 → #364). Корректность, держащаяся
на памяти автора, по канону `mindset.md` («скрипты > инструкции») крепится exit-code'ом.

**Границы гарда — реальные, не отговорки** (полный разбор с причинами — ledger
`docs/architecture/testing.md#consciously-accepted-coverage-gaps`):

- **Половина ребёнка не покрывается.** `encoding` у родителя — только половина
  контракта: дочерний Python пишет в pipe своей ANSI-кодировкой, если не запущен с
  `-X utf8`/`PYTHONUTF8` (репо это знает — `ci_check.py` так зовёт detect-secrets).
  Такой call-site гард пометит зелёным, хотя он сломан.
- **Стандартного правила нет.** Ни ruff, ни bandit, ни pylint не имеют правила на
  encoding у `subprocess` (ruff `PLW1514` — только про `open()`). Записано, чтобы
  прецедент #237 («стандартные тулы > велосипеды») не ре-литигировали заново.
- **Алиас-импорт закрыт, а не задокументирован**: `test_subprocess_helpers_are_not_imported_by_name`
  запрещает `from subprocess import run`, иначе анализ по имени обходится тремя буквами.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
# Три директории — сознательный скоуп, а не недосмотр: сегодня весь трекаемый
# Python лежит в них (проверено), а сканировать от корня опасно — рядом живут
# sibling-venv'ы (`.venv-phoenix` и т.п.), на которые уже наступали в #345.
# Расширять — когда появится трекаемый `.py` вне их, и тогда явным списком.
_SCANNED_DIRS = ("scripts", "src", "tests")

# Функции subprocess, которые запускают процесс и умеют захватывать его вывод.
_SPAWNERS = frozenset({"run", "check_output", "Popen"})

# kwarg'и, любой из которых переводит поток в ТЕКСТОВЫЙ режим, то есть включает
# декодирование на стороне родителя. `errors` — тоже: по докам subprocess он
# подразумевает текстовый режим, и без `encoding` там локальная кодировка.
_TEXT_MODE_KWARGS = frozenset({"text", "universal_newlines", "encoding", "errors"})

# Атрибуты `CompletedProcess`, дефолт на которых подменяет отказ захвата пустым
# значением (#410). Дом инварианта — здесь: общий seam-хелпер построить нельзя
# (репо-корень не на `sys.path` при `python scripts/foo.py` — см.
# `scripts/issue_branch.py`), а правило гарда, в отличие от хелпера, ещё и мешает
# написать дефолт заново.
_OUTPUT_ATTRS = frozenset({"stdout", "stderr"})


def find_output_defaults(source: str, label: str) -> list[str]:
    """`<что-то>.stdout or <дефолт>` — подмена отказа захвата пустым значением.

    Правило намеренно узкое: смотрит на **атрибут** `.stdout`/`.stderr` слева от
    `or`, а не на `or` вообще. Широкое правило флагало бы легитимные дефолты
    (`os.environ.get(...) or ""`), и его пришлось бы ослаблять — а глушить
    ассерт нечем, `noqa` у него нет."""
    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        head = node.values[0]
        if isinstance(head, ast.Attribute) and head.attr in _OUTPUT_ATTRS:
            violations.append(f"{label}:{node.lineno}: `.{head.attr} or ...` default")
    return violations


def _kwargs(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}


def _is_literal_false(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _is_pipe(node: ast.expr) -> bool:
    # `subprocess.PIPE` или голое `PIPE` — второе живо только вместе с
    # алиас-импортом, который отдельно запрещён ниже.
    return (isinstance(node, ast.Attribute) and node.attr == "PIPE") or (
        isinstance(node, ast.Name) and node.id == "PIPE"
    )


def _is_explicit_no_capture(node: ast.expr) -> bool:
    """`stdout=None` / `stdout=subprocess.DEVNULL` — явный отказ от захвата."""
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    return isinstance(node, ast.Attribute) and node.attr == "DEVNULL"


def _captures_output(func_name: str, kwargs: dict[str, ast.expr]) -> bool:
    """Захватывает ли вызов вывод ребёнка (и значит будет его декодировать).

    Везде **fail-closed**: неизвестное (не-литеральное) значение считаем захватом.
    Форма `capture_output=capture` живёт в репо сегодня (`new_branch.py:29`), а
    трактовать переменную как «не захватывает» значило бы пропускать сломанный
    call-site — ложно-зелёный гард хуже отсутствующего.

    `check_output` захватывает stdout **по определению** (он его и возвращает) и
    `capture_output` не принимает вовсе — без этой ветки он числился бы в
    `_SPAWNERS`, но не мог быть помечен никогда: ложно-зелёный ровно той формы,
    против которой гард и написан."""
    if func_name == "check_output":
        return True
    capture = kwargs.get("capture_output")
    if capture is not None and not _is_literal_false(capture):
        return True
    return any(
        _is_pipe(kwargs[stream]) or not _is_explicit_no_capture(kwargs[stream])
        for stream in ("stdout", "stderr")
        if stream in kwargs
    )


def find_violations(source: str, label: str) -> list[str]:
    """Вызовы `subprocess`, которые декодят вывод ребёнка без явного encoding.

    Нарушение := захватывает вывод И в текстовом режиме И без `encoding`. Бинарный
    режим (никаких текстовых kwarg'ов) не нарушение: там декодирует уже вызывающий,
    сознательно."""
    tree = ast.parse(source)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Сверяем только имя атрибута, не получателя: `runner.run(...)` тоже будет
        # помечен. Это осознанно — направление ошибки безопасное (fail-closed), а
        # разбор получателя потребовал бы трассировки имён. Ложных срабатываний в
        # репо сегодня нет; появится — глушить нечем (у ассерта нет `noqa`), и это
        # тоже осознанно: гард правится, а не затыкается.
        if not (isinstance(func, ast.Attribute) and func.attr in _SPAWNERS):
            continue
        kwargs = _kwargs(node)
        if not _captures_output(func.attr, kwargs):
            continue
        text_mode = [
            name
            for name in _TEXT_MODE_KWARGS & kwargs.keys()
            if not _is_literal_false(kwargs[name])
        ]
        if not text_mode:
            continue
        if "encoding" in kwargs:
            continue
        violations.append(f"{label}:{node.lineno}: {func.attr}(...) without encoding")
    return violations


def _scanned_files() -> list[Path]:
    return sorted(
        path
        for directory in _SCANNED_DIRS
        for path in (_REPO / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _imports_subprocess_by_name(source: str) -> list[str]:
    """Имена, импортированные из `subprocess` напрямую — они прячут вызов от анализа.

    Закрываем импорт, а не трассируем алиасы: граница, снимаемая тремя строками,
    не заслуживает того, чтобы быть записанной прозой (`mindset.md`)."""
    return [
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess"
        for alias in node.names
        if alias.name in _SPAWNERS
    ]


class TestAnalyzer:
    """Unit'ы анализатора на inline-исходниках.

    Без них гард ложно-зелёный по построению: анализатор, безусловно возвращающий
    `[]`, прошёл бы и repo-скан (после фикса нарушений нет), и проверку отсутствия
    ложных срабатываний. Тогда тест-набор не отличал бы рабочий гард от заглушки."""

    def test_capturing_call_without_encoding_is_flagged(self) -> None:
        source = "import subprocess\nsubprocess.run(cmd, text=True, capture_output=True)\n"
        violations = find_violations(source, "sample.py")
        assert violations, "a capturing text-mode call without `encoding` must be flagged"
        assert "sample.py:2" in violations[0], (
            f"the violation must name file:line so it is actionable, got {violations[0]!r}"
        )

    def test_non_capturing_call_is_not_flagged(self) -> None:
        # `scripts/ci_check.py:24` — вывод идёт в консоль, родитель его не декодит.
        # Ложное срабатывание здесь особенно дорого: у pytest-ассерта нет `noqa`,
        # глушить нечем — гард обязан быть точным, а не глушимым.
        source = "import subprocess\nsubprocess.run(cmd)\n"
        assert find_violations(source, "sample.py") == []

    def test_non_literal_capture_flag_counts_as_capturing(self) -> None:
        # Форма существует сегодня: `scripts/new_branch.py:29` передаёт переменную.
        # Считать захватом всё, кроме литерального False.
        source = "import subprocess\nsubprocess.run(cmd, text=True, capture_output=capture)\n"
        assert find_violations(source, "sample.py")

    def test_errors_kwarg_alone_counts_as_text_mode(self) -> None:
        source = "import subprocess\nsubprocess.run(cmd, errors='replace', capture_output=True)\n"
        assert find_violations(source, "sample.py")

    def test_pipe_stdout_counts_as_capturing(self) -> None:
        source = (
            "import subprocess\n"
            "subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True)\n"
        )
        assert find_violations(source, "sample.py")

    def test_check_output_captures_implicitly(self) -> None:
        # `check_output` не принимает `capture_output` — он возвращает stdout. Без
        # отдельной ветки он числился бы в списке отслеживаемых, но не мог быть
        # помечен никогда.
        source = "import subprocess\nsubprocess.check_output(cmd, text=True)\n"
        assert find_violations(source, "sample.py")

    def test_unknown_stdout_value_counts_as_capturing(self) -> None:
        # Fail-closed симметрично `capture_output=<переменная>`: про неизвестное
        # значение нельзя утверждать, что захвата нет.
        source = "import subprocess\nsubprocess.run(cmd, stdout=sink, text=True)\n"
        assert find_violations(source, "sample.py")

    def test_explicit_devnull_is_not_capturing(self) -> None:
        source = "import subprocess\nsubprocess.run(cmd, stdout=subprocess.DEVNULL, text=True)\n"
        assert find_violations(source, "sample.py") == []

    def test_explicit_binary_mode_is_clean(self) -> None:
        # `text=False` — явный бинарный режим: декодирует уже вызывающий, сознательно.
        source = "import subprocess\nsubprocess.run(cmd, capture_output=True, text=False)\n"
        assert find_violations(source, "sample.py") == []

    def test_explicit_encoding_is_clean(self) -> None:
        source = (
            "import subprocess\n"
            "subprocess.run(cmd, text=True, capture_output=True, encoding='utf-8')\n"
        )
        assert find_violations(source, "sample.py") == []


class TestOutputDefaultAnalyzer:
    """Unit'ы правила «дефолт на выводе subprocess запрещён» (#410).

    Пока причина `stdout=None` была неизвестна (#109), `or ""` выглядел
    страховкой. #364 показал, что это симптом умершего на декодировании
    потока-читателя, и причину закрыл — после чего дефолт стал подменять
    **реальный отказ захвата** пустым значением, причём в скриптах, которые сами
    являются гейтами (`check_red`, `validate_issue_sections`, секрет-скан)."""

    def test_output_default_is_flagged(self) -> None:
        source = 'x = result.stdout or ""\n'
        violations = find_output_defaults(source, "sample.py")
        assert violations, "a default on captured output must be flagged"
        assert "sample.py:1" in violations[0], (
            f"the violation must name file:line so it is actionable, got {violations[0]!r}"
        )

    def test_non_empty_literal_default_is_flagged(self) -> None:
        # Форма, которую первая версия инвентаря пропустила целиком: греп искал
        # `or ""`, а `or "{}"` / `or "[]"` живут в open_pr и verify_pr_link.
        source = 'data = json.loads(result.stdout or "[]")\n'
        assert find_output_defaults(source, "sample.py")

    def test_stderr_default_is_flagged(self) -> None:
        source = "msg = (proc.stderr or '').strip()\n"
        assert find_output_defaults(source, "sample.py")

    def test_unrelated_or_default_is_not_flagged(self) -> None:
        # Правило про вывод subprocess, а не про `or` вообще — иначе гард начнёт
        # флагать легитимные дефолты и его придётся ослаблять (глушить нечем).
        source = 'name = os.environ.get("X") or ""\nvalue = payload.title or "n/a"\n'
        assert find_output_defaults(source, "sample.py") == []


class TestRepoIsClean:
    def test_scan_covers_known_files(self) -> None:
        """§IV: «набор не пуст» прошёл бы и при схлопывании glob'а до одного файла.
        Поэтому ассертим конкретные файлы — оба несли нарушение на момент #364."""
        scanned = {path.relative_to(_REPO).as_posix() for path in _scanned_files()}
        for expected in (
            "scripts/hooks.py",
            "tests/test_github_trending_pipeline.py",
            "src/kinozal_scraper/alerting.py",
        ):
            assert expected in scanned, f"{expected} dropped out of the scanned set"

    def test_every_capturing_call_declares_utf8(self) -> None:
        violations = [
            violation
            for path in _scanned_files()
            for violation in find_violations(
                path.read_text(encoding="utf-8"), path.relative_to(_REPO).as_posix()
            )
        ]
        assert not violations, (
            "subprocess call(s) decode the child's output with the OS code page — on "
            "Windows that loses the text on any Cyrillic byte (#364):\n  " + "\n  ".join(violations)
        )

    def test_no_output_defaults(self) -> None:
        violations = [
            violation
            for path in _scanned_files()
            for violation in find_output_defaults(
                path.read_text(encoding="utf-8"), path.relative_to(_REPO).as_posix()
            )
        ]
        assert not violations, (
            "default on captured subprocess output — since #364 closed the cause, "
            "`None` means the capture itself failed, so a default silently replaces a "
            "real failure with emptiness (#410):\n  " + "\n  ".join(violations)
        )

    def test_subprocess_helpers_are_not_imported_by_name(self) -> None:
        offenders = [
            f"{path.relative_to(_REPO).as_posix()}: {name}"
            for path in _scanned_files()
            for name in _imports_subprocess_by_name(path.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            "importing subprocess helpers by name hides the call from this guard — "
            f"use `subprocess.run(...)` instead: {offenders}"
        )
