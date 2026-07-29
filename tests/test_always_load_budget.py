"""Бюджет always-load контекста (#375).

**Что стережём.** `CLAUDE.md` и файлы `.claude/rules/*.md` **без** `paths:` во frontmatter
грузятся в каждую сессию целиком, до первого слова задачи (`project-map.md` §«Честно про
токены»). Это безусловная плата, взимаемая независимо от темы сессии, — то есть прямой
предмет приоритета (2) цель-функции. На 29.07.2026 она составляла 21 135 B (~5.3k токенов),
и выросла до этой цифры **молча**: ~3.8 КБ прироста дали #416/#417, добавлявшие тактики в
`mindset.md`. Рецидив наблюдаемый, а не гипотетический, — отсюда гейт.

**Это tripwire, а не норматив качества.** Тест не утверждает «текст хороший» и не может:
«сколько здесь правила, а сколько прозы-обоснования» — семантическое суждение, которое репо
сознательно не скриптует (`project-map.md`). Он утверждает ровно одно: рост платы —
**осознанная правка одной строки на ревью**, а не невидимый дрейф. Именно этим он отличается
от отвергнутого в ledger-записи **AA** гейта «док не длиннее N строк»: там строки были
*прокси* качества дока (под порогом ужимается формулировка, а не археология → гейт зелен,
когда дефект замаскирован), здесь байты — сама взимаемая плата, а порог — храповик.

**Чего гейт НЕ ловит (границы скоупа).** Сессионная преамбула шире этого набора: в неё
входят ещё `description:` сабагентов и слэш-команд и индекс `MEMORY.md`. Зелёный тест
поэтому **не** значит «вся плата под контролем» — cost-shifting в те носители (равно как и
перенос текста в `docs/architecture/*`, который агент всё равно читает по требованию) он
не увидит. Расширение скоупа — отдельное решение, здесь только честно названная граница.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

_ROOT_MEMORY = _REPO_ROOT / "CLAUDE.md"
_RULES_DIR = _REPO_ROOT / ".claude" / "rules"

# Файлы, чьё присутствие в наборе — принятое решение, а не случайность (см.
# `test_expected_files_are_in_scope`).
_EXPECTED_ALWAYS_LOAD = (
    _ROOT_MEMORY,
    _RULES_DIR / "mindset.md",
    _RULES_DIR / "workflow.md",
)

# Порог = достигнутое резом #375 + ~1 КБ запаса. Это **не** «сколько можно потратить»:
# запас нужен, чтобы законная новая тактика не краснила CI с первой же строки, иначе
# «осознанный бамп» выродится в рутинный штамп. Revisit-триггер: бамп ради текста, который
# при чтении диффа оказывается нарративом «как мы к этому пришли», а не правилом, — такой
# текст переезжает в тело issue/PR под указатель `(#N)`, а не в бюджет.
_BUDGET_BYTES = 20_500

_FRONTMATTER_PATHS_KEY = re.compile(r"^paths\s*:", re.MULTILINE)


def _frontmatter(text: str) -> str:
    """Блок между первой и второй строкой `---`, либо пусто."""
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    return text[4:end] if end != -1 else ""


def _is_path_scoped(path: Path) -> bool:
    return bool(_FRONTMATTER_PATHS_KEY.search(_frontmatter(path.read_text(encoding="utf-8"))))


def _always_load_files() -> list[Path]:
    """Набор **выводится**, а не перечисляется: новый always-load rule попадёт под бюджет сам."""
    rules = [p for p in sorted(_RULES_DIR.glob("*.md")) if not _is_path_scoped(p)]
    return [_ROOT_MEMORY, *rules]


def _normalized_size(path: Path) -> int:
    """Байты при LF-переводах строк.

    `.gitattributes` в репо нет, а у мейнтейнера `core.autocrlf=true`: рабочее дерево
    локально CRLF, в CI — LF. Разница на этом наборе — ~230 B, то есть порядка 10% всего
    выигрыша #375; без нормализации гейт был бы «зелёный у меня, красный в CI».
    """
    return len(path.read_bytes().replace(b"\r\n", b"\n"))


class TestAlwaysLoadBudget:
    def test_total_within_budget(self) -> None:
        files = _always_load_files()
        sizes = {p.name: _normalized_size(p) for p in files}
        total = sum(sizes.values())
        assert total <= _BUDGET_BYTES, (
            f"always-load бюджет превышен: {total} B > {_BUDGET_BYTES} B ({sizes}). "
            f"Это плата с КАЖДОЙ сессии, независимо от темы. Прежде чем поднимать порог — "
            f"проверь, не нарратив ли добавлен: «как мы к этому пришли» живёт в теле issue/PR, "
            f"в правиле остаётся указатель `(#N)` (#375). Поднятие порога легитимно, но это "
            f"осознанное решение на ревью, а не побочный эффект правки."
        )

    @pytest.mark.parametrize("path", _EXPECTED_ALWAYS_LOAD, ids=lambda p: p.name)
    def test_expected_files_are_in_scope(self, path: Path) -> None:
        """Поимённо, а не «набор непуст».

        Главный способ обнулить бюджет — дописать `paths:` во frontmatter `mindset.md`:
        файл выпадет из набора, сумма **упадёт**, тест позеленеет, а always-load-правила
        окажутся молча выключены (§IV). Проверка на непустоту это пропускает — в наборе
        остались бы два других файла. Ровно этот риск записан в ledger'е как **Y** (#416),
        где гард на один файл был отвергнут с оговоркой «имеет смысл писать сразу на весь
        каталог»; здесь он и написан на каталог.
        """
        assert path in _always_load_files(), (
            f"{path.name} выпал из always-load набора — вероятно, у него появился `paths:` "
            f"во frontmatter. Тогда правила из него больше не грузятся в каждую сессию: "
            f"бюджет упал, но не потому, что текст ужали (#416, #375)"
        )

    def test_path_scoped_rule_is_excluded(self) -> None:
        """Пинится **механизм фильтра**, а не конкретный файл.

        `testing.md` взят как единственный существующий носитель `paths:`; если он
        переедет или сменит скоуп, красный тест означает «фильтр больше нечем проверить»,
        а не «сломался бюджет».
        """
        path_scoped = [p for p in sorted(_RULES_DIR.glob("*.md")) if _is_path_scoped(p)]
        assert path_scoped, (
            "в `.claude/rules/` не осталось ни одного path-scoped файла — фильтр `paths:` "
            "больше ничем не проверяется, и его поломка прошла бы незаметно"
        )
        assert not set(path_scoped) & set(_always_load_files()), (
            f"path-scoped файлы попали в бюджет: {[p.name for p in path_scoped]}. Бюджет "
            f"обязан считать именно always-load плату, а не все `.md` подряд"
        )
