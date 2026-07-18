"""RED tests for #139: eval-harness (classify / score / load_golden_set / --record).

Классификация считается ОТНОСИТЕЛЬНО `correct` с явной null-веткой (BLOCKING-2);
load_golden_set fail-loud на битой записи, НЕ деградирует в Miss (BLOCKING-1);
#138-seed красный baseline пришпилен xfail(strict) → фикс #141 станет audible.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from scripts.eval_trailers import (
    GoldenSetError,
    classify,
    default_strategy,
    load_golden_set,
    main,
    score,
)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "trailer_golden.json"


# ── classify: 4 исхода относительно correct ───────────────────────────────────


class TestClassify(unittest.TestCase):
    def test_hit_when_pick_equals_correct(self) -> None:
        self.assertEqual(classify("abc", "abc"), "hit")

    def test_wrong_when_pick_is_other_id(self) -> None:
        self.assertEqual(classify("abc", "xyz"), "wrong")

    def test_miss_when_pick_none_but_correct_exists(self) -> None:
        self.assertEqual(classify("abc", None), "miss")

    def test_hit_when_correct_null_and_pick_none(self) -> None:
        # correct=NONE, ничего не выбрали — правильно.
        self.assertEqual(classify(None, None), "hit")

    def test_wrong_when_correct_null_but_pick_set(self) -> None:
        # correct=NONE, но что-то выбрали — навязали лишний трейлер.
        self.assertEqual(classify(None, "abc"), "wrong")


# ── score: Hit +1 / Miss 0 / Wrong −2 ─────────────────────────────────────────


class TestScore(unittest.TestCase):
    def test_weighted_totals(self) -> None:
        self.assertEqual(score(["hit", "hit", "miss", "wrong"]), 1 + 1 + 0 - 2)


# ── load_golden_set: fail-loud валидация ──────────────────────────────────────


class TestLoadGoldenSet(unittest.TestCase):
    def _write(self, data: Any) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.addCleanup(os.unlink, path)
        return path

    def _valid_case(self) -> dict[str, Any]:
        return {
            "film": {"ru_title": "Гнев", "original_title": "Man on Fire", "year": 2026},
            "correct": "abc",
            "candidates": [{"video_id": "abc", "title": "Гнев 2026 трейлер"}],
            "note": "",
        }

    def test_loads_valid_set(self) -> None:
        cases = load_golden_set(self._write([self._valid_case()]))
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].correct, "abc")
        self.assertEqual(cases[0].film.original_title, "Man on Fire")
        self.assertEqual(cases[0].candidates[0].video_id, "abc")

    def test_null_correct_loads(self) -> None:
        case = self._valid_case()
        case["correct"] = None
        cases = load_golden_set(self._write([case]))
        self.assertIsNone(cases[0].correct)

    def test_empty_set_errors(self) -> None:
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([]))

    def test_missing_field_errors(self) -> None:
        case = self._valid_case()
        del case["candidates"]
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([case]))

    def test_duplicate_candidate_id_errors(self) -> None:
        case = self._valid_case()
        case["candidates"] = [
            {"video_id": "dup", "title": "a"},
            {"video_id": "dup", "title": "b"},
        ]
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([case]))

    def test_correct_wrong_type_errors(self) -> None:
        case = self._valid_case()
        case["correct"] = 123
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([case]))


# ── #140: golden-loader несёт метаданные FilmProfile ──────────────────────────


class TestParseFilmMetadata(unittest.TestCase):
    def _write(self, data: Any) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.addCleanup(os.unlink, path)
        return path

    def test_carries_metadata_fields_with_defaults(self) -> None:
        case = {
            "film": {
                "ru_title": "Гнев",
                "original_title": "Man on Fire",
                "year": 2026,
                "cast": ["Дензел Вашингтон"],
                "director": "Тони Скотт",
                "genre": "боевик",
                "description": "Сюжет.",
            },
            "correct": "abc",
            "candidates": [{"video_id": "abc", "title": "Гнев 2026 трейлер"}],
            "note": "",
        }
        film = load_golden_set(self._write([case]))[0].film
        self.assertEqual(film.cast, ["Дензел Вашингтон"])
        self.assertEqual(film.director, "Тони Скотт")
        self.assertEqual(film.genre, "боевик")
        self.assertEqual(film.description, "Сюжет.")

    def test_existing_golden_loads_unchanged(self) -> None:
        # Записи без новых полей грузятся (backward-compat) с пустыми дефолтами.
        cases = load_golden_set(GOLDEN_PATH)
        self.assertTrue(cases)
        self.assertEqual(cases[0].film.cast, [])
        self.assertEqual(cases[0].film.director, "")


# ── --record: fail-fast без API_KEY ───────────────────────────────────────────


class TestRecordMode(unittest.TestCase):
    def test_missing_api_key_fails_fast(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("API_KEY", None)
            with self.assertRaises(SystemExit):
                main(["--record", "--golden", str(GOLDEN_PATH)])


# ── #138 red baseline: known-gap guard ────────────────────────────────────────


def test_138_seed_cases_present() -> None:
    cases = load_golden_set(GOLDEN_PATH)
    assert any(c.note.startswith("seed: #138") for c in cases), (
        "golden-set обязан засеять #138 RU-промахи"
    )


def test_138_ru_selection_is_fixed() -> None:
    # Инверсия бывшего xfail-guard (§I): #141 сменил default_strategy() на
    # язык-aware HeuristicStrategy, а union-retrieval #140 внёс RU в пул всех
    # #138-seed кейсов → отбор берёт RU-эталон → все hit. Guard теперь зелёный
    # и пришпиливает фикс: регресс RU-selection снова покраснеет.
    cases = load_golden_set(GOLDEN_PATH)
    seed = [c for c in cases if c.note.startswith("seed: #138")]
    strat = default_strategy()
    for c in seed:
        pick = strat.pick(c.film, c.candidates)
        assert classify(c.correct, pick.video_id) == "hit"
