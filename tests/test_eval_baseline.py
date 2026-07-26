"""RED-тесты #379: baseline-храповик поверх delivery-скоркарты трейлеров.

Дефект, который чинится: в #359 регресс качества 26→16 прошёл architect-review,
`ci_check`, `claude-review` и три зелёных CI-чека — метрика лежала в репозитории
готовая, но никто не был обязан её запускать. Здесь метрика становится гейтом:
закоммиченный baseline сверяется с реальным прогоном на каждом `pytest`.

Носитель гейта один — `TestBaselineGate::test_committed_baseline_matches_main`
(отдельной записи в `CHECKS`/`ci.yml` нет: `ci_check` уже гоняет `pytest`, а
`.githooks/pre-push` — `ci_check`).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kinozal_scraper.kinozal_pipeline import _TRAILER_MISS_MARKER
from kinozal_scraper.trailer_strategy import FilmProfile, HeuristicStrategy
from scripts.eval_trailers import (
    BASELINE_PATH,
    GoldenCase,
    GoldenSetError,
    Outcome,
    build_baseline,
    compare_to_baseline,
    evaluate_delivery,
    load_baseline,
    load_golden_set,
)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "trailer_golden.json"

# Порог из #359 — подавление низкоуверенных picks. Держится ЗДЕСЬ, а не в `src`:
# в проде эта политика откачена, и `experimental_select` там был бы мёртвым кодом.
_CONFIDENCE_FLOOR = 0.5


def _select_with_359_confidence_gate(profile: FilmProfile, youtube: Any) -> str:
    """Политика, откаченная в #359: pick с `confidence < 0.5` давится в miss-маркер.

    Частичный повтор тела прод-`select_trailer` — намеренный (N3 architect-review):
    контрфактическая политика не имеет права жить в `src`, а assert «вариант хуже
    прода на том же наборе» остаётся осмысленным при любом дрейфе прода.
    """
    pick = HeuristicStrategy().pick(profile, youtube.search_candidates(profile))
    if pick.video_id is None or pick.confidence < _CONFIDENCE_FLOOR:
        return _TRAILER_MISS_MARKER
    return f"https://www.youtube.com/watch?v={pick.video_id}"


class _CompareCase(unittest.TestCase):
    """Мини-golden-set из двух кейсов: один попадает, второй промахивается."""

    def _write(self, data: Any) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.addCleanup(os.unlink, path)
        return path

    def _cases(self) -> list[Any]:
        return load_golden_set(
            self._write(
                [
                    {
                        "film": {"ru_title": "Гнев", "original_title": "Man on Fire", "year": 2026},
                        "correct": "ru01",
                        "candidates": [{"video_id": "ru01", "title": "Гнев 2026 трейлер"}],
                        "note": "",
                    },
                    {
                        "film": {"ru_title": "Волк", "original_title": "Wolf", "year": 2026},
                        "correct": "ru02",
                        "candidates": [],
                        "note": "",
                    },
                ]
            )
        )


class TestCompare(_CompareCase):
    def test_match_reports_ok(self) -> None:
        rows, _ = evaluate_delivery(self._cases())
        report = compare_to_baseline(build_baseline(rows), rows)
        self.assertTrue(report.ok, report.text)

    def test_regression_names_moved_films(self) -> None:
        # Просадка обязана назвать ФИЛЬМ и переход, а не только дельту score:
        # #359 нужно было знать, какие именно 10 picks подавились.
        rows, _ = evaluate_delivery(self._cases())
        baseline = build_baseline(rows)
        degraded: list[tuple[GoldenCase, str | None, Outcome]] = [
            (case, None, "miss") for case, _, _ in rows
        ]
        report = compare_to_baseline(baseline, degraded)
        self.assertFalse(report.ok)
        self.assertIn("Гнев", report.text)
        self.assertIn("hit", report.text)
        self.assertIn("miss", report.text)

    def test_improvement_also_not_ok(self) -> None:
        # Асимметричный гейт («улучшение = зелёное с предупреждением») воспроизводит
        # чинимый дефект: сигнал, который никто не обязан прочитать (§IV). Плюс после
        # #380 суммарно-положительная дельта сможет спрятать своп hit→wrong.
        rows, _ = evaluate_delivery(self._cases())
        baseline = build_baseline(rows)
        improved: list[tuple[GoldenCase, str | None, Outcome]] = [
            (case, "ru01", "hit") for case, _, _ in rows
        ]
        report = compare_to_baseline(baseline, improved)
        self.assertFalse(report.ok)
        self.assertIn("--update-baseline", report.text)

    def test_desynced_length_fails_loud(self) -> None:
        rows, _ = evaluate_delivery(self._cases())
        with self.assertRaises(GoldenSetError):
            compare_to_baseline(build_baseline(rows)[:1], rows)

    def test_desynced_film_name_fails_loud(self) -> None:
        # Сравнение по позиции без сверки имени тихо сопоставило бы разные фильмы.
        rows, _ = evaluate_delivery(self._cases())
        baseline = build_baseline(rows)
        baseline[0].film = "Совсем другой фильм"
        with self.assertRaises(GoldenSetError):
            compare_to_baseline(baseline, rows)

    def test_load_baseline_rejects_malformed_entry(self) -> None:
        with self.assertRaises(GoldenSetError):
            load_baseline(self._write([{"i": 0, "film": "Гнев"}]))

    def test_load_baseline_rejects_unknown_outcome(self) -> None:
        with self.assertRaises(GoldenSetError):
            load_baseline(self._write([{"i": 0, "film": "Гнев", "outcome": "maybe"}]))


class TestBaselineGate(unittest.TestCase):
    """Сам гейт: закоммиченный baseline против реального golden-set."""

    def test_committed_baseline_matches_main(self) -> None:
        cases = load_golden_set(GOLDEN_PATH)
        rows, _ = evaluate_delivery(cases)
        report = compare_to_baseline(load_baseline(BASELINE_PATH), rows)
        self.assertTrue(report.ok, report.text)

    def test_reverted_359_policy_fails_the_gate(self) -> None:
        # Acceptance #379: изменение, идентичное откаченному в #359, доходит до
        # вердикта гейта — не «score стал ниже», а `ok=False` с именами фильмов.
        # Между «score упал» и «гейт сработал» лежит вся сравнивающая логика, то
        # есть ровно то, что должно было поймать #359.
        cases = load_golden_set(GOLDEN_PATH)
        rows, _ = evaluate_delivery(cases, select=_select_with_359_confidence_gate)
        report = compare_to_baseline(load_baseline(BASELINE_PATH), rows)
        self.assertFalse(report.ok, "подавление низкоуверенных picks обязано валить гейт")
        self.assertIn("hit→miss", report.text)
