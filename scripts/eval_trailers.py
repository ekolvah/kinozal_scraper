#!/usr/bin/env python3
"""Eval-harness подбора трейлера (#139, эпик трейлеров).

Читает golden-set (`tests/fixtures/trailer_golden.json`), прогоняет
`TrailerStrategy` по ЗАМОРОЖЕННЫМ кандидатам офлайн (без сети/квоты),
классифицирует каждый фильм Hit/Wrong/Miss ОТНОСИТЕЛЬНО эталона `correct` и
печатает взвешенную скоркарту. Метрика — раньше оптимизации: baseline красный,
порог затягивается по мере #141/#144.

Эталон `correct` — один id, accept-set (`list[str]` равноценных RU-дубляжей) или
null. Fail-loud (§IV/§VI): битая запись golden-set (пустой набор/accept-set,
отсутствующее поле, дубль `video_id`, `correct` неверного типа, id accept-set вне
пула) → GoldenSetError + exit≠0, НИКОГДА не деградирует в тихий Miss.

`--record` (dev-only, live) разово пересобирает снимок `candidates` из YouTube;
без `API_KEY` — явный fail-fast, не тихий no-op. Фикстуры frozen: `--record` —
для первичного посева / сознательного рефреша, не рутинный прогон.
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

# Standalone-run bootstrap: mirror pytest's pythonpath=["src"] так, чтобы
# `import kinozal_scraper` резолвился без editable install (как в ci_check.py).
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
# Пришпиленный исход DELIVERY-скоркарты (их в выводе три — pick / delivery / TMDB).
# Гейт — `tests/test_eval_baseline.py`, а не запись в `ci_check` CHECKS: `ci_check`
# и так гоняет pytest, а pre-push — ci_check, так что второй реестр не нужен (#379).
BASELINE_PATH = _FIXTURES / "trailer_baseline.json"


class GoldenSetError(ValueError):
    """Golden-set повреждён/невалиден — измерять по нему нельзя (fail-loud)."""


@dataclass
class GoldenCase:
    film: FilmProfile
    correct: str | list[str] | None
    candidates: list[Candidate]
    note: str
    # #329: замороженный снимок TMDB-видео (опционален — записи до #329 грузятся
    # с пустым списком; evaluate_tmdb по ним даёт Miss, пока не записан снимок).
    tmdb_videos: list[TmdbVideo] = field(default_factory=list)


def default_strategy() -> TrailerStrategy:
    """Стратегия «под оценкой». #141 — язык-aware `HeuristicStrategy` (был #139
    baseline `FirstResultStrategy`): язык первичен (RU>EN), каст — вторичный
    тай-брейк. Привязанный known-gap guard (#138-кейсы) стал audible → инвертирован."""
    return HeuristicStrategy()


def classify(correct: str | list[str] | None, pick_id: str | None) -> Outcome:
    """Исход относительно эталона. `correct` — один id, accept-set (несколько
    равноценных RU-дубляжей) или NONE. Null-ветка явная: при correct=NONE пустой
    pick — Hit (правильно ничего не выбрать), непустой — Wrong. Иначе Hit, если
    pick ∈ accept-set."""
    if correct is None:
        return "hit" if pick_id is None else "wrong"
    if pick_id is None:
        return "miss"
    accept = {correct} if isinstance(correct, str) else set(correct)
    return "hit" if pick_id in accept else "wrong"


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
    """Опциональный снимок TMDB-видео. Fail-loud (§IV): битый video (нет
    `key`/`iso_639_1`/`type`/`site`, не-str поля) → GoldenSetError, НЕ тихий дроп —
    иначе смещённый пул (потерянное RU-видео) запекается в замороженный снимок."""
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
    """`correct` — str | accept-set (list[str]) | null. Fail-loud (B2/S2):
    пустой accept-set (тихий коллапс в null-семантику, маскирует Miss/Wrong) и
    не-str элемент отвергаются; каждый id accept-set обязан быть в `valid_ids` —
    union пулов YouTube-`candidates` И TMDB-`tmdb_videos` (dual-source ground truth:
    валидный TMDB-key вне YouTube-пула легитимен, но typo-id, которого нет НИГДЕ,
    тихо превратил бы верный pick в wrong). Legacy single-str сохраняет miss-branch
    идиому (эталон-вне-пула → Miss) и от cross-check освобождён."""
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


def _parse_case(raw: Any, where: str) -> GoldenCase:
    if not isinstance(raw, dict):
        raise GoldenSetError(f"{where}: case must be an object, got {type(raw).__name__}")
    _require(raw, ("film", "correct", "candidates"), where)
    candidates = _parse_candidates(raw["candidates"], where)
    tmdb_videos = _parse_tmdb_videos(raw.get("tmdb_videos", []), where)
    valid_ids = {c.video_id for c in candidates} | {v.key for v in tmdb_videos}
    correct = _parse_correct(raw["correct"], valid_ids, where)
    return GoldenCase(
        film=_parse_film(raw["film"], where),
        correct=correct,
        candidates=candidates,
        note=raw.get("note", ""),
        tmdb_videos=tmdb_videos,
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
    """Прогон TMDB-источника: `pick_trailer` по ЗАМОРОЖЕННОМУ `tmdb_videos`,
    классификация против того же accept-set `correct`. Тот же контракт, что и
    `evaluate` (стратегия), — скоркарты сравнимы бок о бок.

    Scope: только кейсы с непустым `tmdb_videos`-снимком. Синтетические
    logic-фикстуры HeuristicStrategy (#138/#140 — placeholder-id вроде
    `dune2_official`, которые реальный YouTube-id из TMDB структурно НЕ может
    hit'нуть) снимка не несут (`_record_tmdb` их обнуляет) → вне cross-source
    сравнения. Реальный «TMDB ничего не нашёл» — непустой снимок без eligible
    видео → `pick_trailer`→None→Miss (не путать с out-of-scope)."""
    rows: list[tuple[GoldenCase, str | None, Outcome]] = []
    for case in cases:
        if not case.tmdb_videos:
            continue
        pick = pick_trailer(case.tmdb_videos)
        pick_id = pick.video_id if pick is not None else None
        rows.append((case, pick_id, classify(case.correct, pick_id)))
    return rows, score([o for _, _, o in rows])


class _FrozenRetrieval:
    """Retrieval-стаб для delivery-прогона: отдаёт замороженный пул кейса.

    Внешняя граница (§II) — заменяется законно; всё, что за ней (`select_trailer`),
    остаётся настоящим прод-кодом."""

    def __init__(self, pool: list[Candidate]) -> None:
        self._pool = pool

    # `_profile` с подчёркиванием: сигнатуру диктует интерфейс retrieval, а пул уже
    # заморожен — ARG002-ратчет (#236) в `scripts/**` не освобождён, в отличие от
    # `tests/**`, и глушить его per-file-ignore ради одного стаба неправильно.
    def search_candidates(self, _profile: FilmProfile) -> list[Candidate]:
        return list(self._pool)


def _pick_id_from_reply(reply: str, where: str) -> str | None:
    """Ответ прод-доставки → `video_id` | None. Fail-loud на всём остальном.

    Маркеры сверяются с ИМПОРТИРОВАННЫМИ константами, а не с копиями строк: реворд
    маркера в проде иначе тихо превратил бы miss в «непарсибельный ответ». `video_id`
    достаётся из query-параметра, а не срезом по префиксу, — формат URL живёт в
    `select_trailer`, дублировать его здесь нечего.

    Error-маркер — сбой ИНСТРУМЕНТА (retrieval-стаб бросить не может), и списать его
    в `miss` значило бы тихо просадить метрику и подумать на подбор (§IV)."""
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
    """Прогон через ПРОД-контракт доставки, а не только через `pick` (#379).

    `evaluate` меряет `TrailerStrategy.pick`; #359 сломал слой НАД ним
    (`kinozal_pipeline.select_trailer` — post-pick политика, §IV-маркеры, формат URL),
    поэтому pick-скоркарта была бы одинаковой до и после регресса. Здесь golden-set
    едет через прод-функцию и её ответ разбирается обратно в `video_id`.

    `select` параметризован симметрично `evaluate(strategy, cases)`: дефолт — настоящая
    прод-функция, а инъекция нужна, чтобы доказать срабатывание гейта на
    контрфактической политике (`tests/test_eval_baseline.py`), не держа её в `src`.
    """
    rows: list[tuple[GoldenCase, str | None, Outcome]] = []
    for i, case in enumerate(cases):
        reply = select(case.film, _FrozenRetrieval(case.candidates))
        pick_id = _pick_id_from_reply(reply, f"case[{i}] {case.film.ru_title!r}")
        rows.append((case, pick_id, classify(case.correct, pick_id)))
    return rows, score([o for _, _, o in rows])


# ── baseline-храповик поверх delivery-скоркарты (#379) ────────────────────────


@dataclass
class BaselineEntry:
    """Пришпиленный исход одного кейса. `i` рядом с `film`, потому что `ru_title`
    в наборе НЕ уникален («Гладиатор 2» встречается дважды) и своп двух одноимённых
    кейсов проверка по одному имени не увидела бы."""

    i: int
    film: str
    outcome: Outcome


@dataclass
class BaselineReport:
    """Вердикт сравнения. Чистые данные — ни I/O, ни exit: гейт (pytest) и печать
    зовут одну и ту же `compare_to_baseline`, поэтому «в CLI зелено, а в тесте
    красно» структурно невозможно."""

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
    """Fail-loud как у golden-set: битый baseline — это неизмеримый гейт, а не
    повод сравнить «как получится»."""
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
    """Чистое сравнение исходов. Расхождение — red в ЛЮБУЮ сторону.

    Улучшение тоже красное намеренно: «зелёное с предупреждением» воспроизводит ровно
    тот дефект, который чинит #379, — сигнал, который никто не обязан прочитать (§IV).
    Плюс с приходом wrong-кейсов (#380) суммарно-положительная дельта сможет прятать
    своп `hit→wrong`, а пофильмовое сравнение — нет. `--update-baseline` в том же PR
    делает улучшение видимым в диффе и ревьюабельным.

    Рассинхрон (длина / индекс / имя) — `GoldenSetError`: сравнивать позиционно
    разъехавшиеся наборы значит тихо сопоставлять разные фильмы."""
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


def _record(golden_path: str | Path) -> int:
    """dev-only live: пересобрать пулы кандидатов в golden-снимке.

    `search_candidates` вызывается без try/except **осознанно** (#383): при
    тотальном отказе retrieval (все ветки union упали, напр. 429) он поднимает
    `TrailerRetrievalError` и прогон падает целиком. Это желаемое поведение —
    записать `candidates: []`, вызванный квотой, значит отравить baseline,
    по которому потом меряется качество подбора. `write_text` идёт после цикла,
    так что частично перезаписанного файла не остаётся.
    """
    key = _require_api_key()
    cases = load_golden_set(golden_path)  # валидируем перед перезаписью
    from dataclasses import asdict

    from googleapiclient.discovery import build

    # §II: тот же union-retrieval, что и прод/будущая композиция — не вторая копия
    # query-build+snippet-map (был `_search_candidates`). RU попадает в записанный пул.
    from kinozal_scraper.youtube import search_candidates

    youtube = build("youtube", "v3", developerKey=key)
    raw = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    for entry, case in zip(raw, cases, strict=True):
        entry["candidates"] = [asdict(c) for c in search_candidates(youtube, case.film)]
    Path(golden_path).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"recorded candidates for {len(cases)} films → {golden_path}")
    return 0


def _record_tmdb(golden_path: str | Path) -> int:
    """dev-only live: пересобрать снимок `tmdb_videos`, ПЕРЕИСПОЛЬЗУЯ
    `TmdbClient.resolve` (§II — не вторая копия query-build, как `_record` тянет
    `search_candidates`). Без `TMDB_TOKEN` — fail-fast (KeyError в конструкторе).
    Малформный ответ (нет `key`) резолвер дропает; всё остальное — fail-loud при
    следующей загрузке снимка (`_parse_tmdb_videos`)."""
    cases = load_golden_set(golden_path)  # валидируем перед перезаписью
    from dataclasses import asdict

    from kinozal_scraper.tmdb_trailer import TmdbClient

    client = TmdbClient()
    raw = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    for entry, case in zip(raw, cases, strict=True):
        # Scope = реальные cross-source кейсы (accept-set-форма `correct: list`).
        # Синтетические logic-фикстуры (`str`/`null` correct) обнуляются: не тратим
        # TMDB-квоту на вымышленные тайтлы и не запекаем их шум в снимок (§IV).
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
    # Печать вердикта — информационная; красноту даёт `tests/test_eval_baseline.py`
    # (единственный носитель гейта, #379). Сравнивающая функция у них общая, поэтому
    # «в CLI зелено, в тесте красно» невозможно.
    print(compare_to_baseline(load_baseline(BASELINE_PATH), delivery_rows).text)

    if args.threshold is not None and total < args.threshold:
        print(f"below threshold {args.threshold}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
