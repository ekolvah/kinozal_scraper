from __future__ import annotations

from scripts.check_red import parse_pytest_summary, red_status


class TestParsePytestSummary:
    def test_all_failed(self) -> None:
        out = "tests/foo.py F\n\n======= 3 failed in 0.12s =======\n"
        assert parse_pytest_summary(out) == (0, 3, 0)

    def test_mixed_pass_and_fail(self) -> None:
        out = "======= 1 failed, 2 passed in 0.10s =======\n"
        assert parse_pytest_summary(out) == (2, 1, 0)

    def test_errors_only(self) -> None:
        out = "======= 2 errors in 0.10s =======\n"
        assert parse_pytest_summary(out) == (0, 0, 2)

    def test_no_summary_line_returns_zeros(self) -> None:
        assert parse_pytest_summary("collected 0 items\n") == (0, 0, 0)

    def test_passed_failed_errors_combined(self) -> None:
        out = "======= 1 failed, 1 passed, 1 error in 0.20s =======\n"
        assert parse_pytest_summary(out) == (1, 1, 1)


class TestRedStatus:
    def test_all_failed_is_ok(self) -> None:
        ok, _ = red_status(passed=0, failed=3, errors=0)
        assert ok

    def test_any_passed_is_not_ok(self) -> None:
        ok, msg = red_status(passed=1, failed=2, errors=0)
        assert not ok
        assert "passed" in msg.lower()

    def test_zero_tests_is_not_ok(self) -> None:
        ok, msg = red_status(passed=0, failed=0, errors=0)
        assert not ok
        assert "no test" in msg.lower() or "0" in msg

    def test_errors_count_as_red(self) -> None:
        ok, _ = red_status(passed=0, failed=0, errors=2)
        assert ok
