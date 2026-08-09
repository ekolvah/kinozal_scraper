"""RED tests for #347: RAGAS evaluation of the summarizer (faithfulness / answer_relevancy).

Replaces regex `response_pattern` (a FORMAT vibe check) with measurable assessment of MEANING:
whether `summary_ru` invented facts beyond the project description (faithfulness) and whether it
answers “for whom/why” (answer_relevancy).

`ragas.evaluate` is an external library boundary (§II), isolated behind the thin
`_evaluate_dataset` seam (the only mocked item). Input construction (`build_ragas_inputs`)
and RAGAS-output normalization (`normalize_ragas_output`) are PURE and tested directly, not
under a mock (fragile version-drift mapping must not hide behind the boundary). These tests do NOT
invoke the live RAGAS judge (no key/tokens)—the baseline comes from a separate live run, not CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.eval_summarizer import (
    GoldenSetError,
    RowScore,
    SummaryCase,
    build_ragas_inputs,
    load_golden_set,
    main,
    normalize_ragas_output,
    scorecard,
)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "summary_golden.json"

_VALID_CASE: dict[str, Any] = {
    "input": {"title": "foo-lib", "description": "bar baz qux", "language": "Python"},
    "summary": "Для кого: бэкенд-разработчики\nЗачем: кэширование запросов",
    "note": "ok",
}


def _write(tmp_path: Path, obj: Any) -> Path:
    p = tmp_path / "g.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


class TestLoadGoldenSet:
    def test_loads_valid_cases(self, tmp_path: Path) -> None:
        cases = load_golden_set(_write(tmp_path, [_VALID_CASE]))
        assert len(cases) == 1
        assert cases[0].title == "foo-lib"
        assert cases[0].description == "bar baz qux"
        assert cases[0].language == "Python"
        assert cases[0].summary.startswith("Для кого:")

    def test_rejects_non_list(self, tmp_path: Path) -> None:
        with pytest.raises(GoldenSetError):
            load_golden_set(_write(tmp_path, {"not": "a list"}))

    def test_rejects_empty_summary(self, tmp_path: Path) -> None:
        # An empty summary must not be silently measured for faithfulness—fail loudly (§IV).
        bad = {**_VALID_CASE, "summary": "   "}
        with pytest.raises(GoldenSetError):
            load_golden_set(_write(tmp_path, [bad]))

    def test_rejects_missing_input_field(self, tmp_path: Path) -> None:
        # A case without input.description → GoldenSetError, not a silent skip.
        bad = {"input": {"title": "foo"}, "summary": "s", "note": ""}
        with pytest.raises(GoldenSetError):
            load_golden_set(_write(tmp_path, [bad]))

    def test_shipped_fixture_is_valid(self) -> None:
        # The repository's frozen golden set loads without errors (structure-drift guard).
        cases = load_golden_set(GOLDEN_PATH)
        assert cases, "shipped golden-set must be non-empty"


class TestBuildRagasInputs:
    def test_maps_case_to_row(self) -> None:
        case = SummaryCase(
            title="foo-lib",
            description="bar baz",
            language="Python",
            summary="Для кого: X\nЗачем: Y",
            note="",
        )
        rows = build_ragas_inputs([case])
        assert len(rows) == 1
        row = rows[0]
        # answer = the summary under evaluation.
        assert row["answer"] == "Для кого: X\nЗачем: Y"
        # contexts = source the model actually saw (title+description+language).
        assert isinstance(row["contexts"], list)
        ctx = row["contexts"][0]
        assert "foo-lib" in ctx and "bar baz" in ctx and "Python" in ctx
        # question = fixed, non-empty summarization intent.
        assert row["question"]

    def test_language_optional_absent(self) -> None:
        case = SummaryCase(title="t", description="d", language="", summary="s", note="")
        ctx = build_ragas_inputs([case])[0]["contexts"][0]
        assert "t" in ctx and "d" in ctx


class TestNormalizeRagasOutput:
    def test_maps_ragas_result_to_rowscores(self) -> None:
        # Raw per-row RAGAS output (list[dict]) → list[RowScore] (pure mapping).
        raw = [
            {"faithfulness": 0.9, "answer_relevancy": 0.8},
            {"faithfulness": 0.5, "answer_relevancy": 1.0},
        ]
        assert normalize_ragas_output(raw) == [
            RowScore(faithfulness=0.9, answer_relevancy=0.8),
            RowScore(faithfulness=0.5, answer_relevancy=1.0),
        ]

    def test_missing_metric_is_visible_error(self) -> None:
        # RAGAS did not calculate a metric → visible anomaly (§IV), not a silent 0.
        with pytest.raises(GoldenSetError):
            normalize_ragas_output([{"faithfulness": 0.9}])


class TestScorecard:
    def test_aggregates_mean_metrics(self) -> None:
        card = scorecard([RowScore(1.0, 0.6), RowScore(0.0, 0.8)])
        assert card.mean_faithfulness == pytest.approx(0.5)
        assert card.mean_answer_relevancy == pytest.approx(0.7)
        assert card.n == 2


class TestMain:
    def test_threshold_below_exits_1(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        golden = _write(tmp_path, [_VALID_CASE, _VALID_CASE])
        # §II boundary: the live judge is mocked with low faithfulness—RAGAS is not imported.
        monkeypatch.setattr(
            "scripts.eval_summarizer._evaluate_dataset",
            lambda rows: [{"faithfulness": 0.2, "answer_relevancy": 0.9} for _ in rows],
        )
        assert main(["--golden", str(golden), "--threshold", "0.7"]) == 1

    def test_threshold_met_exits_0(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        golden = _write(tmp_path, [_VALID_CASE])
        monkeypatch.setattr(
            "scripts.eval_summarizer._evaluate_dataset",
            lambda rows: [{"faithfulness": 0.9, "answer_relevancy": 0.9} for _ in rows],
        )
        assert main(["--golden", str(golden)]) == 0
