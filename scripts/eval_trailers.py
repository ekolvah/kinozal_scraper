#!/usr/bin/env python3
"""Trailer-selection evaluation harness (#139, trailer epic).

Reads golden set (`tests/fixtures/trailer_golden.json`), runs `TrailerStrategy` over
FROZEN offline candidates (no network/quota), classifies each film Hit/Wrong/Miss against
`correct`, and prints a weighted scorecard. Metric comes before optimization: baseline is
red, with threshold tightening through #141/#144.

`correct` reference is one id, accept set (`list[str]` of equivalent RU dubs), or null.
Fail loud (§IV/§VI): corrupt golden record (empty set/accept set, missing field, duplicate
`video_id`, invalid `correct`, accept id outside pool) → GoldenSetError + exit≠0, NEVER
silently degrades to Miss.

`--record` (dev-only, live) rebuilds `candidates` snapshot from YouTube once; missing
`API_KEY` fails fast, not silently. Fixtures are frozen: use it for initial seeding or
intentional refresh, not routine runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

# Standalone-run bootstrap: mirror pytest's pythonpath=["src"] so
# `import kinozal_scraper` resolves without editable install (as in ci_check.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kinozal_scraper.kinozal_pipeline import (  # noqa: E402
    _TRAILER_ERROR_MARKER,
    _TRAILER_MISS_MARKER,
    select_trailer,
)
from kinozal_scraper.tmdb_trailer import TmdbVideo, pick_trailer  # noqa: E402
from kinozal_scraper.trailer_strategy import (  # noqa: E402
    Candidate,
    FilmProfile,
    HeuristicStrategy,
    TrailerStrategy,
)

Outcome = Literal["hit", "wrong", "miss"]

_SCORE: dict[Outcome, int] = {"hit": 1, "miss": 0, "wrong": -2}
_OUTCOMES: tuple[Outcome, ...] = ("hit", "wrong", "miss")

_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
_DEFAULT_GOLDEN = _FIXTURES / "trailer_golden.json"
# Pinned DELIVERY scorecard outcome (output has three: pick / delivery / TMDB).
# The gate is `tests/test_eval_baseline.py`, not ci_check CHECKS: ci_check already runs
# pytest and pre-push runs ci_check, so a second registry is unnecessary (#379).
BASELINE_PATH = _FIXTURES / "trailer_baseline.json"


class GoldenSetError(ValueError):
    """Golden set is corrupt/invalid and cannot be measured (fail loud)."""


@dataclass
class GoldenCase:
    film: FilmProfile
    correct: str | list[str] | None
    candidates: list[Candidate]
    note: str
# #329: frozen TMDB-video snapshot (optional—pre-#329 records load with an empty
# list; evaluate_tmdb returns Miss until a snapshot is recorded).
    tmdb_videos: list[TmdbVideo] = field(default_factory=list)
# #380: pool candidates VERIFIED as another work (evidence in `note`). Answers what an
# accept set cannot: “another film” versus an incompletely recorded dub of the same one.
# Ground truth concerns the POOL, not outcome, so it survives strategy improvement; it
# does not participate in scoring (weights outside #380 scope), while `classify` marks wrong.
    trap: list[str] = field(default_factory=list)


def default_strategy() -> TrailerStrategy:
    """Strategy under evaluation. #141 language-aware `HeuristicStrategy` replaced #139
    baseline `FirstResultStrategy`: language is primary (RU>EN), cast secondary tie-break.
    The pinned known-gap guard (#138 cases) became audible and was inverted."""
    return HeuristicStrategy()


def classify(correct: str | list[str] | None, pick_id: str | None) -> Outcome:
    """Outcome against reference. `correct` is one id, an accept set (equivalent RU
    dubs), or NONE. Null branch is explicit: correct=NONE plus empty pick is Hit,
    nonempty pick is Wrong. Otherwise Hit means pick ∈ accept set."""
    if correct is None:
        return "hit" if pick_id is None else "wrong"
    if pick_id is None:
        return "miss"
    accept = {correct} if isinstance(correct, str) else set(correct)
    return "hit" if pick_id in accept else "wrong"


def score(outcomes: list[Outcome]) -> int:
    """Hit +1 / Miss 0 / Wrong −2: an unrelated trailer is worse than an honest §IV marker."""
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
    cast = raw.get("cast", [])
    if not isinstance(cast, list):
        raise GoldenSetError(f"{where}.film: 'cast' must be a list, got {type(cast).__name__}")
    return FilmProfile(
        ru_title=raw["ru_title"],
        original_title=raw["original_title"],
        year=year,
        cast=cast,
        director=raw.get("director", ""),
        genre=raw.get("genre", ""),
        description=raw.get("description", ""),
    )


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


def _parse_tmdb_videos(raw: Any, where: str) -> list[TmdbVideo]:
    """Optional TMDB-video snapshot. Fail loud (§IV): corrupt video (missing
    `key`/`iso_639_1`/`type`/`site` or non-str fields) → GoldenSetError, never silent
    drop, or a biased pool (lost RU video) becomes frozen in the snapshot."""
    if not isinstance(raw, list):
        raise GoldenSetError(f"{where}: 'tmdb_videos' must be a list, got {type(raw).__name__}")
    out: list[TmdbVideo] = []
    for j, vid in enumerate(raw):
        spot = f"{where}.tmdb_videos[{j}]"
        if not isinstance(vid, dict):
            raise GoldenSetError(f"{spot}: video must be an object, got {type(vid).__name__}")
        _require(vid, ("key", "iso_639_1", "type", "site"), spot)
        for str_field in ("key", "iso_639_1", "type", "site", "name"):
            if str_field in vid and not isinstance(vid[str_field], str):
                raise GoldenSetError(
                    f"{spot}: {str_field!r} must be str, got {type(vid[str_field]).__name__}"
                )
        out.append(
            TmdbVideo(
                key=vid["key"],
                iso_639_1=vid["iso_639_1"],
                type=vid["type"],
                official=bool(vid.get("official", False)),
                site=vid["site"],
                name=vid.get("name", ""),
            )
        )
    return out


def _parse_correct(raw: Any, valid_ids: set[str], where: str) -> str | list[str] | None:
    """`correct` is str | accept set (list[str]) | null. Fail loud (B2/S2): reject an
    empty accept set (silent null-semantics collapse masking Miss/Wrong) and non-str members.
    Every accept id must be in `valid_ids`, the union of YouTube `candidates` and TMDB
    `tmdb_videos`: a valid TMDB key outside YouTube is legitimate, but a nowhere-present typo
    would silently turn a correct pick wrong. Legacy single-str retains out-of-pool → Miss."""
    if raw is None or isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        if not raw:
            raise GoldenSetError(
                f"{where}: 'correct' accept-set must be non-empty (use null for 'no trailer')"
            )
        for k, el in enumerate(raw):
            if not isinstance(el, str):
                raise GoldenSetError(
                    f"{where}: 'correct'[{k}] must be str, got {type(el).__name__}"
                )
            if el not in valid_ids:
                raise GoldenSetError(
                    f"{where}: 'correct' id {el!r} not among candidate/tmdb video_ids"
                )
        return raw
    raise GoldenSetError(
        f"{where}: 'correct' must be str, list[str] or null, got {type(raw).__name__}"
    )


def _parse_trap(
    raw: Any, pool_ids: set[str], correct: str | list[str] | None, where: str
) -> list[str]:
    """Verified unrelated candidates. Fail loud (§IV/§VI) like the rest of the set:
    an id typo would silently disarm labeling, making a case look labeled when it is not.

    Check against `candidates`, not candidates|tmdb union unlike `correct`: a trap only
    matters among what strategy actually ranks. Overlap with accept set contradicts ground
    truth—a candidate cannot be both reference and unrelated work."""
    if not isinstance(raw, list):
        raise GoldenSetError(f"{where}: 'trap' must be a list, got {type(raw).__name__}")
    accept: set[str] = set()
    if isinstance(correct, str):
        accept = {correct}
    elif isinstance(correct, list):
        accept = set(correct)
    out: list[str] = []
    for j, vid in enumerate(raw):
        if not isinstance(vid, str):
            raise GoldenSetError(f"{where}: 'trap'[{j}] must be str, got {type(vid).__name__}")
        if vid not in pool_ids:
            raise GoldenSetError(f"{where}: 'trap' id {vid!r} not among candidate video_ids")
        if vid in accept:
            raise GoldenSetError(f"{where}: 'trap' id {vid!r} is also in the accept-set")
        out.append(vid)
    return out


def _parse_case(raw: Any, where: str) -> GoldenCase:
    if not isinstance(raw, dict):
        raise GoldenSetError(f"{where}: case must be an object, got {type(raw).__name__}")
    _require(raw, ("film", "correct", "candidates"), where)
    candidates = _parse_candidates(raw["candidates"], where)
    tmdb_videos = _parse_tmdb_videos(raw.get("tmdb_videos", []), where)
    pool_ids = {c.video_id for c in candidates}
    valid_ids = pool_ids | {v.key for v in tmdb_videos}
    correct = _parse_correct(raw["correct"], valid_ids, where)
    return GoldenCase(
        film=_parse_film(raw["film"], where),
        correct=correct,
        candidates=candidates,
        note=raw.get("note", ""),
        tmdb_videos=tmdb_videos,
        trap=_parse_trap(raw.get("trap", []), pool_ids, correct, where),
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


def evaluate_tmdb(
    cases: list[GoldenCase],
) -> tuple[list[tuple[GoldenCase, str | None, Outcome]], int]:
    """TMDB-source run: `pick_trailer` over FROZEN `tmdb_videos`, classified against
    the same `correct` accept set. It shares `evaluate`'s contract, so scorecards compare.

    Scope is cases with nonempty `tmdb_videos`. Synthetic HeuristicStrategy logic fixtures
    (#138/#140, placeholder ids such as `dune2_official` that real TMDB YouTube ids cannot
    hit) carry no snapshot (`_record_tmdb` clears them), so are outside cross-source comparison.
    Actual “TMDB found nothing” is nonempty snapshot with no eligible video → None → Miss."""
    rows: list[tuple[GoldenCase, str | None, Outcome]] = []
    for case in cases:
        if not case.tmdb_videos:
            continue
        pick = pick_trailer(case.tmdb_videos)
        pick_id = pick.video_id if pick is not None else None
        rows.append((case, pick_id, classify(case.correct, pick_id)))
    return rows, score([o for _, _, o in rows])


class _FrozenRetrieval:
    """Retrieval stub for delivery run: returns the case's frozen pool.

    The external boundary (§II) is legitimately replaced; everything beyond it
    (`select_trailer`) remains real production code."""

    def __init__(self, pool: list[Candidate]) -> None:
        self._pool = pool

    # `_profile` has an underscore because retrieval dictates the signature while the pool
    # is frozen. The ARG002 ratchet (#236) remains enabled in `scripts/**`, unlike
    # `tests/**`; a per-file ignore for one stub would be wrong.
    def search_candidates(self, _profile: FilmProfile) -> list[Candidate]:
        return list(self._pool)


def _pick_id_from_reply(reply: str, where: str) -> str | None:
    """Production-delivery reply → `video_id` | None. Fail loud for everything else.

    Compare markers with IMPORTED constants, not copied strings: production rewording would
    otherwise silently turn Miss into “unparsable reply.” Extract `video_id` from query rather
    than prefix slicing because URL format belongs to `select_trailer`.

    Error marker is TOOL failure (the retrieval stub cannot throw); treating it as `miss`
    would silently lower the metric and blame selection (§IV)."""
    if reply == _TRAILER_MISS_MARKER:
        return None
    if reply == _TRAILER_ERROR_MARKER:
        raise GoldenSetError(f"{where}: delivery returned the §IV error marker — harness is broken")
    ids = parse_qs(urlsplit(reply).query).get("v", [])
    if not ids:
        raise GoldenSetError(f"{where}: unparsable delivery reply {reply!r}")
    return ids[0]


def evaluate_delivery(
    cases: list[GoldenCase],
    select: Callable[[FilmProfile, Any], str] = select_trailer,
) -> tuple[list[tuple[GoldenCase, str | None, Outcome]], int]:
    """Run through the PRODUCTION delivery contract, not only `pick` (#379).

    `evaluate` measures `TrailerStrategy.pick`; #359 broke the layer ABOVE it
    (`kinozal_pipeline.select_trailer`: post-pick policy, §IV markers, URL format), so a
    pick scorecard would be identical before and after regression. Here golden set travels
    through production function and its reply parses back to `video_id`.

    `select` is parameterized like `evaluate(strategy, cases)`: default is real production
    function; injection proves gate firing on counterfactual policy
    (`tests/test_eval_baseline.py`) without keeping it in `src`.
    """
    rows: list[tuple[GoldenCase, str | None, Outcome]] = []
    for i, case in enumerate(cases):
        reply = select(case.film, _FrozenRetrieval(case.candidates))
        pick_id = _pick_id_from_reply(reply, f"case[{i}] {case.film.ru_title!r}")
        rows.append((case, pick_id, classify(case.correct, pick_id)))
    return rows, score([o for _, _, o in rows])


# ── baseline ratchet over delivery scorecard (#379) ───────────────────────────


@dataclass
class BaselineEntry:
    """Pinned outcome of one case. `i` accompanies `film` because `ru_title` is NOT
    unique in the set, so checking only its name would miss a swap of namesake cases."""

    i: int
    film: str
    outcome: Outcome


@dataclass
class BaselineReport:
    """Comparison verdict. Pure data: no I/O or exit. Gate (pytest) and printing use
    the same `compare_to_baseline`, structurally preventing CLI-green/test-red divergence."""

    moved: list[tuple[str, Outcome, Outcome]]
    baseline_score: int
    current_score: int
    n: int

    @property
    def ok(self) -> bool:
        return not self.moved

    @property
    def text(self) -> str:
        if self.ok:
            return f"baseline: match (score={self.current_score}, n={self.n})"
        delta = self.current_score - self.baseline_score
        lines = [
            f"baseline: MISMATCH (score {self.baseline_score} → {self.current_score}, "
            f"Δ{delta:+d}), переехало кейсов: {len(self.moved)}"
        ]
        lines += [f"  {film!r} {was}→{now}" for film, was, now in self.moved]
        lines.append(
            "если изменение осознанное — обнови фикстуру: "
            "python scripts/eval_trailers.py --update-baseline (дифф пойдёт в PR)"
        )
        return "\n".join(lines)


def build_baseline(rows: list[tuple[GoldenCase, str | None, Outcome]]) -> list[BaselineEntry]:
    return [BaselineEntry(i, case.film.ru_title, o) for i, (case, _, o) in enumerate(rows)]


def load_baseline(path: str | Path) -> list[BaselineEntry]:
    """Fail loud as golden set: corrupt baseline makes the gate unmeasurable, not a
    reason to compare “as best as possible.”"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise GoldenSetError(f"{path}: baseline must be a non-empty list")
    out: list[BaselineEntry] = []
    for j, entry in enumerate(raw):
        where = f"{path}[{j}]"
        if not isinstance(entry, dict):
            raise GoldenSetError(f"{where}: entry must be an object")
        for key in ("i", "film", "outcome"):
            if key not in entry:
                raise GoldenSetError(f"{where}: missing required field {key!r}")
        outcome = next((o for o in _OUTCOMES if o == entry["outcome"]), None)
        if outcome is None:
            raise GoldenSetError(f"{where}: unknown outcome {entry['outcome']!r}")
        out.append(BaselineEntry(i=entry["i"], film=entry["film"], outcome=outcome))
    return out


def save_baseline(path: str | Path, entries: list[BaselineEntry]) -> None:
    payload = [{"i": e.i, "film": e.film, "outcome": e.outcome} for e in entries]
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def compare_to_baseline(
    baseline: list[BaselineEntry], rows: list[tuple[GoldenCase, str | None, Outcome]]
) -> BaselineReport:
    """Pure outcome comparison. Any divergence is red.

    Improvement is intentionally red: “green with warning” recreates #379's defect, a
    signal nobody must read (§IV). With wrong cases (#380), positive aggregate delta can
    hide hit→wrong; per-film comparison cannot. `--update-baseline` makes improvement
    visible and reviewable in the same PR.

    Out-of-sync length/index/name → `GoldenSetError`: positional comparison of diverged
    sets would silently compare different films."""
    if len(baseline) != len(rows):
        raise GoldenSetError(
            f"baseline out of sync: {len(baseline)} entries vs {len(rows)} cases — "
            "regenerate with --update-baseline"
        )
    moved: list[tuple[str, Outcome, Outcome]] = []
    for i, (entry, (case, _, outcome)) in enumerate(zip(baseline, rows, strict=True)):
        if entry.i != i or entry.film != case.film.ru_title:
            raise GoldenSetError(
                f"baseline out of sync at position {i}: expected {case.film.ru_title!r} "
                f"(i={i}), baseline has {entry.film!r} (i={entry.i})"
            )
        if entry.outcome != outcome:
            moved.append((case.film.ru_title, entry.outcome, outcome))
    return BaselineReport(
        moved=moved,
        baseline_score=score([e.outcome for e in baseline]),
        current_score=score([o for _, _, o in rows]),
        n=len(rows),
    )


def _print_scorecard(rows: list[tuple[GoldenCase, str | None, Outcome]], total: int) -> None:
    tally: dict[Outcome, int] = {"hit": 0, "wrong": 0, "miss": 0}
    for case, pick_id, outcome in rows:
        tally[outcome] += 1
        # §IV attribution (#380): “wrong” alone cannot distinguish another work from an
        # incompletely recorded dub of the same work; this marker names the former.
        trap = " TRAP" if pick_id is not None and pick_id in case.trap else ""
        print(
            f"  {outcome.upper():5}{trap} {case.film.ru_title!r} → "
            f"pick={pick_id!r} correct={case.correct!r}"
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


def _record(golden_path: str | Path) -> int:
    """Dev-only live: rebuild candidate pools in the golden snapshot.

    `search_candidates` deliberately has no try/except (#383): universal retrieval
    failure raises `TrailerRetrievalError` and fails the run. Recording quota-caused
    `candidates: []` would poison the baseline used to measure selection. `write_text`
    follows the loop, so no partially overwritten file remains.

    Revalidate fresh payload BEFORE writing (#380): pools drift, while `correct`/`trap`
    reference concrete ids. Silently recording it would break the next pytest load without
    cause; validation names drifted ids at the point of recording (§IV/§V).
    """
    key = _require_api_key()
    cases = load_golden_set(golden_path)  # Validate before overwrite.
    from dataclasses import asdict

    from googleapiclient.discovery import build

    # §II: same union retrieval as production/future composition, not a second copy of
    # query building plus snippet mapping (formerly `_search_candidates`). RU enters pool.
    from kinozal_scraper.youtube import search_candidates

    youtube = build("youtube", "v3", developerKey=key)
    raw = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    for entry, case in zip(raw, cases, strict=True):
        entry["candidates"] = [asdict(c) for c in search_candidates(youtube, case.film)]
    for i, entry in enumerate(raw):
        _parse_case(entry, f"{golden_path}[{i}] (fresh pool)")
    Path(golden_path).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"recorded candidates for {len(cases)} films → {golden_path}")
    return 0


def _record_tmdb(golden_path: str | Path) -> int:
    """Dev-only live: rebuild `tmdb_videos` snapshot, REUSING `TmdbClient.resolve`
    (§II, not a second query builder like `_record` uses `search_candidates`). Missing
    `TMDB_TOKEN` fails fast (constructor KeyError). Resolver drops malformed no-key output;
    everything else fails loud on next snapshot load (`_parse_tmdb_videos`)."""
    cases = load_golden_set(golden_path)  # Validate before overwrite.
    from dataclasses import asdict

    from kinozal_scraper.tmdb_trailer import TmdbClient

    client = TmdbClient()
    raw = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    for entry, case in zip(raw, cases, strict=True):
        # Scope = real cross-source cases (`correct: list` accept-set form). Synthetic
        # logic fixtures (`str`/`null` correct) clear: do not spend TMDB quota on invented
        # titles or freeze their noise into snapshot (§IV).
        if isinstance(case.correct, list):
            entry["tmdb_videos"] = [asdict(v) for v in client.resolve(case.film)]
        else:
            entry["tmdb_videos"] = []
    Path(golden_path).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"recorded tmdb_videos for {len(cases)} films → {golden_path}")
    return 0


def _ensure_utf8_stdout() -> None:
    """Scorecard prints Cyrillic titles; default Windows console (cp1252) otherwise
    crashes harness with UnicodeEncodeError. Root cause is console encoding, not logic."""
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
        "--record-tmdb",
        action="store_true",
        help="dev-only: пересобрать снимок tmdb_videos из TMDB (нужен TMDB_TOKEN)",
    )
    parser.add_argument(
        "--threshold", type=int, default=None, help="exit≠0 если итоговый score ниже порога"
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="перезаписать trailer_baseline.json текущей delivery-скоркартой",
    )
    args = parser.parse_args(argv)

    if args.record:
        return _record(args.golden)
    if args.record_tmdb:
        return _record_tmdb(args.golden)

    cases = load_golden_set(args.golden)
    rows, total = evaluate(default_strategy(), cases)
    print("── HeuristicStrategy (YouTube retrieval) ──")
    _print_scorecard(rows, total)
    delivery_rows, delivery_total = evaluate_delivery(cases)
    print("── delivery (kinozal_pipeline.select_trailer — то, что уходит юзеру) ──")
    _print_scorecard(delivery_rows, delivery_total)
    tmdb_rows, tmdb_total = evaluate_tmdb(cases)
    print("── TMDB videos (metadata source) ──")
    _print_scorecard(tmdb_rows, tmdb_total)

    if args.update_baseline:
        save_baseline(BASELINE_PATH, build_baseline(delivery_rows))
        print(f"baseline updated → {BASELINE_PATH}")
        return 0
    # Printing verdict is informational; `tests/test_eval_baseline.py` makes it red (the
    # sole gate carrier, #379). Both use one comparison function, so CLI-green/test-red
    # is impossible.
    print(compare_to_baseline(load_baseline(BASELINE_PATH), delivery_rows).text)

    if args.threshold is not None and total < args.threshold:
        print(f"below threshold {args.threshold}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
