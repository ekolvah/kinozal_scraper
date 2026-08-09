#!/usr/bin/env python3
"""RAGAS evaluation of `summary_ru` summarization (#347).

The summarizer (`gemini_enricher.py`, `sources.json[*].enrich` configuration) emits two
lines about audience and purpose for a GitHub project. The only current guard, regex
`response_pattern`, checks FORMAT rather than MEANING, so a well-formatted hallucination
passes. This harness measures meaning through RAGAS:
  • faithfulness      — whether summary invents facts beyond the project description;
  • answer_relevancy  — whether summary answers audience/purpose.

Reads the frozen golden set (`tests/fixtures/summary_golden.json`: input plus summary
recorded for evaluation), assembles RAGAS inputs, runs metrics, and prints a scorecard.
`--threshold` applies to faithfulness; baseline is captured once by a live run (metric
before optimization, like `eval_trailers.py`).

**Offline boundary (unlike trailers):** RAGAS is itself an LLM judge, so routine metric
runs are live/API-gated, like trailer `--record`, rather than its offline scorecard. The
live judge is isolated in `_evaluate_dataset` (the only external boundary, mocked in unit
tests); CI runs only pure seams. Baseline comes from a dev run with configured judge, NOT CI.

Fail loud (§IV/§VI): corrupt golden-set entry → GoldenSetError + exit≠0, NEVER silent skip.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_GOLDEN = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "summary_golden.json"
)

# Fixed answer_relevancy question: the summarization intent (same as the production
# audience/purpose prompt). It is shared by all cases because the task is invariant.
_SUMMARY_QUESTION = "Для кого этот проект и зачем он нужен?"

# Per-row metric keys in RAGAS output. Different RAGAS versions name answer_relevancy
# differently; keep aliases so upgrades cannot silently break normalization. Drift
# tolerance lives in pure `normalize_ragas_output`, directly tested rather than mocked.
_FAITHFULNESS_KEYS = ("faithfulness",)
_RELEVANCY_KEYS = ("answer_relevancy", "response_relevancy")


class GoldenSetError(ValueError):
    """Golden set is corrupt/invalid and cannot be measured (fail loud)."""


@dataclass
class SummaryCase:
    title: str
    description: str
    language: str
    summary: str
    note: str


@dataclass(frozen=True)
class RowScore:
    faithfulness: float
    answer_relevancy: float


@dataclass(frozen=True)
class Scorecard:
    mean_faithfulness: float
    mean_answer_relevancy: float
    n: int


# ── golden-set loading (fail-loud) ────────────────────────────────────────────


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoldenSetError(f"{where}: must be a non-empty string")
    return value


def _parse_case(raw: Any, where: str) -> SummaryCase:
    if not isinstance(raw, dict):
        raise GoldenSetError(f"{where}: case must be an object, got {type(raw).__name__}")
    inp = raw.get("input")
    if not isinstance(inp, dict):
        raise GoldenSetError(f"{where}: 'input' must be an object")
    title = _require_str(inp.get("title"), f"{where}.input.title")
    description = _require_str(inp.get("description"), f"{where}.input.description")
    summary = _require_str(raw.get("summary"), f"{where}.summary")
    language = inp.get("language", "")
    if not isinstance(language, str):
        raise GoldenSetError(f"{where}.input.language: must be a string if present")
    return SummaryCase(
        title=title,
        description=description,
        language=language,
        summary=summary,
        note=raw.get("note", ""),
    )


def load_golden_set(path: str | Path) -> list[SummaryCase]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        kind = type(raw).__name__ if not isinstance(raw, list) else "empty list"
        raise GoldenSetError(f"{path}: golden set must be a non-empty list (got {kind})")
    return [_parse_case(entry, f"{path}[{i}]") for i, entry in enumerate(raw)]


# ── RAGAS input assembly (pure) ───────────────────────────────────────────────


def build_ragas_inputs(cases: list[SummaryCase]) -> list[dict[str, Any]]:
    """Case → RAGAS-dataset row (internal contract, independent of RAGAS version).

    `contexts` is the source the model ACTUALLY saw (title + description + language, as in
    the production prompt); `answer` is evaluated summary; `question` is fixed intent.
    `_evaluate_dataset` translates these neutral keys to RAGAS schema (boundary §II).
    """
    rows: list[dict[str, Any]] = []
    for case in cases:
        parts = [case.title, case.description]
        if case.language:
            parts.append(f"Язык: {case.language}")
        rows.append(
            {
                "question": _SUMMARY_QUESTION,
                "answer": case.summary,
                "contexts": ["\n".join(parts)],
            }
        )
    return rows


# ── RAGAS output normalization (pure, tested directly — SHOULD-FIX-3) ─────────


def _pick_metric(row: dict[str, Any], keys: tuple[str, ...], where: str) -> float:
    for key in keys:
        if key in row:
            return float(row[key])
    raise GoldenSetError(
        f"{where}: RAGAS output missing metric {keys!r} — judge did not compute it "
        f"(visible anomaly, not a silent 0)"
    )


def normalize_ragas_output(raw: list[dict[str, Any]]) -> list[RowScore]:
    """Raw per-row RAGAS output → list[RowScore]. Fail loud (§IV): missing metric is a
    visible anomaly, not silent 0. Tolerates relevancy-key aliases between RAGAS versions;
    this fragile part is directly tested, not behind a mock boundary."""
    scores: list[RowScore] = []
    for i, row in enumerate(raw):
        where = f"ragas_output[{i}]"
        scores.append(
            RowScore(
                faithfulness=_pick_metric(row, _FAITHFULNESS_KEYS, where),
                answer_relevancy=_pick_metric(row, _RELEVANCY_KEYS, where),
            )
        )
    return scores


def scorecard(rows: list[RowScore]) -> Scorecard:
    n = len(rows)
    if n == 0:
        return Scorecard(mean_faithfulness=0.0, mean_answer_relevancy=0.0, n=0)
    return Scorecard(
        mean_faithfulness=sum(r.faithfulness for r in rows) / n,
        mean_answer_relevancy=sum(r.answer_relevancy for r in rows) / n,
        n=n,
    )


# ── RAGAS boundary (§II — the ONLY mocked seam) ───────────────────────────────


def _evaluate_dataset(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only external boundary: assemble RAGAS dataset, call `ragas.evaluate` with
    faithfulness+answer_relevancy, and return per-row score dictionaries. RAGAS imports
    are LAZY (dev-only; the rest of the repo and unit tests avoid their import cost).
    Unit tests mock THIS function, so CI never calls the live judge.

    Dataset schema (`user_input`/`response`/`retrieved_contexts`), metric symbols, and
    LLM-judge/embedding wiring vary by version: check the PINNED RAGAS version during live
    runs (docs over guessing). `normalize_ragas_output` normalizes returned metric aliases.
    """
    from ragas import EvaluationDataset, evaluate  # noqa: PLC0415 — lazy dev-only import
    from ragas.dataset_schema import EvaluationResult  # noqa: PLC0415
    from ragas.metrics import answer_relevancy, faithfulness  # noqa: PLC0415

    dataset = EvaluationDataset.from_list(
        [
            {
                "user_input": row["question"],
                "response": row["answer"],
                "retrieved_contexts": row["contexts"],
            }
            for row in rows
        ]
    )
    result = evaluate(dataset, metrics=[faithfulness, answer_relevancy])
    # evaluate() returns EvaluationResult | Executor; the Executor branch only fires with
    # return_executor=True (default False), so this is always EvaluationResult. Assert to
    # narrow for mypy and fail loud if a ragas upgrade ever changes that invariant.
    assert isinstance(result, EvaluationResult)
    scores: list[dict[str, Any]] = list(result.scores)
    return scores


