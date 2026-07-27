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


def _case(name: str, *children: str, classname: str = "tests.mod.T") -> str:
    body = "".join(f'<{tag} message="m">text</{tag}>' for tag in children)
    return f'<testcase classname="{classname}" name="{name}">{body}</testcase>'


def _collection_error(module: str) -> str:
    """Реальная форма ошибки сбора (замерено, pytest 9.0.3): `classname` ПУСТ,
    `name` — точечный путь модуля, ребёнок `<error message="collection failure">`.

    Выдумывать здесь `classname="tests.mod.T"` значило бы пиновать фикцию: именно
    в этой ветке вердикт печатает имя иначе (`lstrip(":")`), и проверка «назвал
    поимённо» прошла бы мимо реального формата."""
    return (
        f'<testcase classname="" name="{module}">'
        '<error message="collection failure">ImportError</error></testcase>'
    )


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

    def test_collection_error_alone_is_not_red(self) -> None:
        # #402 (инверсия поведения из #400): набор, который не собрался, ничего не
        # доказал. Засчитать это RED значит пустить `/implement` в GREEN с тестом,
        # который ни разу не выполнялся, — ложно-зелёный гейт.
        ok, msg = evaluate_report(_report(_collection_error("tests.broken_module")))
        assert not ok
        assert "tests.broken_module" in msg
        assert "0 failed" not in msg

    def test_error_beside_real_failure_is_not_red(self) -> None:
        # «Остальные-то красные» — не аргумент: про невыполненную часть набора
        # по-прежнему ничего не известно.
        ok, msg = evaluate_report(
            _report(_collection_error("tests.broken_module"), _case("test_plain_red", "failure"))
        )
        assert not ok
        assert "tests.broken_module" in msg

    def test_phase_split_entries_are_one_test(self) -> None:
        # pytest пишет запись НА ФАЗУ: упавший тест со сломанным teardown приезжает
        # ДВУМЯ <testcase> с одинаковыми classname/name (замерено). Без группировки
        # один тест считался бы дважды; `error` перевешивает `failure` — сломанный
        # teardown это дефект, который надо чинить, а не подтверждённый RED.
        ok, msg = evaluate_report(_report(_case("test_x", "failure"), _case("test_x", "error")))
        assert not ok
        assert "test_x" in msg

    def test_errored_and_green_are_both_named(self) -> None:
        ok, msg = evaluate_report(
            _report(_collection_error("tests.broken_module"), _case("test_really_green"))
        )
        assert not ok
        assert "tests.broken_module" in msg
        assert "test_really_green" in msg

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
