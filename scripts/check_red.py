#!/usr/bin/env python3
"""Confirm that a set of pytest paths are all failing (RED step).

Usage: python scripts/check_red.py <path-or-nodeid> [<path-or-nodeid> ...]

Exits 0 only when the given tests are RED: ни один тест не зелёный И хотя бы один
упал. Used by `/implement` to gate the RED→GREEN transition: if the freshly-written
tests already pass, the test plan does not cover the intended behaviour change and
`/implement` must abort.

Исход берётся из **junit-отчёта** (`--junitxml`, ядро pytest + stdlib-разбор), а не
из итоговой строки прогона: счётчики `N failed, M passed` не несут идентичности
теста, и `unittest.subTest` их рассинхронизирует — провалившийся сабтест попадает
и в `failed`, и родительский тест в `passed` (#400, наблюдено в #380). Отчёт даёт
исход НА ТЕСТ, поэтому «зелёный только снаружи» перестаёт существовать как класс.
Не «упрощать» обратно к разбору сводки.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Дочерние элементы `<testcase>`, любой из которых означает «тест не зелёный».
# `skipped` здесь же: пропущенный тест ничего не проверил, засчитывать его как
# зелёный — значит блокировать RED-шаг по несуществующему покрытию.
_NOT_GREEN = frozenset({"failure", "error", "skipped"})


def evaluate_report(xml_text: str) -> tuple[bool, str]:
    """Вердикт RED-шага по junit-отчёту. Чистая функция: ни I/O, ни exit.

    RED := зелёных тестов нет, ни один не сломался И хотя бы один упал. При
    `not RED` вердикт называет виновные тесты поимённо — иначе «not RED: 1 passed»
    не подсказывает, какой именно тест уже проходит или не выполнился (§IV).

    **`error` — не RED (#402).** Ошибка сбора/фикстуры значит «тест не выполнялся»,
    а RED-шаг обязан доказать, что тест ЛОВИТ поведение; засчитать её красной значит
    пустить `/implement` в GREEN по непроверенному тесту. Побочно это чинит ложный
    RED на **зелёном** тесте со сломанным teardown (он тоже даёт `<error>`).

    **`error` перевешивает `failure` внутри одного теста.** pytest пишет запись НА
    ФАЗУ, поэтому «упал + сломался teardown» приезжает двумя `<testcase>` с одним
    `classname`/`name`; они группируются в один тест, и сломанная обвязка важнее —
    её надо чинить, а не считать подтверждённым RED. Не «упрощать» обратно.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"junit report is not parsable XML: {exc}") from exc
    # Группировка по (classname, name): без неё запись на фазу считается отдельным
    # тестом — счётчик врёт, а классификация «error перевешивает» не работает вовсе.
    tags_by_test: dict[tuple[str, str], set[str]] = {}
    for case in root.iter("testcase"):
        key = (case.get("classname", ""), case.get("name", ""))
        tags_by_test.setdefault(key, set()).update(child.tag for child in case)
    if not tags_by_test:
        return False, "no tests collected (0 testcases in the junit report)"

    def name_of(key: tuple[str, str]) -> str:
        return f"{key[0]}::{key[1]}".lstrip(":")

    errored = [name_of(k) for k, t in tags_by_test.items() if "error" in t]
    failed = [name_of(k) for k, t in tags_by_test.items() if "error" not in t and "failure" in t]
    green = [name_of(k) for k, t in tags_by_test.items() if not t & _NOT_GREEN]
    total = len(tags_by_test)
    if errored:
        also = f"; зелёные рядом: {', '.join(green)}" if green else ""
        return False, (
            f"not RED: {len(errored)} тест(ов) не выполнились (ошибка сбора/фикстуры) "
            f"of {total}: {', '.join(errored)}{also} — почини их, RED по ним не засчитан"
        )
    if green:
        return False, f"not RED: {len(green)} green test(s) of {total}: {', '.join(green)}"
    if failed:
        return True, f"RED: {len(failed)} failed, 0 green (of {total} tests)"
    skipped = sum(1 for t in tags_by_test.values() if "skipped" in t)
    return False, f"not RED: 0 green, but nothing failed either ({skipped} skipped of {total})"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/check_red.py <path> [<path> ...]", file=sys.stderr)
        sys.exit(2)
    paths = sys.argv[1:]
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "red.xml"
        cmd = [sys.executable, "-m", "pytest", "--tb=no", "-q", f"--junitxml={report}", *paths]
        completed = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        stdout = (completed.stdout or "") + (completed.stderr or "")
        try:
            ok, msg = evaluate_report(report.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Гейт не смог посчитать. Это НЕ RED: молчаливое «красное» открыло бы
            # дорогу в GREEN по непрочитанному отчёту (§IV/§VI). Отдельный код 2
            # отличает «гейт сломан» от «тесты не красные» (1).
            print(f"check_red: cannot evaluate the junit report: {exc}", file=sys.stderr)
            print("--- pytest output ---", file=sys.stderr)
            print(stdout, file=sys.stderr)
            sys.exit(2)
    print(msg)
    if not ok:
        print("--- pytest output ---", file=sys.stderr)
        print(stdout, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
