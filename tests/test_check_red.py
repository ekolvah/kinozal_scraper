from __future__ import annotations

import pytest

from scripts.check_red import evaluate_report


def _report(*cases: str) -> str:
    """junit-отчёт pytest вокруг готовых `<testcase>`-элементов.

    Тесты держат ЗАХВАЧЕННЫЙ формат (pytest 9.0.3, `--junitxml`), а не запускают
    подпроцесс: гейт обязан быть проверяем без прогона pytest внутри pytest.
    """
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites>'
        f'<testsuite name="pytest" tests="{len(cases)}">{"".join(cases)}'
        "</testsuite></testsuites>"
    )


def _case(name: str, *children: str) -> str:
    body = "".join(f'<{tag} message="m">text</{tag}>' for tag in children)
    return f'<testcase classname="tests.mod.T" name="{name}">{body}</testcase>'


class TestEvaluateReport:
    def test_subtest_failures_make_parent_not_green(self) -> None:
        # Репро #400: pytest считает такой тест в `passed` (сабтесты не роняют
        # родителя), но в отчёте у него два `<failure>` — зелёным он не является.
        ok, msg = evaluate_report(_report(_case("test_all_subtests_fail", "failure", "failure")))
        assert ok, msg

    def test_genuinely_green_test_blocks_red(self) -> None:
        # Ложно-зелёное направление — самый дорогой исход: гейт обязан НЕ пустить
        # в GREEN, когда рядом с красными есть настоящий зелёный тест, и назвать его.
        ok, msg = evaluate_report(
            _report(_case("test_subtests_failed", "failure"), _case("test_really_green"))
        )
        assert not ok
        assert "test_really_green" in msg
        assert "test_subtests_failed" not in msg

    def test_plain_failure_is_red(self) -> None:
        ok, msg = evaluate_report(_report(_case("test_plain_red", "failure")))
        assert ok, msg

    def test_collection_error_is_red(self) -> None:
        # Поведение сохранено как было: ошибка сбора = RED. Что она означает
        # «тест ни разу не выполнялся» — отдельная логическая единица (Out of scope #400).
        ok, msg = evaluate_report(_report(_case("tests.broken_module", "error")))
        assert ok, msg

    def test_skipped_only_is_not_red(self) -> None:
        # Пропущенный тест не зелёный, но и не упавший: объявить это RED значило бы
        # пустить `/implement` в GREEN по набору, который ничего не проверил.
        ok, msg = evaluate_report(_report(_case("test_skipme", "skipped")))
        assert not ok
        assert "skip" in msg.lower()

    def test_no_testcases_is_not_red(self) -> None:
        ok, msg = evaluate_report(_report())
        assert not ok
        assert "0" in msg or "no test" in msg.lower()

    def test_malformed_report_raises(self) -> None:
        # Неспособность посчитать не должна открывать дорогу в GREEN (§IV/§VI).
        with pytest.raises(ValueError):
            evaluate_report("not xml at all")
