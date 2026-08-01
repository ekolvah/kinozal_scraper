#!/usr/bin/env python3
"""Детектор дрейфа: объявленные required status checks ↔ фактические в GitHub (#436).

    python scripts/check_branch_protection.py

Печатает фактический состав контекстов ветки `main` **всегда**, на любом коде выхода — это и
есть воспроизводимый способ его увидеть, не открывая настройки руками. Коды: `0` совпало,
`1` дрейф, `2` отказ инструмента (`gh` недоступен/без прав, битый JSON, сломанный захват вывода
#364/#410). Разделение `1` и `2` — §IV: отказ инструмента не имеет права выглядеть как вердикт
«дрейфа нет».

**Почему дом объявления здесь, а не в доке.** Состав контекстов машинно сверяется — и с GitHub
(этот скрипт), и с воркфлоу репо (`tests/test_branch_protection.py`). Проза сверяться не умеет,
поэтому доки на `REQUIRED_CONTEXTS` ссылаются, а не пересказывают его (тот же приём, что
`scripts/set_issue_priority.py`).

**Почему дев-скрипт в `.githooks/pre-push`, а не job в CI.** У `GITHUB_TOKEN` нет скоупа
`administration`, а чтение `branches/*/protection` требует admin-прав, поэтому CI-форма требует
положить в secrets отдельный токен (fine-grained PAT с `Administration: read` или GitHub App).
Радиус такого токена узкий, то есть форма рабочая — отвергнута она **по цене**: долгоживущий
секрет приносит обязанность ротации, а протухший токен красит job без реального дрейфа и учит
игнорировать детектор. Плюс `ci.yml` зеркалит реестр `CHECKS` из `ci_check.py`
(`tests/test_ci_check.py::TestStepParity`), так что чек в `ci_check` автоматически обязан стать
шагом CI — где и упрётся в права. Обходной путь через ruleset-эндпоинт (`repos/{owner}/{repo}/
rules/branches/main`, читается обычным repo-read) для этого репо пуст: enforcement живёт в classic
branch protection, которая туда не попадает. Пересматривать выбор уместно при переезде на rulesets
или при появлении второго контрибьютора.

**Хук — не enforcement.** `.githooks` включается опциональным `git config core.hooksPath`, так что
это surfacing на машине мейнтейнера. Единственный авторитетный барьер для `main` — сама branch
protection.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import yaml

# Канон состава required-контекстов ветки `main`.
REQUIRED_CONTEXTS: tuple[str, ...] = ("quality", "pr-link", "agent-review-gate")

# PR-джобы, сознательно НЕ сделанные required, и почему. Пустая причина — забытое решение,
# а не принятое, поэтому гард её не принимает.
NOT_REQUIRED: dict[str, str] = {}

BRANCH = "main"
# Плейсхолдеры `{owner}`/`{repo}` подставляет сам `gh`; без ведущего слэша — иначе MSYS
# на Windows подменит путь (CLAUDE.md §Среда).
_ENDPOINT = f"repos/{{owner}}/{{repo}}/branches/{BRANCH}/protection"
# Одиночный GET к GitHub API; щедро, но конечно — эта проверка стоит перед восьмиминутным
# ci_check, и висеть без вывода ей нельзя.
_GH_TIMEOUT_S = 30


def protection_drift(
    actual: Iterable[str], expected: Iterable[str] = REQUIRED_CONTEXTS
) -> tuple[list[str], list[str]]:
    """`(missing, unexpected)` — расхождение по обоим направлениям.

    Сравнение по множеству: порядок в `checks` GitHub не гарантирует. Незаявленный контекст —
    такое же расхождение, как отсутствующий: канон состава живёт в репо, и «кто-то добавил
    руками» должно доходить до оператора, а не считаться нормой.
    """
    actual_set, expected_set = set(actual), set(expected)
    return sorted(expected_set - actual_set), sorted(actual_set - expected_set)


def contexts_from_protection(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Фактические контексты из ответа branch-protection API.

    Читается `required_status_checks.checks[*].context` — недеприкейченная форма, та же, что
    пишется при обновлении. Отсутствие ключа даёт пустой кортеж, то есть **дрейф**, а не сбой:
    снесённый protection — самый вероятный реальный сценарий, и молчать о нём нельзя.
    """
    # `or {}` на каждом шаге, а не только дефолт `.get`: явный JSON `null` в ответе — это не
    # отсутствующий ключ, и без него `.get` на `None` кинул бы AttributeError, то есть отказ
    # инструмента ушёл бы наружу под кодом 1 («дрейф») — ровно та подмена, от которой
    # `fetch_protection` защищается.
    checks = (payload.get("required_status_checks") or {}).get("checks") or []
    return tuple(check["context"] for check in checks)


