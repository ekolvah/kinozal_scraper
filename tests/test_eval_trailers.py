"""RED tests for #139: eval-harness (classify / score / load_golden_set / --record).

Classification is computed RELATIVE TO `correct`, with an explicit null branch (BLOCKING-2);
load_golden_set fails loudly on a malformed record and does NOT degrade to Miss (BLOCKING-1);
the #138-seed red baseline is pinned by xfail(strict) → fix #141 becomes audible.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kinozal_scraper.kinozal_pipeline import _TRAILER_ERROR_MARKER
from kinozal_scraper.tmdb_trailer import TmdbVideo
from kinozal_scraper.trailer_strategy import Candidate
from scripts.eval_trailers import (
    GoldenSetError,
    classify,
    default_strategy,
    evaluate,
    evaluate_delivery,
    evaluate_tmdb,
    load_golden_set,
    main,
    score,
)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "trailer_golden.json"


# ── classify: 4 outcomes relative to correct ──────────────────────────────────


class TestClassify(unittest.TestCase):
    def test_hit_when_pick_equals_correct(self) -> None:
        self.assertEqual(classify("abc", "abc"), "hit")

    def test_wrong_when_pick_is_other_id(self) -> None:
        self.assertEqual(classify("abc", "xyz"), "wrong")

    def test_miss_when_pick_none_but_correct_exists(self) -> None:
        self.assertEqual(classify("abc", None), "miss")

    def test_hit_when_correct_null_and_pick_none(self) -> None:
        # correct=NONE, selected nothing—correct.
        self.assertEqual(classify(None, None), "hit")

    def test_wrong_when_correct_null_but_pick_set(self) -> None:
        # correct=NONE, but selected something—an unnecessary trailer was imposed.
        self.assertEqual(classify(None, "abc"), "wrong")

    def test_classify_accepts_pick_in_accept_set(self) -> None:
        # accept set: a real film often has several equivalent RU dubs;
        # any of them is a Hit (RED before membership classification).
        self.assertEqual(classify(["a", "b"], "b"), "hit")
        self.assertEqual(classify(["a", "b"], "c"), "wrong")
        self.assertEqual(classify(["a", "b"], None), "miss")

    def test_classify_str_correct_backward_compat(self) -> None:
        # The legacy single-str form (miss-branch idiom) remains valid.
        self.assertEqual(classify("abc", "abc"), "hit")
        self.assertEqual(classify("abc", "xyz"), "wrong")
        self.assertEqual(classify("abc", None), "miss")


# ── score: Hit +1 / Miss 0 / Wrong −2 ─────────────────────────────────────────


class TestScore(unittest.TestCase):
    def test_weighted_totals(self) -> None:
        self.assertEqual(score(["hit", "hit", "miss", "wrong"]), 1 + 1 + 0 - 2)


# ── load_golden_set: fail-loud validation ─────────────────────────────────────


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
        # accept set: several equivalent references in the pool (RED before list loader).
        case = self._valid_case()
        case["correct"] = ["abc", "def"]
        case["candidates"] = [
            {"video_id": "abc", "title": "t1"},
            {"video_id": "def", "title": "t2"},
        ]
        cases = load_golden_set(self._write([case]))
        self.assertEqual(cases[0].correct, ["abc", "def"])

    def test_rejects_empty_accept_set(self) -> None:
        # B2: an empty accept set silently collapses to null semantics, masking a Miss.
        case = self._valid_case()
        case["correct"] = []
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([case]))

    def test_rejects_non_str_accept_element(self) -> None:
        # B2: a non-str accept-set element → fail loudly, not silent garbage.
        case = self._valid_case()
        case["correct"] = ["abc", 123]
        case["candidates"] = [{"video_id": "abc", "title": "t"}]
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([case]))

    def test_rejects_accept_id_not_in_candidates(self) -> None:
        # S2: otherwise a typo ID in the accept set silently turns a correct pick into wrong.
        case = self._valid_case()
        case["correct"] = ["not_in_pool"]
        case["candidates"] = [{"video_id": "abc", "title": "t"}]
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([case]))


# ── #140: golden loader carries FilmProfile metadata ──────────────────────────


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
        # Records without the new fields load (backward compatibility) with empty defaults.
        cases = load_golden_set(GOLDEN_PATH)
        self.assertTrue(cases)
        self.assertEqual(cases[0].film.cast, [])
        self.assertEqual(cases[0].film.director, "")


# ── --record: fail fast without API_KEY ───────────────────────────────────────


class TestRecordMode(unittest.TestCase):
    def test_missing_api_key_fails_fast(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("API_KEY", None)
            with self.assertRaises(SystemExit):
                main(["--record", "--golden", str(GOLDEN_PATH)])

    def test_record_fails_before_write_when_pinned_id_gone(self) -> None:
        """A fresh pool without a pinned ID fails recording, not the fixture (#380).

        Pools drift: recording “Extreme Measures” again an hour later no longer returned
        `qpMTP6obUeo`. Recording such a pool saves the file, but the next LOAD fails—for everyone
        who merely runs `pytest`, with no hint of the cause. The check before `write_text` names
        the drifted IDs at the source (§IV/§V).
        """
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        before = json.dumps(
            [
                {
                    "film": {"ru_title": "Леший", "original_title": "", "year": 2026},
                    "correct": ["keep"],
                    "candidates": [{"video_id": "keep", "title": "Леший 2026 трейлер"}],
                    "note": "",
                }
            ],
            ensure_ascii=False,
        )
        Path(path).write_text(before, encoding="utf-8")
        drifted = [Candidate(video_id="fresh", title="Леший 2026 трейлер")]
        with (
            mock.patch.dict(os.environ, {"API_KEY": "test-key"}),  # pragma: allowlist secret
            mock.patch("googleapiclient.discovery.build", return_value=object()),
            mock.patch("kinozal_scraper.youtube.search_candidates", return_value=drifted),
            self.assertRaises(GoldenSetError) as ctx,
        ):
            main(["--record", "--golden", path])
        self.assertIn("keep", str(ctx.exception))
        self.assertEqual(Path(path).read_text(encoding="utf-8"), before)


# ── #329: TMDB video snapshot + evaluate_tmdb ─────────────────────────────────


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
        # The tmdb_videos snapshot parses to list[TmdbVideo] (RED before field/parser).
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
        # Records without tmdb_videos load (backward compatibility) with an empty list.
        cases = load_golden_set(self._write([self._valid_case()]))
        self.assertEqual(cases[0].tmdb_videos, [])

    def test_rejects_malformed_tmdb_video(self) -> None:
        # Fail loudly (§IV): malformed video (missing key/iso_639_1/type) → GoldenSetError,
        # not a silent drop (otherwise a biased pool is baked into the snapshot).
        case = self._valid_case()
        case["tmdb_videos"] = [{"iso_639_1": "ru", "type": "Trailer", "site": "YouTube"}]
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([case]))

    def test_accept_set_may_reference_tmdb_key_not_in_candidates(self) -> None:
        # Dual-source ground truth: a valid TMDB key absent from the YouTube pool is valid in
        # the accept set—cross-check against (candidates ∪ tmdb keys), not candidates alone
        # (RED before generalizing _parse_correct).
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
        # A typo ID in neither candidates nor tmdb_videos → fail loudly.
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
        # evaluate_tmdb runs pick_trailer on frozen tmdb_videos and classifies against the
        # accept set: Hit / Wrong / Miss (RED before evaluate_tmdb).
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
        # A real Miss: snapshot is NOT empty but has no eligible video (only Clip) →
        # pick_trailer→None. Distinct from out-of-scope (empty snapshot, below).
        miss_case = {
            "film": {"ru_title": "C", "original_title": "C", "year": 2026},
            "correct": ["ruC"],
            "candidates": [{"video_id": "ruC", "title": "C 2026 трейлер"}],
            "tmdb_videos": [
                {
                    "key": "clipC",
                    "iso_639_1": "ru",
                    "type": "Clip",
                    "official": True,
                    "site": "YouTube",
                    "name": "",
                }
            ],
            "note": "",
        }
        cases = load_golden_set(self._write([hit_case, wrong_case, miss_case]))
        rows, total = evaluate_tmdb(cases)
        outcomes = [o for _, _, o in rows]
        self.assertEqual(outcomes, ["hit", "wrong", "miss"])
        self.assertEqual(total, 1 - 2 + 0)

    def test_evaluate_tmdb_skips_cases_without_snapshot(self) -> None:
        # A case without a tmdb_videos snapshot is outside TMDB scope (synthetic logic fixtures)
        # and does NOT enter rows—or a placeholder ID would inflate TMDB Wrong outcomes.
        with_snapshot = {
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
        no_snapshot = {
            "film": {"ru_title": "B", "original_title": "B", "year": 2026},
            "correct": "placeholder_id",
            "candidates": [{"video_id": "placeholder_id", "title": "B 2026 трейлер"}],
            "note": "",
        }
        cases = load_golden_set(self._write([with_snapshot, no_snapshot]))
        rows, _ = evaluate_tmdb(cases)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0].film.ru_title, "A")


# ── #379: delivery run (production contract, not only pick) ───────────────────


class TestDeliveryEval(unittest.TestCase):
    """#379: the harness measured `pick`, while #359 broke `enrich_with_trailer`—the layer
    between the pick and the user. `evaluate_delivery` runs the golden set through production
    `select_trailer` and parses the RESPONSE (URL / §IV marker) back to `video_id`."""

    def _write(self, data: Any) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.addCleanup(os.unlink, path)
        return path

    def _case(self, correct: Any, candidates: list[dict[str, str]]) -> Any:
        return {
            "film": {"ru_title": "Гнев", "original_title": "Man on Fire", "year": 2026},
            "correct": correct,
            "candidates": candidates,
            "note": "",
        }

    def test_url_reply_classified_by_video_id(self) -> None:
        cases = load_golden_set(
            self._write([self._case("ru01", [{"video_id": "ru01", "title": "Гнев 2026 трейлер"}])])
        )
        rows, total = evaluate_delivery(cases)
        self.assertEqual([(pick, out) for _, pick, out in rows], [("ru01", "hit")])
        self.assertEqual(total, 1)

    def test_miss_marker_maps_to_no_pick(self) -> None:
        # Empty pool → production returns _TRAILER_MISS_MARKER; the measurement must read it
        # as “nothing selected,” not as an unparseable response.
        cases = load_golden_set(self._write([self._case("ru01", [])]))
        rows, total = evaluate_delivery(cases)
        self.assertEqual([(pick, out) for _, pick, out in rows], [(None, "miss")])
        self.assertEqual(total, 0)

    def test_null_correct_with_miss_marker_is_hit(self) -> None:
        # The same null branch as classify: no trailer exists → correctly return nothing.
        # Verifies that the marker reaches classify as None.
        cases = load_golden_set(self._write([self._case(None, [])]))
        rows, total = evaluate_delivery(cases)
        self.assertEqual([out for _, _, out in rows], ["hit"])
        self.assertEqual(total, 1)

    def test_error_marker_raises_not_scored(self) -> None:
        # §IV: broken retrieval (production → _TRAILER_ERROR_MARKER) must not look like
        # “strategy found nothing”—otherwise the metric silently drops and selection is blamed.
        # `test_kinozal_pipeline::test_failure_returns_error_marker` already pins that this
        # marker represents retrieval failure.
        cases = load_golden_set(self._write([self._case("ru01", [])]))
        with self.assertRaises(GoldenSetError):
            evaluate_delivery(cases, select=lambda profile, youtube: _TRAILER_ERROR_MARKER)

    def test_delivery_matches_pick_on_committed_golden_set(self) -> None:
        # Today on main both scorecards must agree: there is no policy between pick and delivery.
        # A difference NOW means the harness lies; its value is that after the next #359 they can diverge.
        cases = load_golden_set(GOLDEN_PATH)
        _, pick_total = evaluate(default_strategy(), cases)
        _, delivery_total = evaluate_delivery(cases)
        self.assertEqual(delivery_total, pick_total)


# ── #380: `trap` annotation—verified FOREIGN candidate in the pool ────────────


class TestTrapField(unittest.TestCase):
    """`trap` answers a question an accept set cannot: “this candidate is a different work,”
    not “an incompletely recorded dub of the same work.” It is therefore ground truth about the
    POOL rather than the outcome, and survives strategy improvements."""

    def _write(self, data: Any) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.addCleanup(os.unlink, path)
        return path

    def _case(self, **over: Any) -> dict[str, Any]:
        case: dict[str, Any] = {
            "film": {"ru_title": "Леший", "original_title": "", "year": 2026},
            "correct": ["ours"],
            "candidates": [
                {"video_id": "ours", "title": "Леший 2026 трейлер сериала"},
                {"video_id": "alien", "title": "Леший трейлер 2026"},
            ],
            "trap": ["alien"],
            "note": "",
        }
        case.update(over)
        return case

    def test_trap_loads_and_marks_pool_members(self) -> None:
        cases = load_golden_set(self._write([self._case()]))
        self.assertEqual(cases[0].trap, ["alien"])

    def test_absent_trap_defaults_to_empty(self) -> None:
        # Backward compatibility modeled on `tmdb_videos` (#329): 28 pre-#380 records load unchanged.
        case = self._case()
        del case["trap"]
        cases = load_golden_set(self._write([case]))
        self.assertEqual(cases[0].trap, [])

    def test_trap_id_outside_candidate_pool_errors(self) -> None:
        # Check the CANDIDATE pool, not candidates|tmdb union (unlike `correct`): a trap is
        # meaningful only among items the strategy actually ranks. Otherwise an ID typo silently
        # disarms the annotation—a case looks annotated without being so (§IV).
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([self._case(trap=["typo"])]))

    def test_trap_intersecting_accept_set_errors(self) -> None:
        # A candidate cannot be both a reference and a foreign work—this is a ground-truth
        # contradiction, not a “refinement.”
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([self._case(trap=["ours"])]))

    def test_trap_must_be_a_list(self) -> None:
        # A string is not a “list with one ID”: `"alien"` iterates by character and the pool
        # membership check would converge on an empty set.
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([self._case(trap="alien")]))

    def test_trap_elements_must_be_str(self) -> None:
        with self.assertRaises(GoldenSetError):
            load_golden_set(self._write([self._case(trap=[1])]))


# ── #138 red baseline: known-gap guard ────────────────────────────────────────


def test_138_seed_cases_present() -> None:
    cases = load_golden_set(GOLDEN_PATH)
    assert any(c.note.startswith("seed: #138") for c in cases), (
        "golden-set обязан засеять #138 RU-промахи"
    )


def test_138_ru_selection_is_fixed() -> None:
    # Inversion of the former xfail guard (§I): #141 changed default_strategy() to a
    # language-aware HeuristicStrategy, while #140 union retrieval put RU into every
    # #138-seed case pool → selection takes the RU reference → all hit. The guard is green now
    # and pins the fix: a RU-selection regression turns red again.
    cases = load_golden_set(GOLDEN_PATH)
    seed = [c for c in cases if c.note.startswith("seed: #138")]
    strat = default_strategy()
    for c in seed:
        pick = strat.pick(c.film, c.candidates)
        assert classify(c.correct, pick.video_id) == "hit"
