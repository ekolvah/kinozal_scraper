#!/usr/bin/env python3
"""RAGAS-eval суммаризатора `summary_ru` (#347).

Суммаризатор (`gemini_enricher.py`, конфиг `sources.json[*].enrich`) выдаёт двухстрочник
«Для кого / Зачем» по GitHub-проекту. Единственный текущий страж — regex
`response_pattern` — проверяет ФОРМАТ, а не СМЫСЛ: галлюцинация в правильном формате
проходит насквозь. Этот харнесс мерит смысл через RAGAS:
  • faithfulness      — не выдумал ли summary фактов сверх описания проекта;
  • answer_relevancy  — отвечает ли summary на «для кого/зачем».

Читает замороженный golden-set (`tests/fixtures/summary_golden.json`: вход + записанный
под-оценкой summary), собирает RAGAS-инпуты, прогоняет метрики и печатает скоркард.
Порог (`--threshold`) — по faithfulness; baseline снимается разово live-прогоном (metric
before optimization, как `eval_trailers.py`).

**Граница offline (в отличие от трейлеров):** RAGAS сам является LLM-судьёй, поэтому
рутинный прогон метрики live/API-gated — как `--record` у трейлеров, а не их офлайн-
скоркард. Живой судья изолирован в `_evaluate_dataset` (единственная внешняя граница,
мокается в юнит-тестах); CI гоняет только чистые швы. Baseline производится dev-прогоном с
настроенным судьёй, НЕ в CI.

Fail-loud (§IV/§VI): битая запись golden-set → GoldenSetError + exit≠0, НИКОГДА тихий скип.
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

# Фиксированный «вопрос» для answer_relevancy: интент суммаризации (совпадает с prod-
# промптом «Для кого / Зачем»). Один на все кейсы — задача суммаризатора неизменна.
_SUMMARY_QUESTION = "Для кого этот проект и зачем он нужен?"

# Ключи per-row метрик в выводе ragas. answer_relevancy в разных версиях ragas зовётся
# по-разному — держим alias-set, чтобы апгрейд ragas не ломал нормализацию тихо (drift-
# толерантность живёт в ЧИСТОЙ normalize_ragas_output, под прямым тестом, не за моком).
_FAITHFULNESS_KEYS = ("faithfulness",)
_RELEVANCY_KEYS = ("answer_relevancy", "response_relevancy")


class GoldenSetError(ValueError):
    """Golden-set повреждён/невалиден — измерять по нему нельзя (fail-loud)."""


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
    """Кейс → строка RAGAS-датасета (внутренний контракт, независимый от версии ragas).

    `contexts` — источник, который модель РЕАЛЬНО видела (title + description + язык, как
    в prod-промпте); `answer` — оцениваемый summary; `question` — фикс. интент.
    Перевод этих нейтральных ключей в схему ragas — в `_evaluate_dataset` (граница §II).
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


# ── RAGAS output normalization (pure, tested directly — SHOULD-FIX-3) ──────────


def _pick_metric(row: dict[str, Any], keys: tuple[str, ...], where: str) -> float:
    for key in keys:
        if key in row:
            return float(row[key])
    raise GoldenSetError(
        f"{where}: RAGAS output missing metric {keys!r} — judge did not compute it "
        f"(visible anomaly, not a silent 0)"
    )


def normalize_ragas_output(raw: list[dict[str, Any]]) -> list[RowScore]:
    """Сырой per-row вывод ragas → list[RowScore]. Fail-loud (§IV): отсутствующая метрика
    — видимая аномалия, не тихий 0. Толерантна к alias'ам ключа relevancy между версиями
    ragas (эта хрупкая часть — под прямым тестом, не за мок-границей)."""
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
    """Единственная внешняя граница: собрать RAGAS-датасет, вызвать `ragas.evaluate` с
    faithfulness+answer_relevancy, вернуть per-row score-словари. Импорт ragas ЛЕНИВЫЙ
    (dev-only; остальной репо и юнит-тесты import-цену не платят). Юнит-тесты мокают
    ИМЕННО эту функцию — живой судья в CI не зовётся.

    Точная схема датасета (`user_input`/`response`/`retrieved_contexts`), символы метрик и
    проводка LLM-судьи/эмбеддингов версионно-зависимы: свериться со схемой ЗАПИНЕННОЙ
    версии ragas при live-прогоне (docs-over-guessing), не угадывать вслепую. Возвращаемые
    ключи метрик нормализует `normalize_ragas_output` (толерантна к alias'ам).
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
    """Скоркард печатает кириллические тайтлы; дефолтная Windows-консоль (cp1252) иначе
    роняет harness UnicodeEncodeError'ом. Root cause — кодировка консоли, не логика."""
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