# ── scorecard printing + CLI ──────────────────────────────────────────────────


def _print_scorecard(cases: list[SummaryCase], rows: list[RowScore], card: Scorecard) -> None:
    for case, row in zip(cases, rows, strict=True):
        print(
            f"  faith={row.faithfulness:.2f} relev={row.answer_relevancy:.2f}  "
            f"{case.title!r}" + (f"  # {case.note}" if case.note else "")
        )
    print(
        f"mean_faithfulness={card.mean_faithfulness:.3f} "
        f"mean_answer_relevancy={card.mean_answer_relevancy:.3f} (n={card.n})"
    )


def _ensure_utf8_stdout() -> None:
    """Scorecard prints Cyrillic titles; default Windows console (cp1252) otherwise
    crashes the harness with UnicodeEncodeError. The root cause is console encoding, not logic."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="RAGAS-eval суммаризатора (#347).")
    parser.add_argument("--golden", default=str(_DEFAULT_GOLDEN), help="путь к golden-set JSON")
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="exit≠0 если средний faithfulness ниже порога",
    )
    args = parser.parse_args(argv)

    cases = load_golden_set(args.golden)
    rows = build_ragas_inputs(cases)
    scores = normalize_ragas_output(_evaluate_dataset(rows))
    card = scorecard(scores)
    print("── RAGAS eval (summary_ru: faithfulness / answer_relevancy) ──")
    _print_scorecard(cases, scores, card)
    if args.threshold is not None and card.mean_faithfulness < args.threshold:
        print(f"below faithfulness threshold {args.threshold}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
