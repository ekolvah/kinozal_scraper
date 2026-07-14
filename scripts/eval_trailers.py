#!/usr/bin/env python3
"""Eval-harness подбора трейлера (#139, эпик трейлеров).

Читает golden-set (`tests/fixtures/trailer_golden.json`), прогоняет
`TrailerStrategy` по ЗАМОРОЖЕННЫМ кандидатам офлайн (без сети/квоты),
классифицирует каждый фильм Hit/Wrong/Miss ОТНОСИТЕЛЬНО эталона `correct` и
печатает взвешенную скоркарту. Метрика — раньше оптимизации: baseline красный,
порог затягивается по мере #141/#144.

Fail-loud (§IV/§VI): битая запись golden-set (пустой набор, отсутствующее поле,
дубль `video_id`, `correct` не str|null) → GoldenSetError + exit≠0, НИКОГДА не
деградирует в тихий Miss.

`--record` (dev-only, live) разово пересобирает снимок `candidates` из YouTube;
без `API_KEY` — явный fail-fast, не тихий no-op. Фикстуры frozen: `--record` —
для первичного посева / сознательного рефреша, не рутинный прогон.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# Standalone-run bootstrap: mirror pytest's pythonpath=["src"] так, чтобы
# `import kinozal_scraper` резолвился без editable install (как в ci_check.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kinozal_scraper.trailer_strategy import (  # noqa: E402
    Candidate,
    FilmProfile,
    FirstResultStrategy,
    TrailerStrategy,
)

Outcome = Literal["hit", "wrong", "miss"]

_SCORE: dict[Outcome, int] = {"hit": 1, "miss": 0, "wrong": -2}

_DEFAULT_GOLDEN = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "trailer_golden.json"
)


class GoldenSetError(ValueError):
    """Golden-set повреждён/невалиден — измерять по нему нельзя (fail-loud)."""


@dataclass
class GoldenCase:
    film: FilmProfile
    correct: str | None
    candidates: list[Candidate]
    note: str


def default_strategy() -> TrailerStrategy:
    """Стратегия «под оценкой». #139 — baseline; #141 сменит на язык-aware, и
    привязанный к ней known-gap guard (#138-кейсы) станет audible."""
    return FirstResultStrategy()


def classify(correct: str | None, pick_id: str | None) -> Outcome:
    """Исход относительно эталона. Null-ветка явная: при correct=NONE пустой
    pick — Hit (правильно ничего не выбрать), непустой — Wrong."""
    if correct is None:
        return "hit" if pick_id is None else "wrong"
    if pick_id is None:
        return "miss"
    return "hit" if pick_id == correct else "wrong"


def score(outcomes: list[Outcome]) -> int:
    """Hit +1 / Miss 0 / Wrong −2 — чужой трейлер хуже честного §IV-маркера."""
    return sum(_SCORE[o] for o in outcomes)


# ── golden-set loading (fail-loud) ────────────────────────────────────────────


def _require(entry: dict[str, Any], keys: tuple[str, ...], where: str) -> None:
    for key in keys:
        if key not in entry:
            raise GoldenSetError(f"{where}: missing required field {key!r}")


def _parse_film(raw: Any, where: str) -> FilmProfile:
    if not isinstance(raw, dict):
        raise GoldenSetError(f"{where}: 'film' must be an object, got {type(raw).__name__}")
    _require(raw, ("ru_title", "original_title", "year"), f"{where}.film")
    year = raw["year"]
    if year is not None and not isinstance(year, int):
        raise GoldenSetError(f"{where}.film: 'year' must be int or null, got {type(year).__name__}")
    return FilmProfile(ru_title=raw["ru_title"], original_title=raw["original_title"], year=year)


def _parse_candidates(raw: Any, where: str) -> list[Candidate]:
    if not isinstance(raw, list):
        raise GoldenSetError(f"{where}: 'candidates' must be a list, got {type(raw).__name__}")
    seen: set[str] = set()
    out: list[Candidate] = []
    for j, cand in enumerate(raw):
        spot = f"{where}.candidates[{j}]"
        if not isinstance(cand, dict):
            raise GoldenSetError(f"{spot}: candidate must be an object, got {type(cand).__name__}")
        _require(cand, ("video_id", "title"), spot)
        vid = cand["video_id"]
        if vid in seen:
            raise GoldenSetError(f"{spot}: duplicate candidate video_id {vid!r}")
        seen.add(vid)
        out.append(
            Candidate(
                video_id=vid,
                title=cand["title"],
                channel=cand.get("channel", ""),
                description=cand.get("description", ""),
                published_at=cand.get("published_at", ""),
            )
        )
    return out


def _parse_case(raw: Any, where: str) -> GoldenCase:
    if not isinstance(raw, dict):
        raise GoldenSetError(f"{where}: case must be an object, got {type(raw).__name__}")
    _require(raw, ("film", "correct", "candidates"), where)
    correct = raw["correct"]
    if correct is not None and not isinstance(correct, str):
        raise GoldenSetError(
            f"{where}: 'correct' must be str or null, got {type(correct).__name__}"
        )
    return GoldenCase(
        film=_parse_film(raw["film"], where),
        correct=correct,
        candidates=_parse_candidates(raw["candidates"], where),
        note=raw.get("note", ""),
    )


def load_golden_set(path: str | Path) -> list[GoldenCase]:
    text = Path(path).read_text(encoding="utf-8")
    raw = json.loads(text)
    if not isinstance(raw, list) or not raw:
        kind = type(raw).__name__ if not isinstance(raw, list) else "empty list"
        raise GoldenSetError(f"{path}: golden set must be a non-empty list (got {kind})")
    return [_parse_case(entry, f"{path}[{i}]") for i, entry in enumerate(raw)]


# ── evaluation + scorecard ────────────────────────────────────────────────────


def evaluate(
    strategy: TrailerStrategy, cases: list[GoldenCase]
) -> tuple[list[tuple[GoldenCase, str | None, Outcome]], int]:
    rows: list[tuple[GoldenCase, str | None, Outcome]] = []
    for case in cases:
        pick = strategy.pick(case.film, case.candidates)
        rows.append((case, pick.video_id, classify(case.correct, pick.video_id)))
    return rows, score([o for _, _, o in rows])


def _print_scorecard(rows: list[tuple[GoldenCase, str | None, Outcome]], total: int) -> None:
    tally: dict[Outcome, int] = {"hit": 0, "wrong": 0, "miss": 0}
    for case, pick_id, outcome in rows:
        tally[outcome] += 1
        print(
            f"  {outcome.upper():5} {case.film.ru_title!r} → pick={pick_id!r} correct={case.correct!r}"
        )
    print(
        f"score={total}  hit={tally['hit']} miss={tally['miss']} wrong={tally['wrong']} "
        f"(n={len(rows)})"
    )


# ── --record (dev-only, live) ─────────────────────────────────────────────────


def _require_api_key() -> str:
    key = os.environ.get("API_KEY")
    if not key:
        raise SystemExit("--record requires API_KEY (dev-only live mode); refusing to run")
    return key


def _search_candidates(youtube: Any, film: FilmProfile) -> list[dict[str, str]]:
    title = film.original_title or film.ru_title
    query = f"{title} {film.year} trailer" if film.year else f"{title} trailer"
    response = (
        youtube.search()
        .list(q=query, part="id,snippet", maxResults=5, type="video", videoDuration="short")
        .execute()
    )
    out: list[dict[str, str]] = []
    for item in response.get("items", []):
        if item.get("id", {}).get("kind") != "youtube#video":
            continue
        snippet = item.get("snippet", {})
        out.append(
            {
                "video_id": item["id"]["videoId"],
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "description": snippet.get("description", ""),
                "published_at": snippet.get("publishedAt", ""),
            }
        )
    return out


def _record(golden_path: str | Path) -> int:
    key = _require_api_key()
    cases = load_golden_set(golden_path)  # валидируем перед перезаписью
    from googleapiclient.discovery import build

    youtube = build("youtube", "v3", developerKey=key)
    raw = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    for entry, case in zip(raw, cases, strict=True):
        entry["candidates"] = _search_candidates(youtube, case.film)
    Path(golden_path).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"recorded candidates for {len(cases)} films → {golden_path}")
    return 0


def _ensure_utf8_stdout() -> None:
    """Скоркарта печатает кириллические названия; дефолтная Windows-консоль
    (cp1252) иначе роняет harness UnicodeEncodeError'ом. Root cause — кодировка
    консоли, не логика."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="Eval-harness подбора трейлера (#139).")
    parser.add_argument("--golden", default=str(_DEFAULT_GOLDEN), help="путь к golden-set JSON")
    parser.add_argument(
        "--record", action="store_true", help="dev-only: пересобрать снимок candidates из YouTube"
    )
    parser.add_argument(
        "--threshold", type=int, default=None, help="exit≠0 если итоговый score ниже порога"
    )
    args = parser.parse_args(argv)

    if args.record:
        return _record(args.golden)

    cases = load_golden_set(args.golden)
    rows, total = evaluate(default_strategy(), cases)
    _print_scorecard(rows, total)
    if args.threshold is not None and total < args.threshold:
        print(f"below threshold {args.threshold}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