def load_workflows(directory: Path) -> dict[str, Mapping[Any, Any]]:
    """Распарсить воркфлоу каталога: имя файла → документ.

    Оба расширения, потому что GitHub Actions принимает и `.yml`, и `.yaml`: гард, который
    смотрит только на первое, пропустил бы новый PR-джоб в `.yaml` молча и вакуумно позеленел
    (§IV — в том самом гарде, чья работа это ловить). Пустой файл `safe_load` отдаёт как `None`,
    нормализуем в `{}`, иначе обход упал бы трейсбеком вместо вердикта.
    """
    paths = sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")])
    return {path.name: yaml.safe_load(path.read_text(encoding="utf-8")) or {} for path in paths}


def _triggers(doc: Mapping[Any, Any]) -> Any:
    """Секция `on:` воркфлоу. YAML 1.1 читает голое `on` как булев `True` — берём оба ключа.

    Отсюда и `Mapping[Any, Any]` у документа: ключи воркфлоу — не только строки, и сузить тип
    до `str` значило бы описать не тот объект, который реально приходит из парсера.
    """
    return doc.get("on", doc.get(True, {}))


def _runs_on_pull_request(triggers: Any) -> bool:
    """Триггерится ли воркфлоу на `pull_request` (dict / список / строка)."""
    if isinstance(triggers, Mapping):
        return "pull_request" in triggers
    if isinstance(triggers, str):
        return triggers == "pull_request"
    if isinstance(triggers, Iterable):
        return "pull_request" in triggers
    return False


# Фильтры на `pull_request`-триггере: с ними джоб отчитывается не на каждом PR, а required-контекст
# обязан отчитаться на любом — иначе он навсегда «Expected».
_TRIGGER_FILTERS = ("paths", "paths-ignore", "branches", "branches-ignore")


class _Job(NamedTuple):
    """Определение джоба вместе с фильтрами воркфлоу, который его несёт."""

    definition: Mapping[str, Any]
    filters: tuple[str, ...]


def _pull_request_filters(triggers: Any) -> tuple[str, ...]:
    """Фильтры на `pull_request`-триггере, из-за которых джоб отчитается не на каждом PR."""
    if not isinstance(triggers, Mapping):
        return ()
    config = triggers.get("pull_request")
    if not isinstance(config, Mapping):
        return ()
    return tuple(key for key in _TRIGGER_FILTERS if key in config)


def _pull_request_jobs(
    workflows: Mapping[str, Mapping[Any, Any]],
) -> tuple[dict[str, _Job], list[str]]:
    """Эффективное имя check-run'а → джоб, плюс список имён, встретившихся дважды.

    Имя контекста — это `name:` джоба, если он задан, иначе ключ джоба. Сверка по ключу
    сломалась бы молча при добавлении `name:`, оставив required-контекст навсегда в статусе
    «Expected»; при `enforce_admins: true` это заперло бы мёрдж, включая PR с починкой.
    Коллизия имён возвращается отдельно, а не затирается молча: какой из двух джобов отчитается
    под этим контекстом — неопределённость, а не деталь.
    """
    jobs: dict[str, _Job] = {}
    duplicates: list[str] = []
    for doc in workflows.values():
        triggers = _triggers(doc)
        if not _runs_on_pull_request(triggers):
            continue
        filters = _pull_request_filters(triggers)
        for job_key, job in (doc.get("jobs") or {}).items():
            context = (job or {}).get("name") or job_key
            if context in jobs:
                duplicates.append(context)
            jobs[context] = _Job(job or {}, filters)
    return jobs, duplicates


def _declared_problems(declared: tuple[str, ...], jobs: Mapping[str, _Job]) -> list[str]:
    """Проблемы объявленных контекстов: нет джоба, матрица, фильтр на триггере."""
    problems: list[str] = []
    for context in declared:
        job = jobs.get(context)
        if job is None:
            problems.append(
                f"объявлен required-контекст {context!r}, но PR-джоба с таким именем check-run'а "
                f"нет — он навсегда останется в статусе «Expected» и запрёт мёрдж"
            )
            continue
        if "matrix" in (job.definition.get("strategy") or {}):
            problems.append(
                f"{context!r} объявлен голым именем, но джоб использует matrix — реальные "
                f"контексты будут вида '{context} (value)'"
            )
        if job.filters:
            problems.append(
                f"{context!r} объявлен required, но его воркфлоу фильтрует `pull_request` по "
                f"{', '.join(job.filters)} — на PR, не прошедшем фильтр, контекст не отчитается "
                f"вовсе и запрёт мёрдж, включая PR со снятием фильтра"
            )
    return problems


