#!/usr/bin/env python3
"""Измеритель доступности soldoutticketbox.com из CI — ВРЕМЕННЫЙ, до решения по #396.

**Зачем.** RCA #396 установил, что исход ежедневного прогона решает единственная
величина — пробьёт ли хоть одна попытка вероятностный `cf-mitigated=challenge`, — но
исторические логи её не дают: диагностика `describe_block` появилась только 25.07
(#358). Без этого числа выбор между «разнести попытки во времени» (бесплатно) и
«residential-прокси» (платный сервис + секрет) — покупка вслепую.

**Почему отдельный workflow, а не прогон продового.** Единственный способ дёрнуть
fetch из датацентра — `run-script.yml`, а он рассылает в Telegram-канал. Каждая
проверка гипотезы стоила бы пользователю сообщений. Здесь нет ни одного секрета и
ни одной доставки.

**Решающее правило (пред-зарегистрировано в #396).** Тайм-бокс 7 суток, 24 замера в
сутки. Если хотя бы один успех был в КАЖДЫЕ сутки — достаточно разнести попытки,
прокси не покупаем; если хоть в одни сутки 0 из 24 — нужен другой egress.

**Удалить** этот файл, `.github/workflows/soldout-probe.yml` и `tests/test_probe.py`
после решения. Забыть не даст `_EXPIRES`: после этой даты пробник краснеет сам.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from curl_cffi import requests
from curl_cffi.requests.exceptions import HTTPError

# `python scripts/probe.py` кладёт в sys.path только `scripts/`, а editable-install
# пакета есть не в каждом окружении. Тот же bootstrap, что в `ci_check.check_imports`:
# зеркалит pytest'овый `pythonpath=["src"]`, чтобы скрипт резолвил `kinozal_scraper`
# и локально, и на голом раннере.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kinozal_scraper.http_fetch import (  # noqa: E402 — только после sys.path выше
    _HTML_GET,
    _IMAGE_GET,
    _get_once,
    describe_block,
)

_ATTEMPTS = 3
# Длиннее всего retry-окна прода (~7 с: 4 попытки с exp-backoff 1/2/4). Внутри того
# окна попытки бьют в одно и то же решение CF по одному IP — RCA #396 видел 4/4
# отказа подряд на постерах; здесь замеры должны быть разнесены достаточно, чтобы
# показать, коррелированы ли отказы в пределах минут.
_PAUSE_S = 60
# Hard-expiry вместо пункта «удалить при закрытии» в доке: текстовое напоминание в
# длинном pipeline пропускается, exit-code — нет («скрипты > инструкции»). Дата = с
# запасом на мерж поверх 7-суточного тайм-бокса.
_EXPIRES = dt.date(2026, 8, 9)

# Постеры лежат на том же хосте, но берутся другим путём (`Accept: image/*` поверх
# impersonate-профиля). RCA дал разные доли — страница 1/4, постеры 0/8 — поэтому
# «постеры вылечатся тем же обходом» это гипотеза, и её меряем, а не предполагаем.
_POSTER_RE = re.compile(r"""["'](\S*?assets/image/\S+?)["']""")


def is_expired(today: dt.date) -> bool:
    return today > _EXPIRES


def _line(kind: str, attempt: int, elapsed: float, described: str) -> None:
    """Одна машинно-читаемая строка на КАЖДЫЙ исход, включая 200.

    Выражать успех отсутствием строки нельзя: долю успеха пришлось бы читать как
    «сколько строк не хватает» — молчание как данные (§IV), и грепом это не
    собирается. `described` уже начинается со статуса, поэтому `status=` клеится
    к нему, а не дублирует его.
    """
    stamp = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    print(
        f"PROBE ts={stamp} kind={kind} attempt={attempt}/{_ATTEMPTS} "
        f"elapsed={elapsed:.2f}s status={described}",
        flush=True,
    )


def _measure(url: str, kind: str, attempt: int, **kwargs: Any) -> requests.Response | None:
    """Один запрос прод-путём, БЕЗ ретраев; вернуть ответ или None при блокировке.

    `_get_once` — тот же код, что у прода, но до retry-обёртки: мерим вероятность
    ОДНОЙ попытки, продовый ретрай её маскирует, превращая замер в «хоть раз повезло».

    Ловится только `HTTPError` — это исход измерения. Любое другое исключение
    (DNS, TLS, баг здесь) уходит наружу и краснит прогон: измеритель, зеленеющий
    при собственной поломке, бесполезен.
    """
    started = time.monotonic()
    try:
        resp = _get_once(url, **kwargs)
    except HTTPError as exc:
        blocked = exc.response
        body = blocked.content.decode("utf-8", "replace") if blocked.content else ""
        _line(
            kind,
            attempt,
            time.monotonic() - started,
            describe_block(blocked.status_code, blocked.headers, body),
        )
        return None
    _line(
        kind,
        attempt,
        time.monotonic() - started,
        describe_block(resp.status_code, resp.headers, resp.text),
    )
    return resp


def _poster_url(page_url: str, body: str) -> str | None:
    match = _POSTER_RE.search(body)
    return urljoin(page_url, match.group(1)) if match else None


def main(
    env: Mapping[str, str],
    *,
    sleep: Callable[[float], Any] = time.sleep,
    today: dt.date | None = None,
) -> int:
    if is_expired(today or dt.datetime.now(dt.UTC).date()):
        print(
            f"probe expired on {_EXPIRES} — delete scripts/probe.py, "
            "its workflow and tests per #396"
        )
        return 1

    url = (env.get("PROBE_URL") or "").strip()
    if not url:
        # Незаданная `vars.SOLDOUT_URL` разворачивается в "", а не в отсутствующий
        # ключ. Без этой проверки «сбой инструмента» неотличим от «сайт лежит» (§VI).
        print("PROBE_URL is unset or empty — refusing to report a vacuous measurement")
        return 1

    for attempt in range(1, _ATTEMPTS + 1):
        if attempt > 1:
            sleep(_PAUSE_S)
        page = _measure(url, "html", attempt, **_HTML_GET)
        if page is not None and (poster := _poster_url(url, page.text)):
            _measure(poster, "poster", attempt, **_IMAGE_GET)
    return 0


if __name__ == "__main__":
    sys.exit(main(os.environ))
