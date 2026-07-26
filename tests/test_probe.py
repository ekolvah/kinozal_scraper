"""Измеритель доступности soldout из CI (#396).

RCA #396 показал, что исход прогона решает единственная величина — пробьёт ли хоть
одна попытка вероятностный `cf-mitigated=challenge`, — но исторические логи её не
дают (диагностика приехала только 25.07). Пробник собирает её без побочных эффектов;
эти тесты пиннят свойства, без которых замер перестаёт что-либо значить: одна попытка
на замер, тот же путь что у прода, строка на КАЖДЫЙ исход, и разделение «HTTP-исход»
(зелёный) от «сбой инструмента» (красный).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import unittest
import unittest.mock

from curl_cffi.requests.exceptions import HTTPError
from scripts.probe import is_expired, main

from kinozal_scraper.http_fetch import fetch_html

_BEFORE_EXPIRY = dt.date(2026, 7, 27)
_URL = "https://www.soldoutticketbox.com/easyconsole.cfm/page/category/cat_id/17/lang/ru"
_ENV = {"PROBE_URL": _URL}


def _blocked(status: int = 403) -> unittest.mock.Mock:
    """403 с настоящими headers/body — их читает `describe_block` на пути отказа."""
    resp = unittest.mock.Mock()
    resp.status_code = status
    resp.headers = {"cf-ray": "a210a6f17f3098c3-ORD", "cf-mitigated": "challenge"}
    resp.content = b"<html><title>Security Check</title></html>"
    resp.raise_for_status.side_effect = HTTPError(f"HTTP Error {status}", 0, resp)
    return resp


def _page(body: str = "<html>ok</html>") -> unittest.mock.Mock:
    resp = unittest.mock.Mock()
    resp.status_code = 200
    resp.headers = {"cf-ray": "a214b0ae788f356a-ORD"}
    resp.text = body
    resp.content = body.encode()
    resp.raise_for_status.return_value = None
    return resp


def _poster() -> unittest.mock.Mock:
    resp = unittest.mock.Mock()
    resp.status_code = 200
    resp.headers = {"content-type": "image/jpeg"}
    resp.text = ""
    resp.content = b"\xff\xd8\xff"
    resp.raise_for_status.return_value = None
    return resp


def _run(
    responses: list[unittest.mock.Mock] | Exception, env: dict[str, str] | None = None
) -> tuple[int, str, unittest.mock.Mock]:
    """Прогнать `main` на подставленной границе curl_cffi, вернуть (код, stdout, mock)."""
    out = io.StringIO()
    with (
        unittest.mock.patch(
            "kinozal_scraper.http_fetch.requests.get", side_effect=responses
        ) as mget,
        contextlib.redirect_stdout(out),
    ):
        code = main(
            _ENV if env is None else env,
            sleep=unittest.mock.Mock(),
            today=_BEFORE_EXPIRY,
        )
    return code, out.getvalue(), mget


class TestProbeLines(unittest.TestCase):
    def test_prints_a_line_for_every_attempt_including_success(self) -> None:
        """Успех, выраженный ОТСУТСТВИЕМ строки, — это молчание как данные (§IV):
        долю успеха пришлось бы читать как «сколько строк не хватает», а грепом это
        не собирается вовсе."""
        _, stdout, _ = _run([_blocked(), _page(), _page()])

        lines = [ln for ln in stdout.splitlines() if ln.startswith("PROBE")]
        self.assertEqual(len(lines), 3)
        self.assertEqual(sum("status=200" in ln for ln in lines), 2)
        self.assertEqual(sum("status=403" in ln for ln in lines), 1)

    def test_line_matches_grep_contract(self) -> None:
        """Ассерты сужены до контракта, по которому снимается матрица. Наличие
        `cf-mitigated=` не проверяем — это покрытие `describe_block`."""
        _, stdout, _ = _run([_blocked(), _blocked(), _blocked()])

        first = next(ln for ln in stdout.splitlines() if ln.startswith("PROBE"))
        for field in ("ts=", "kind=html", "attempt=1/3", "status=", "elapsed="):
            self.assertIn(field, first)


class TestSingleAttempt(unittest.TestCase):
    def test_makes_one_request_per_attempt(self) -> None:
        _, _, mget = _run([_blocked(), _page(), _blocked()])

        self.assertEqual(mget.call_count, 3)

    def test_single_attempt_does_not_retry_403(self) -> None:
        """Мерим вероятность ОДНОЙ попытки: прод-ретрай (4 попытки) её маскирует —
        12 запросов вместо 3 превратили бы замер в «хоть раз повезло»."""
        _, _, mget = _run([_blocked(), _blocked(), _blocked()])

        self.assertEqual(mget.call_count, 3)

    def test_uses_the_same_request_kwargs_as_fetch_html(self) -> None:
        """Анти-дрейф «замер = прод»: разойдясь с `fetch_html` по impersonate или
        timeout, пробник начнёт мерить что-то другое, и это никак не проявится."""
        _, _, mget = _run([_blocked(), _blocked(), _blocked()])
        probe_kwargs = mget.call_args_list[0].kwargs

        with unittest.mock.patch(
            "kinozal_scraper.http_fetch.requests.get", return_value=_page()
        ) as prod:
            fetch_html(_URL)

        self.assertEqual(probe_kwargs, prod.call_args.kwargs)


class TestExitCodes(unittest.TestCase):
    def test_block_exits_zero(self) -> None:
        """403 — это РЕЗУЛЬТАТ измерения, а не сбой: красный прогон каждые 3 часа
        обесценил бы алертинг остальных workflow."""
        code, _, _ = _run([_blocked(), _blocked(), _blocked()])

        self.assertEqual(code, 0)

    def test_probe_own_failure_is_not_swallowed(self) -> None:
        """Измеритель, зеленеющий при собственной поломке, бесполезен."""
        with self.assertRaises(ConnectionError):
            _run(ConnectionError("dns down"))

    def test_missing_url_exits_nonzero(self) -> None:
        """Незаданная `vars.SOLDOUT_URL` разворачивается в "" — без явной проверки
        «сбой инструмента» неотличим от «сайт лежит» (§VI)."""
        code, stdout, mget = _run([_page()], env={"PROBE_URL": ""})

        self.assertNotEqual(code, 0)
        self.assertIn("PROBE_URL", stdout)
        mget.assert_not_called()


class TestExpiry(unittest.TestCase):
    """Тайм-бокс как механизм, а не как обещание в доке: после срока пробник сам
    краснеет и требует удаления («скрипты > инструкции»)."""

    def test_is_expired_is_pure(self) -> None:
        self.assertFalse(is_expired(dt.date(2026, 7, 27)))
        self.assertTrue(is_expired(dt.date(2099, 1, 1)))

    def test_expired_probe_exits_nonzero_without_requesting(self) -> None:
        out = io.StringIO()
        with (
            unittest.mock.patch("kinozal_scraper.http_fetch.requests.get") as mget,
            contextlib.redirect_stdout(out),
        ):
            code = main(_ENV, sleep=unittest.mock.Mock(), today=dt.date(2099, 1, 1))

        self.assertNotEqual(code, 0)
        self.assertIn("#396", out.getvalue())
        mget.assert_not_called()


class TestPosterPath(unittest.TestCase):
    """RCA дал разные доли для двух путей (страница 1/4, постеры 0/8), поэтому
    «постеры вылечатся тем же обходом» — гипотеза, а не факт. Меряем оба."""

    def test_probes_poster_url_when_page_succeeds(self) -> None:
        page = _page('<html><img src="/assets/image/imageoriginal/jony220.jpg"></html>')
        _, _, mget = _run([page, _poster(), _blocked(), _blocked()])

        poster_calls = [c for c in mget.call_args_list if "assets/image" in c.args[0]]
        self.assertEqual(len(poster_calls), 1)
        self.assertTrue(poster_calls[0].args[0].startswith("https://www.soldoutticketbox.com/"))
        self.assertIn("image/", poster_calls[0].kwargs["headers"]["Accept"])

    def test_poster_line_is_tagged_separately(self) -> None:
        page = _page('<html><img src="/assets/image/imageoriginal/jony220.jpg"></html>')
        _, stdout, _ = _run([page, _blocked(), _blocked(), _blocked()])

        self.assertIn("kind=poster", stdout)

    def test_no_poster_probe_when_page_blocked(self) -> None:
        _, _, mget = _run([_blocked(), _blocked(), _blocked()])

        self.assertFalse([c for c in mget.call_args_list if "assets/image" in c.args[0]])


if __name__ == "__main__":
    unittest.main()
