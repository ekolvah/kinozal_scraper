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

from kinozal_scraper.tmdb_trailer import TmdbVideo

from scripts.eval_trailers import (
    GoldenSetError,
    classify,
    default_strategy,
    evaluate_tmdb,
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

    def test_classify_accepts_pick_in_accept_set(self) -> None:
        # accept-set: у реального фильма часто несколько равноценных RU-дубляжей,
        # любой из них — Hit (RED до membership-classify).
        self.assertEqual(classify(["a", "b"], "b"), "hit")
        self.assertEqual(classify(["a", "b"], "c"), "wrong")
        self.assertEqual(classify(["a", "b"], None), "miss")

    def test_classify_str_correct_backward_compat(self) -> None:
        # legacy single-str форма (miss-branch идиома) остаётся рабочей.
        self.assertEqual(classify("abc", "abc"), "hit")
        self.assertEqual(classify("abc", "xyz"), "wrong")
        self.assertEqual(classify("abc", None), "miss")


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

    def test_load_golden_set_accepts_list_correct(self) -> None:
        # accept-set: несколько равноценных эталонов в пуле (RED до list-loader).
        case = self._valid_case()
        case["correct"] = ["abc", "def"]
        case["candidates"] = [
            {"video_id": "abc", "title": "t1"},
            {"video_id": "def", "title": "t2"},
        ]
        cases = load_golden_set(self._write([case]))
        self.assertEqual(cases[0].correct, ["abc", "def"])

    def test_rejects_empty_accept_set(self) -> None:
        # B2: пустой accept-set — тихий коллапс в null-семантику, маскирует Miss.
        case = self._valid_case()
        case["correct"] = []
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([case]))

    def test_rejects_non_str_accept_element(self) -> None:
        # B2: не-str элемент accept-set → fail-loud, не тихий мусор.
        case = self._valid_case()
        case["correct"] = ["abc", 123]
        case["candidates"] = [{"video_id": "abc", "title": "t"}]
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([case]))

    def test_rejects_accept_id_not_in_candidates(self) -> None:
        # S2: typo-id в accept-set иначе тихо превращает верный pick в wrong.
        case = self._valid_case()
        case["correct"] = ["not_in_pool"]
        case["candidates"] = [{"video_id": "abc", "title": "t"}]
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


# ── #329: TMDB videos-снимок + evaluate_tmdb ──────────────────────────────────


class TestTmdbSnapshot(unittest.TestCase):
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

    def test_loads_tmdb_videos_snapshot(self) -> None:
        # Снимок tmdb_videos парсится в list[TmdbVideo] (RED до поля/парса).
        case = self._valid_case()
        case["tmdb_videos"] = [
            {
                "key": "abc",
                "iso_639_1": "ru",
                "type": "Trailer",
                "official": True,
                "site": "YouTube",
                "name": "официальный трейлер",
            }
        ]
        cases = load_golden_set(self._write([case]))
        vids = cases[0].tmdb_videos
        self.assertEqual(len(vids), 1)
        self.assertIsInstance(vids[0], TmdbVideo)
        self.assertEqual(vids[0].key, "abc")
        self.assertEqual(vids[0].iso_639_1, "ru")
        self.assertEqual(vids[0].type, "Trailer")
        self.assertTrue(vids[0].official)
        self.assertEqual(vids[0].site, "YouTube")

    def test_existing_case_without_snapshot_defaults_empty(self) -> None:
        # Записи без tmdb_videos грузятся (backward-compat) с пустым списком.
        cases = load_golden_set(self._write([self._valid_case()]))
        self.assertEqual(cases[0].tmdb_videos, [])

    def test_rejects_malformed_tmdb_video(self) -> None:
        # fail-loud (§IV): битый video (нет key/iso_639_1/type) → GoldenSetError,
        # не тихий дроп (иначе смещённый пул запекается в снимок).
        case = self._valid_case()
        case["tmdb_videos"] = [{"iso_639_1": "ru", "type": "Trailer", "site": "YouTube"}]
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([case]))

    def test_accept_set_may_reference_tmdb_key_not_in_candidates(self) -> None:
        # Dual-source ground truth: валидный TMDB-key, которого нет в YouTube-пуле,
        # допустим в accept-set — cross-check по (candidates ∪ tmdb keys), не только
        # по candidates (RED до обобщения _parse_correct).
        case = self._valid_case()
        case["correct"] = ["tmdbOnly"]
        case["tmdb_videos"] = [
            {
                "key": "tmdbOnly",
                "iso_639_1": "ru",
                "type": "Trailer",
                "official": True,
                "site": "YouTube",
                "name": "",
            }
        ]
        cases = load_golden_set(self._write([case]))
        self.assertEqual(cases[0].correct, ["tmdbOnly"])

    def test_rejects_accept_id_in_neither_pool(self) -> None:
        # Typo-id, которого нет ни в candidates, ни в tmdb_videos → fail-loud.
        case = self._valid_case()
        case["correct"] = ["ghost"]
        case["tmdb_videos"] = [
            {
                "key": "real",
                "iso_639_1": "ru",
                "type": "Trailer",
                "official": True,
                "site": "YouTube",
                "name": "",
            }
        ]
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([case]))


class TestEvaluateTmdb(unittest.TestCase):
    def _write(self, data: Any) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.addCleanup(os.unlink, path)
        return path

    def test_evaluate_tmdb_classifies_against_correct(self) -> None:
        # evaluate_tmdb гоняет pick_trailer по замороженному tmdb_videos и
        # классифицирует против accept-set: Hit / Wrong / Miss (RED до evaluate_tmdb).
        hit_case = {
            "film": {"ru_title": "A", "original_title": "A", "year": 2026},
            "correct": ["ruKey"],
            "candidates": [{"video_id": "yt1", "title": "A 2026 трейлер"}],
            "tmdb_videos": [
                {
                    "key": "ruKey",
                    "iso_639_1": "ru",
                    "type": "Trailer",
                    "official": False,
                    "site": "YouTube",
                    "name": "",
                }
            ],
            "note": "",
        }
        wrong_case = {
            "film": {"ru_title": "B", "original_title": "B", "year": 2026},
            "correct": ["ruWant"],
            "candidates": [{"video_id": "ruWant", "title": "B 2026 трейлер"}],
            "tmdb_videos": [
                {
                    "key": "enOnly",
                    "iso_639_1": "en",
                    "type": "Trailer",
                    "official": True,
                    "site": "YouTube",
                    "name": "",
                }
            ],
            "note": "",
        }
        miss_case = {
            "film": {"ru_title": "C", "original_title": "C", "year": 2026},
            "correct": ["ruC"],
            "candidates": [{"video_id": "ruC", "title": "C 2026 трейлер"}],
            "tmdb_videos": [],
            "note": "",
        }
        cases = load_golden_set(self._write([hit_case, wrong_case, miss_case]))
        rows, total = evaluate_tmdb(cases)
        outcomes = [o for _, _, o in rows]
        self.assertEqual(outcomes, ["hit", "wrong", "miss"])
        self.assertEqual(total, 1 - 2 + 0)


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