def _undeclared_problems(
    declared: tuple[str, ...], jobs: Mapping[str, _Job], excluded: Mapping[str, str]
) -> list[str]:
    """Проблемы остальных: решение не принято, исключение без причины, протухшее исключение."""
    problems: list[str] = []
    for context in sorted(jobs):
        if context in declared:
            continue
        if context not in excluded:
            problems.append(
                f"PR-джоб {context!r} не объявлен required и не внесён в NOT_REQUIRED — "
                f"решение не принято, а забыто"
            )
        elif not excluded[context].strip():
            problems.append(f"{context!r} исключён из required без причины")
    for context in sorted(excluded):
        if context not in jobs:
            problems.append(
                f"{context!r} числится в NOT_REQUIRED, но PR-джоба с таким именем больше нет — "
                f"протухшее исключение переживает свой джоб и молча ничего не значит"
            )
        elif context in declared:
            problems.append(f"{context!r} одновременно в REQUIRED_CONTEXTS и NOT_REQUIRED")
    return problems


def declaration_problems(
    workflows: Mapping[str, Mapping[Any, Any]],
    declared: Iterable[str] = REQUIRED_CONTEXTS,
    excluded: Mapping[str, str] = NOT_REQUIRED,
) -> list[str]:
    """Расхождения объявления с воркфлоу репо — оффлайн-половина гарда.

    Ловит то, что сетевая половина увидеть не может: контекст без джоба (вечный «Expected»),
    джоб с матрицей или с фильтром на триггере (контекст отчитается не всегда), новый PR-джоб,
    про который решение «required или нет» не принято, и протухшее/противоречивое исключение.
    """
    jobs, duplicates = _pull_request_jobs(workflows)
    declared = tuple(declared)
    return [
        *(
            f"имя check-run'а {context!r} принадлежит более чем одному PR-джобу — какой из них "
            f"отчитается под этим контекстом, неопределено"
            for context in sorted(set(duplicates))
        ),
        *_declared_problems(declared, jobs),
        *_undeclared_problems(declared, jobs, excluded),
    ]


def fetch_protection() -> Mapping[str, Any]:
    """Прочитать branch protection через `gh api`; отказ инструмента → exit 2.

    Отдельный код 2 (а не пустой словарь) намеренно: транзиентный сбой `gh` (auth, rate-limit,
    сеть) не должен быть истолкован как «required-контекстов нет», то есть как дрейф — это ложный
    диагноз с противоположным лечением.
    """
    try:
        result = subprocess.run(
            ["gh", "api", _ENDPOINT],
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=_GH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        # Без таймаута зависший `gh` (прокси, DNS-чёрная дыра) повесил бы pre-push без вывода —
        # а `CLAUDE.md` §Среда прямо учит оператора не убивать долгий pre-push.
        print(
            f"error: `gh api {_ENDPOINT}` не ответил за {_GH_TIMEOUT_S} с — проверка не выполнена.",
            file=sys.stderr,
        )
        sys.exit(2)
    if result.stdout is None or result.stderr is None:
        print(
            f"error: capture failed for `gh api {_ENDPOINT}` (rc={result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
            file=sys.stderr,
        )
        sys.exit(2)
    if result.returncode != 0:
        print(
            f"error: `gh api {_ENDPOINT}` failed (rc={result.returncode}): "
            f"{result.stderr.strip()} — нужен `gh auth` с admin-правами на репозиторий.",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        payload: Mapping[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(
            f"error: `gh api {_ENDPOINT}` вернул неразбираемый ответ ({exc}): "
            f"{result.stdout[:200]!r}",
            file=sys.stderr,
        )
        sys.exit(2)
    return payload


def main(argv: list[str] | None = None) -> None:
    """Напечатать фактический состав контекстов и вернуть вердикт кодом выхода."""
    parser = argparse.ArgumentParser(
        description=f"Сверить required status checks ветки `{BRANCH}` с объявлением в этом файле."
    )
    parser.parse_args(argv)

    actual = contexts_from_protection(fetch_protection())
    print(f"required status checks on `{BRANCH}`: {', '.join(actual) or '(none)'}")
    print(f"declared in {Path(__file__).name}: {', '.join(REQUIRED_CONTEXTS)}")

    missing, unexpected = protection_drift(actual)
    if not missing and not unexpected:
        return

    if missing:
        body = json.dumps(
            {"strict": True, "checks": [{"context": c} for c in REQUIRED_CONTEXTS]},
            ensure_ascii=False,
        )
        print(
            f"error: объявлены, но НЕ являются required: {', '.join(missing)} — гейт краснеет "
            f"в UI и ничего не блокирует (ровно дефект #436).\n"
            f"  починка: echo '{body}' | gh api --method PATCH "
            f"{_ENDPOINT}/required_status_checks --input -",
            file=sys.stderr,
        )
    if unexpected:
        print(
            f"error: required в GitHub, но не объявлены здесь: {', '.join(unexpected)} — если "
            f"контекст добавлен намеренно, легальный путь один: внести его в REQUIRED_CONTEXTS "
            f"тем же PR.",
            file=sys.stderr,
        )
    sys.exit(1)


if __name__ == "__main__":
    main()
