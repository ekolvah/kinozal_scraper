"""Гард на форму ссылки в `.md`: `#N` — указатель, а не член предложения (#428).

**Что стережём.** `project-map.md` §«Что описывает документация» требует от доков текущее
состояние, а не хронику. Правило жило только прозой, и давление на него одностороннее:
абзац с номером таски выглядит авторитетнее, поэтому номера накапливаются, а вычищать их
никто не приходит. Замер в [ADR-0001](../docs/adr/0001-record-architecture-decisions.md)
это и показал. Детерминируемая половина правила — **форма**: `#N` стоит скобочным
указателем (предложение полно и без него) либо является членом предложения (без перехода
в трекер фраза неполна). Это случай `mindset.md` §«Скрипты > инструкции»: exit-code вместо
пункта в чек-листе ревьюера.

**Две ветки, разные причины.** В теле — форма ради читаемости. В заголовке `#N` запрещён
**и в скобках**: якорь GitHub генерится из текста заголовка, поэтому `## Eval harness
(#139)` делает адресом секции `#eval-harness-139` — адрес, привязанный к номеру породившей
секцию таски. `tests/test_doc_links.py` ловит **последствие** (провисший якорь после
переименования), эта ветка — **причину**, до того как якорь вообще создан.

**Fail-closed на непарном разделителе.** Разрешённая зона — только **сомкнутая** пара
`()`. Незакрытая скобка зоны не открывает: иначе одна опечатка молча превращает хвост
абзаца в белый список, и гард зеленеет, не сообщив ничего (§IV — ровно тот дефект, ради
которого он написан). По той же причине включён `table`: без него таблица склеивается в
один `inline`-токен и скобка из одной ячейки смыкается со скобкой из другой через пять
строк.

**Парность backtick'ов и границы ссылок отдаются парсеру.** `code_inline` и label ссылки —
легальные места для `#N`, и скобки внутри них в парности не участвуют (`` `f(x)` ``).
Считать это регекспом значило бы переписать `markdown-it-py`, ради которого он и взят
(#427). Содержимое ```-блоков отсеивается парсером даром: это токен `fence`, не `inline`.

**Сигил `#` зарезервирован за ссылками на issue/PR.** Номер правила пишется `workflow.md §7`,
доска — `Project 1`. Конвенция (канон — `project-map.md`) заменяет собой ветку исключений в
предикате: без неё пришлось бы вести открытый словарь левого контекста, который растёт с
каждой новой формой записи. Сама она — **третья ветка гарда** и единственная репо-широкая:
`#N` рядом с `workflow`/`Project` встречается и в `.py`, и в `.toml`, а прозы для этой
половины не хватило — на её свёртку в #428 ушло три коммита, два из них после ревью.
Гейтируемой ветку делает **закрытость** словаря (две формы) — ровно то условие, по которому
ветка дат была отклонена как открытая (ledger **AC** в `coverage-gaps.md`).

**Скоуп.** Ветки про форму указателя — `.md` из индекса git минус `docs/adr/`; ветка сигила —
**все** отслеживаемые файлы. `git ls-files`, а не обход ФС: `.claude/worktrees/` gitignored
и держит копии репо со старыми доками (тот же аргумент, что в `tests/test_doc_links.py`).
Записи MADR исключены **по жанру**: запись — дом обоснования, она датирована по конструкции
и после принятия иммутабельна, так что покрасневший на ней гард не имел бы легального
фикса — остался бы exception-список.

**Границы гарда, честно.** Гейтится **форма**, а не жанр: `Closed by #88.` → `Закрыто
(#88).` пройдёт, не став лучше. Замер на момент заведения гарда: из 266 упоминаний `#N`
в скоупе под него попали 23, остальные разрешены — и настоящий нарратив среди разрешённых
есть (`.claude/commands/implement.md` держит два в скобках). Гард делает рецидив **видимым
на ревью** в форме, в которой он дешевле всего, — не делает невозможным. Семантическое «норматив или археология» остаётся человеку, тот же
класс, что детектор семантических дублей (`project-map.md`).

Ещё два предела, названных явно. **Label ссылки прозрачен целиком**, а не только когда он
и есть номер: `[Closed by #88 — подробности](url)` гард пропустит. Сузить можно, но плата —
ветка на форму label'а ради случая, которого в репо нет (§VII). **Номер строки в сообщении
может отставать**, если выше в том же абзаце стоит токен, чей `content` короче занятого им
куска исходника: code-span, перенесённый через строку (CommonMark заменяет перевод строки
внутри `` ` `` пробелом), `![](url)` (в `content` только alt) или экранированный `\\#428`.
Дефект сообщения, а не вердикта; чинить его пришлось бы собственным source-map'ом.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import pytest
from markdown_it import MarkdownIt
from markdown_it.token import Token

_REPO_ROOT = Path(__file__).resolve().parent.parent

# `table` не декоративен — см. §Fail-closed в докстринге.
_MD = MarkdownIt("commonmark").enable("table")

# `#` сразу после буквы/цифры — не ссылка (`C#1`); после дефиса — ссылка (`pre-#95`,
# и это как раз запрещённая форма). Якорь (`#doc-guards`) не матчится: за `#` не цифра.
_ISSUE_REF = re.compile(r"(?<!\w)#\d+\b")

# Заглушка непрозрачной зоны: длина сохраняется, чтобы смещения оставались настоящими.
_OPAQUE = "\x00"

_EXPECTED_SCOPE_DIRS = ("docs/architecture", ".claude/rules", ".claude/commands")

# Не-issue употребления сигила: номер правила и номер доски. Словарь **закрыт** — ровно
# то условие, по которому ветка дат была отклонена (ledger **AC**), а эта взята. Живёт
# не в скоупе `.md`: три коммита этого же PR понадобились, чтобы вычистить `.py`
# и `.toml`, — прозы для второй половины конвенции не хватило.
#
# Хвост `(?:\.md)?[`)\]]*` не украшение: ссылка на правило пишется четырьмя способами —
# `workflow #N`, `` `workflow.md` #N ``, `[`workflow.md`](path) #N`, `Project #N` — и
# закрыть словарь по двум *написаниям* вместо двух *токенов* значило бы оставить
# большинство `.md`-мест на прозе. `IGNORECASE` — ради `Workflow` в начале предложения.
#
# Отрицательный пример пишется через метапеременную `#N`, а не с цифрой: эта ветка —
# построчный regexp по сырому файлу, code-span для неё не escape hatch (в отличие от двух
# markdown-веток). За `#` не цифра — предикат молчит, и правило можно показать.
_NON_ISSUE_SIGIL = re.compile(r"(?:workflow(?:\.md)?[`)\]]*|project)\s+#\d", re.IGNORECASE)

# Каталог записей MADR — вне скоупа по жанру (докстринг §Скоуп). Пришпилено
# `test_adr_records_are_out_of_scope`, чтобы carve-out остался осознанным.
_EXCLUDED_DIR = "docs/adr/"


def readable_stream(inline: Token) -> str:
    """Текст `inline`-токена, где непрозрачные для правила зоны забиты заглушкой.

    Заглушкой становятся `code_inline` и содержимое label'а ссылки: `#N` там легален,
    а скобки внутри не должны участвовать в парности. `softbreak` → `\\n`, иначе
    смещение находки не переводится в номер строки.
    """
    parts: list[str] = []
    link_depth = 0
    for child in inline.children or []:
        if child.type == "link_open":
            link_depth += 1
        elif child.type == "link_close":
            link_depth -= 1
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append("\n")
        elif link_depth or child.type != "text":
            parts.append(_OPAQUE * len(child.content))
        else:
            parts.append(child.content)
    return "".join(parts)


def paired_spans(text: str) -> list[tuple[int, int]]:
    """Диапазоны **сомкнутых** пар `()`. Незакрытая `(` зоны не открывает."""
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    for i, char in enumerate(text):
        if char == "(":
            stack.append(i)
        elif char == ")" and stack:
            spans.append((stack.pop(), i))
    return spans


def narrative_refs(markdown: str) -> list[tuple[int, str]]:
    """`(строка, фрагмент)` для каждого `#N`, стоящего не скобочным указателем."""
    found: list[tuple[int, str]] = []
    for token in _MD.parse(markdown):
        if token.type != "inline":
            continue
        # `map or [0]`, а не `map is None → skip`: пропуск выбросил бы содержимое токена
        # из проверки молча — ровно тот §IV-вакуум, против которого гард написан. Сегодня
        # ветка недостижима (ячейки таблиц `.map` несут — это доказывает
        # `test_table_cell_paren_does_not_leak`), но цена честного отчёта — ноль.
        start_line = (token.map or [0])[0]
        stream = readable_stream(token)
        spans = paired_spans(stream)
        for match in _ISSUE_REF.finditer(stream):
            if any(start < match.start() < end for start, end in spans):
                continue
            line = start_line + stream[: match.start()].count("\n") + 1
            around = stream[max(0, match.start() - 60) : match.start() + 60]
            found.append((line, around.replace(_OPAQUE, "·").replace("\n", " ").strip()))
    return found


def heading_issue_refs(markdown: str) -> list[tuple[int, str]]:
    """`(строка, текст заголовка)` для каждого заголовка с `#N` — в скобках тоже."""
    tokens = _MD.parse(markdown)
    return [
        ((token.map or [0])[0] + 1, tokens[i + 1].content)
        for i, token in enumerate(tokens)
        if token.type == "heading_open" and _ISSUE_REF.search(tokens[i + 1].content)
    ]


def sigil_misuses(text: str) -> list[tuple[int, str]]:
    """`(строка, фрагмент)` там, где `#N` назвал не issue, а правило или доску.

    Построчный regexp, а не разбор markdown: ветка идёт по **всем** отслеживаемым
    файлам, включая `.py` и `.toml`, где markdown-структуры нет.
    """
    return [
        (i, line.strip())
        for i, line in enumerate(text.splitlines(), 1)
        if _NON_ISSUE_SIGIL.search(line)
    ]


@lru_cache(maxsize=1)
def _tracked_files() -> tuple[str, ...]:
    """Все отслеживаемые пути. `-z`: иначе git C-квотит нелатиницу."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return tuple(name for name in result.stdout.split("\0") if name)


