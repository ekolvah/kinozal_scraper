#!/usr/bin/env python3
"""Заглушка-сигнатура для RED-шага #436 — реализация приходит GREEN-коммитом."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

REQUIRED_CONTEXTS: tuple[str, ...] = ()
NOT_REQUIRED: dict[str, str] = {}


def protection_drift(
    actual: Iterable[str], expected: Iterable[str] = REQUIRED_CONTEXTS
) -> tuple[list[str], list[str]]:
    """Расхождение объявленного состава контекстов с фактическим."""
    raise NotImplementedError


def contexts_from_protection(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Фактические required-контексты из ответа branch-protection API."""
    raise NotImplementedError


def declaration_problems(
    workflows: Mapping[str, Mapping[str, Any]],
    declared: Iterable[str],
    excluded: Mapping[str, str],
) -> list[str]:
    """Расхождения объявления с воркфлоу репо (оффлайн-половина гарда)."""
    raise NotImplementedError


def fetch_protection() -> Mapping[str, Any]:
    """Прочитать branch protection ветки `main` через `gh api`."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> None:
    """Напечатать фактический состав контекстов и вернуть вердикт кодом выхода."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
