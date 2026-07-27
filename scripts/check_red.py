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
# Подмножество, означающее «тест реально упал». `skipped` в него не входит: набор
# из одних пропусков — не RED, иначе `/implement` уйдёт в GREEN, ничего не проверив.
_FAILED = frozenset({"failure", "error"})


def evaluate_report(xml_text: str) -> tuple[bool, str]:
    """Вердикт RED-шага по junit-отчёту. Чистая функция: ни I/O, ни exit.

    RED := зелёных тестов нет И хотя бы один упал. При `not RED` вердикт называет
    зелёные тесты поимённо — иначе «not RED: 1 passed» не подсказывает, какой
    именно тест написан так, что уже проходит (§IV).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"junit report is not parsable XML: {exc}") from exc
    green: list[str] = []
    failed = 0
    skipped = 0
    total = 0
    for case in root.iter("testcase"):
        total += 1
        tags = {child.tag for child in case}
        if tags & _FAILED:
            failed += 1
        elif "skipped" in tags:
            skipped += 1
        if not tags & _NOT_GREEN:
            green.append(f"{case.get('classname', '')}::{case.get('name', '')}".lstrip(":"))
    if total == 0:
        return False, "no tests collected (0 testcases in the junit report)"
    if not green and failed >= 1:
        return True, f"RED: {failed} failed, 0 green (of {total} testcases)"
    if not green:
        return False, f"not RED: 0 green, but nothing failed either ({skipped} skipped)"
    return False, f"not RED: {len(green)} green test(s) of {total}: {', '.join(green)}"


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
