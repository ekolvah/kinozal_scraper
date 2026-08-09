"""RED tests for #379: a baseline ratchet over the trailer delivery scorecard.

Defect addressed: in #359, a quality regression from 26→16 passed architect review,
`ci_check`, `claude-review`, and three green CI checks—the metric was ready in the repository,
but nobody had to run it. Here the metric becomes a gate: the committed baseline is compared
with a real run on every `pytest`.

The gate has one carrier—`TestBaselineGate::test_committed_baseline_matches_main`
(there is no separate entry in `CHECKS`/`ci.yml`: `ci_check` already runs `pytest`, and
`.githooks/pre-push` runs `ci_check`).
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
    classify,
    compare_to_baseline,
    evaluate_delivery,
    load_baseline,
    load_golden_set,
)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "trailer_golden.json"

# The #359 threshold suppresses low-confidence picks. It belongs HERE, not in `src`:
# production reverted this policy, and `experimental_select` there would be dead code.
_CONFIDENCE_FLOOR = 0.5


def _select_with_359_confidence_gate(profile: FilmProfile, youtube: Any) -> str:
    """Policy reverted in #359: a pick with `confidence < 0.5` is suppressed to the miss marker.

    Partial repetition of production `select_trailer` is intentional (N3 architect review):
    a counterfactual policy must not live in `src`, while the assertion that “the variant is worse
    than production on the same set” remains meaningful under any production drift.
    """
    pick = HeuristicStrategy().pick(profile, youtube.search_candidates(profile))
    if pick.video_id is None or pick.confidence < _CONFIDENCE_FLOOR:
        return _TRAILER_MISS_MARKER
    return f"https://www.youtube.com/watch?v={pick.video_id}"


class _CompareCase(unittest.TestCase):
    """A two-case mini golden set: one hit and one miss."""

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
        # A regression must name the FILM and transition, not only the score delta:
        # #359 needed to show which exact 10 picks were suppressed.
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
        # An asymmetric gate (“improvement = green with warning”) reproduces the addressed
        # defect: a signal nobody must read (§IV). After #380, a net-positive delta could also
        # hide a hit→wrong swap.
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
        # Position-only comparison without name checking would silently pair different films.
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


class TestWrongPole(unittest.TestCase):
    """#380: the metric must have a live negative pole.

    The `Hit +1 / Miss 0 / Wrong −2` scale declares a foreign trailer twice as bad as
    an honest marker, but in set #327 `wrong` occurred NOT ONCE: every case was built as
    “the correct answer exists, find it.” Half the scale was dead—the set could only
    penalize caution (#359: −10 hit) and could not show “how many wrong outcomes were prevented.”

    The gate is a FIXTURE invariant, not a claim about strategy: it counts `trap`
    annotations, so selection improvements do not make it red (otherwise it would penalize the
    change it was created for), while it still catches hollowing out the annotations.
    """

    _MIN_TRAP_CASES = 3

    def test_golden_set_keeps_verified_traps(self) -> None:
        cases = load_golden_set(GOLDEN_PATH)
        marked = [c.film.ru_title for c in cases if c.trap]
        self.assertGreaterEqual(
            len(marked),
            self._MIN_TRAP_CASES,
            "в наборе не осталось кейсов с верифицированным чужим кандидатом — "
            "метрика снова слепа к изменениям, защищающим от чужих ссылок (#380); "
            f"размечены: {marked}",
        )

    def test_trap_pick_classifies_as_wrong(self) -> None:
        # A link between annotation and scale, not a declaration: `trap` does not participate
        # in scoring (it does not change #380 weights), so “trap = wrong” rests solely on the
        # disjointness of trap and accept set validated by the loader. This test pins that consequence on real cases.
        for case in (c for c in load_golden_set(GOLDEN_PATH) if c.trap):
            for trap_id in case.trap:
                with self.subTest(film=case.film.ru_title, trap=trap_id):
                    self.assertEqual(classify(case.correct, trap_id), "wrong")


class TestBaselineGate(unittest.TestCase):
    """The gate itself: committed baseline against the real golden set."""

    def test_committed_baseline_matches_main(self) -> None:
        cases = load_golden_set(GOLDEN_PATH)
        rows, _ = evaluate_delivery(cases)
        report = compare_to_baseline(load_baseline(BASELINE_PATH), rows)
        self.assertTrue(report.ok, report.text)

    def test_reverted_359_policy_fails_the_gate(self) -> None:
        # Acceptance #379: a change identical to the one reverted in #359 reaches the gate
        # verdict—not “score became lower,” but `ok=False` with film names. All comparison logic
        # lies between “score fell” and “gate fired,” exactly what #359 should have caught.
        cases = load_golden_set(GOLDEN_PATH)
        rows, _ = evaluate_delivery(cases, select=_select_with_359_confidence_gate)
        report = compare_to_baseline(load_baseline(BASELINE_PATH), rows)
        self.assertFalse(report.ok, "подавление низкоуверенных picks обязано валить гейт")
        self.assertIn("hit→miss", report.text)