def _tracked_docs() -> tuple[str, ...]:
    """Отслеживаемые `.md` вне `docs/adr/` — скоуп веток про форму указателя."""
    names = [name for name in _tracked_files() if name.endswith(".md")]
    return tuple(name for name in names if not name.startswith(_EXCLUDED_DIR))


def _report(finder: Callable[[str], list[tuple[int, str]]], docs: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for doc in docs:
        text = (_REPO_ROOT / doc).read_text(encoding="utf-8")
        found.extend(f"{doc}:{line}: {fragment}" for line, fragment in finder(text))
    return found


def _report_any(names: tuple[str, ...]) -> list[str]:
    """`sigil_misuses` по **всем** отслеживаемым файлам, включая нетекстовые.

    Декодирование лоскутное (`errors="replace"`): искомые формы ASCII-only, поэтому
    порча нелатиницы находок не теряет, а фильтр по расширению — наоборот, тихо
    выронил бы новый тип файла из инварианта.
    """
    found: list[str] = []
    for name in names:
        text = (_REPO_ROOT / name).read_bytes().decode("utf-8", errors="replace")
        found.extend(f"{name}:{line}: {fragment}" for line, fragment in sigil_misuses(text))
    return found


class TestDocsCarryNoNarrative:
    @pytest.mark.parametrize("directory", _EXPECTED_SCOPE_DIRS)
    def test_scope_covers_expected_dirs(self, directory: str) -> None:
        assert any(name.startswith(f"{directory}/") for name in _tracked_docs()), (
            f"в скоуп гарда не попал ни один `.md` из {directory} — либо каталог переехал, "
            f"либо `git ls-files` вернул не то. Пустой скоуп зелёный, и это ровно тот "
            f"вакуум, против которого гард написан (§IV)"
        )

    def test_adr_records_are_out_of_scope(self) -> None:
        """Carve-out осознан, а не выродился: записи MADR есть и в скоуп не попали."""
        result = subprocess.run(
            ["git", "ls-files", "-z", "docs/adr"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        assert [name for name in result.stdout.split("\0") if name.endswith(".md")]
        assert not [name for name in _tracked_docs() if name.startswith(_EXCLUDED_DIR)]

    def test_no_issue_ref_in_heading(self) -> None:
        problems = _report(heading_issue_refs, _tracked_docs())
        assert not problems, (
            "номер таски в заголовке секции: якорь GitHub генерится из текста заголовка, "
            "поэтому адрес секции оказывается привязан к номеру породившей её таски и "
            "рвётся при первом переименовании:\n  " + "\n  ".join(problems)
        )

    def test_non_md_files_are_in_sigil_scope(self) -> None:
        """Ветка сигила репо-широкая: `.py`/`.toml` обязаны в неё попадать (§IV)."""
        assert [name for name in _tracked_files() if not name.endswith(".md")]

    def test_sigil_names_only_issues(self) -> None:
        problems = _report_any(_tracked_files())
        assert not problems, (
            "сигил `#` зарезервирован за ссылками на issue/PR (канон — `project-map.md` "
            "§«Что описывает документация»): номер правила пишется `workflow.md §N`, "
            "номер доски — `Project N`. Иначе адрес неотличим от номера задачи — а задача "
            "с таким номером в этом репо, скорее всего, существует:\n  " + "\n  ".join(problems)
        )

    def test_no_narrative_issue_ref(self) -> None:
        problems = _report(narrative_refs, _tracked_docs())
        assert not problems, (
            "`#N` членом предложения — без перехода в трекер фраза неполна. Указатель "
            "ставится в скобки после законченного утверждения; номер правила — "
            "`workflow.md §N`, доска — `Project N`:\n  " + "\n  ".join(problems)
        )


class TestNarrativePredicates:
    """Предикаты на синтетике: без них гард доказывал бы сам себя на зелёном репо."""

    def test_pointer_in_parens_is_allowed(self) -> None:
        assert narrative_refs("Дискриминатор сверяет точный литерал (#385).") == []

    def test_pointer_across_soft_wrap_is_allowed(self) -> None:
        assert narrative_refs("Цена локализованной игры — два запроса\nвместо одного (#384).") == []
        assert narrative_refs("Цена локализованной игры (два\nзапроса вместо одного, #384).") == []

    def test_pointer_around_code_span_is_allowed(self) -> None:
        assert narrative_refs("Плата видна в байтах (`test_always_load_budget.py`, #375).") == []

    def test_pointer_in_link_label_is_allowed(self) -> None:
        assert narrative_refs("Гейт пришпилен [#231](https://example.com/231).") == []

    def test_pointer_in_code_span_is_allowed(self) -> None:
        assert narrative_refs("Скрипт форсит `Closes #319` в body PR.") == []

    def test_sentence_member_is_reported(self) -> None:
        assert len(narrative_refs("Closed by #88.")) == 1
        assert len(narrative_refs("RCA #396 установил, что исход решает одна величина.")) == 1

    def test_unbalanced_paren_does_not_whitelist_tail(self) -> None:
        """Одна незакрытая скобка не превращает хвост абзаца в белый список (§IV)."""
        assert len(narrative_refs("Вызов `f(x` сломан — подробности в #88.")) == 1

    def test_stray_backtick_does_not_whitelist_tail(self) -> None:
        assert len(narrative_refs("Одиночный ` в прозе, а дальше Closed by #88.")) == 1

    def test_paren_inside_code_span_is_not_a_zone(self) -> None:
        """Скобки внутри code-span'а в парности не участвуют — их разрешил парсер."""
        assert len(narrative_refs("Функция `f(x)` сломана — Closed by #88.")) == 1

    def test_table_cell_paren_does_not_leak(self) -> None:
        table = "| a | b |\n|---|---|\n| x (foo | bar) Closed by #88 |\n"
        assert len(narrative_refs(table)) == 1

    def test_ref_inside_fence_is_ignored(self) -> None:
        assert narrative_refs("```\nClosed by #88\n```\n") == []

    def test_issue_ref_in_heading_is_reported(self) -> None:
        """В заголовке скобки не спасают: якорь генерится из всего текста."""
        assert len(heading_issue_refs("## Eval harness (#139)")) == 1
        assert heading_issue_refs("## Eval harness — trailer selection") == []
        assert narrative_refs("## Eval harness (#139)") == []

    def test_anchor_is_not_an_issue_ref(self) -> None:
        assert narrative_refs("Секция `ci.md#doc-guards` описывает реестр.") == []
        assert narrative_refs("Якорь #doc-guards-2 у второго одноимённого заголовка.") == []

    def test_rule_and_board_numbers_are_reported(self) -> None:
        # Нарушители собираются интерполяцией: ветка сигила сканирует **все**
        # отслеживаемые файлы, включая этот, и литерал в тесте покраснел бы сам на себе.
        assert len(sigil_misuses(f"правило workflow #{7} и доска Project #{1}")) == 1
        assert sigil_misuses("правило `workflow.md` §7 и доска Project 1") == []

    def test_all_four_spellings_of_a_rule_number_are_reported(self) -> None:
        """Словарь закрыт по *токену*, а не по одному написанию.

        Три из четырёх форм стоят в `.md` внутри сомкнутых скобок, то есть
        `narrative_refs` их пропускает по построению — если бы regexp требовал
        соседства, регресс `§N` → `#N` был бы невидим всем трём веткам.
        """
        for spelling in (
            f"workflow #{9}",
            f"`workflow.md` #{9}",
            f"[`workflow.md`](../rules/workflow.md) #{9}",
            f"Workflow #{9}",
        ):
            assert sigil_misuses(spelling), spelling

    def test_sigil_branch_reads_any_file_type(self) -> None:
        """Ветка построчная — работает и там, где markdown-структуры нет."""
        line = f"# comment: pip-compile (workflow #{7})"
        assert sigil_misuses(f'{line}\nname = "x"\n') == [(1, line)]

    def test_line_number_points_at_the_offending_line(self) -> None:
        markdown = "# Заголовок\n\nПервая строка абзаца,\nвторая: Closed by #88.\n"
        assert [line for line, _ in narrative_refs(markdown)] == [4]
