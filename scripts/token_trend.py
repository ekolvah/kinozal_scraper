#!/usr/bin/env python3
"""Фактический расход токенов dev-сессий Claude Code и детектор его роста (#464).

**Зачем.** Приоритет (2) цель-функции не измерялся ничем: статический храповик
`tests/test_always_load_budget.py` стережёт *объявленную* плату (байты always-load набора),
но уплаченную — байты, умноженные на число turn'ов через `cache_read` — не видел никто.

**Носитель данных.** Транскрипты Claude Code (`~/.claude/projects/<slug>/*.jsonl`) чистятся
по `cleanupPeriodDays` (дефолт 30 дней), поэтому они не durable-стор: агрегат по ветке
дописывается в локальный ledger рядом с ними, иначе база сравнения молча исчезает через месяц.

**Режимы.** `--hook` — тихий, для `SessionStart`: печатает только аномалию и **всегда** выходит
с кодом 0 (`SessionStart` отдаёт stdout в контекст Claude лишь на exit 0). `--report` — таблица
по веткам.

**Чего метрика НЕ отвечает (границы скоупа).** Нормировка на turn убирает второй возможный
драйвер — «стало больше turn'ов на задачу»; он виден только в колонке `turns` отчёта. Сама
цена turn'а к тому же растёт с длиной сессии механически (контекст перечитывается целиком),
поэтому длинная ветка выглядит дороже короткой при одинаковой дисциплине. Вердикт `grown` —
повод посмотреть разбивку, а не приговор конкретной ветке. Codex-половина расхода
(`~/.codex/sessions`) сюда не входит вовсе.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import statistics
import sys
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


class TextSink(Protocol):
    """Минимальный контракт приёмника текста: и настоящий stdout, и тестовый двойник."""

    def write(self, text: str, /) -> int: ...


# Цена относительно свежего input-токена той же модели (прайсинг Anthropic на 2026-08-06:
# Opus 5 $5/$25 за Mtok, cache read 0.1x базового input, cache write 1.25x на 5m TTL и 2x на 1h).
# Ратио внутри модели одинаковы у всей линейки, различается только базовая цена — она вынесена
# в `_MODEL_SCALE`, чтобы переезд части работы на другую модель двигал сводную величину.
_RATIO_OUTPUT = 5.0
_RATIO_CACHE_READ = 0.1
_RATIO_CACHE_WRITE_5M = 1.25
_RATIO_CACHE_WRITE_1H = 2.0

# Базовая цена input-токена относительно Opus 5 ($5/Mtok = 1.0). Ключ — подстрока в `model`.
# Сопоставление по семейству, а не по точному id, — сознательный выбор: новая версия семейства
# (`claude-opus-6`) унаследует вес семейства молча, зато аномалия не поднимается на каждый
# релиз. Граница: смену цены **внутри** семейства метрика не заметит — сверять при бампе весов.
_MODEL_SCALE: dict[str, float] = {
    "fable": 2.0,
    "opus": 1.0,
    "sonnet": 0.6,
    "haiku": 0.2,
    # Служебные записи Claude Code (21 штука в реальной выборке): нулевой usage, не биллятся.
    # В таблице они не ради веса, а чтобы не поднимать `unknown_model` каждый старт.
    "<synthetic>": 0.0,
}
_UNKNOWN_MODEL_SCALE = 1.0

# Бакеты, которые не участвуют в тренде: `main` разнороден (планирование, ревью, чтение),
# записи без ветки — сессии вне репо-контекста. Оба видны в отчёте, но не в детекторе.
MAIN_BUCKET = "main"
NO_BRANCH_BUCKET = "(no-branch)"

LEDGER_NAME = "token_ledger.jsonl"
LEDGER_SCHEMA = 1

# Машинный канон интерфейса: алерт печатается в контекст и предлагает оператору флаг, поэтому
# расхождение текста с `argparse` отправляло бы его в `unrecognized arguments` ровно в тот
# момент, ради которого метрика заведена. Гейтится `TestReportMode`.
CLI_FLAGS = ("--hook", "--report")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# Порог и окно подобраны по фактическим данным на стадии реализации (цифры — в теле PR).
# Порог сознательно крупный: алерт печатается в контекст каждого старта, поэтому чуткий
# детектор сам становится статьёй расхода по приоритету (2).
DEFAULT_WINDOW = 5
DEFAULT_REL_THRESHOLD = 0.4
DEFAULT_ABS_FLOOR = 20_000.0

# Окно чтения транскриптов шире ретенции Claude Code (30 дней): всё, что старше, уже живёт
# в ledger'е, и перечитывать его каждый старт незачем.
DEFAULT_MTIME_DAYS = 45


@dataclass(frozen=True)
class UsageRecord:
    """Один turn: дедуплицированная usage-запись из транскрипта."""

    timestamp: str
    session_id: str
    branch: str
    model: str
    is_sidechain: bool
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int


@dataclass(frozen=True)
class Anomaly:
    """Видимый сбой метрики: не бывает тихим (§IV)."""

    kind: str  # "malformed_line" | "no_usage_records" | "high_anomaly_rate" | "unknown_model"
    detail: str


@dataclass(frozen=True)
class BranchStats:
    """Агрегат расхода по одной ветке — единица и отчёта, и ledger'а."""

    branch: str
    turns: int
    first_seen: str
    last_seen: str
    effective: float
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    sidechain_effective: float

    @property
    def per_turn(self) -> float:
        """Effective tokens на turn — шкала «каждый шаг стал дороже»."""
        return self.effective / self.turns if self.turns else 0.0


@dataclass(frozen=True)
class Verdict:
    """Исход детекции: `grown` / `steady` / `insufficient_data`."""

    status: str
    recent_median: float
    baseline_median: float
    recent_branches: tuple[str, ...]
    baseline_branches: tuple[str, ...]


def _int(source: dict, key: str) -> int:
    value = source.get(key)
    return value if isinstance(value, int) else 0


def _dedup_key(entry: dict, fallback: int) -> object:
    """Ключ дедупликации: `requestId`, иначе `uuid`, иначе позиция.

    Записи без `requestId` существуют (8 штук в реальной выборке): дедуп по отсутствующему
    ключу схлопнул бы их в одну и выглядел бы как падение расхода.
    """
    request_id = entry.get("requestId")
    if isinstance(request_id, str) and request_id:
        return ("req", request_id)
    uuid = entry.get("uuid")
    if isinstance(uuid, str) and uuid:
        return ("uuid", uuid)
    return ("pos", fallback)


def parse_lines(lines: Iterable[str]) -> tuple[list[UsageRecord], list[Anomaly]]:
    """Разобрать строки транскрипта в записи + аномалии, дедуплицируя по `requestId`."""
    records: list[UsageRecord] = []
    anomalies: list[Anomaly] = []
    seen: set[object] = set()
    for index, raw in enumerate(lines):
        text = raw.strip()
        if not text:
            continue
        try:
            entry = json.loads(text)
        except json.JSONDecodeError as exc:
            anomalies.append(Anomaly("malformed_line", f"line {index + 1}: {exc}"))
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            # Апстрим переименовал/убрал поле — записей просто не будет, и это ловит
            # `health_anomalies`: тишина здесь не равна «роста нет».
            continue
        key = _dedup_key(entry, index)
        if key in seen:
            continue
        seen.add(key)
        creation = usage.get("cache_creation")
        if isinstance(creation, dict):
            write_5m = _int(creation, "ephemeral_5m_input_tokens")
            write_1h = _int(creation, "ephemeral_1h_input_tokens")
        else:
            # Без детализации TTL считаем всю запись кэша по 5-минутному тарифу — это
            # нижняя оценка платы, а не подстановка нуля.
            write_5m, write_1h = _int(usage, "cache_creation_input_tokens"), 0
        branch = entry.get("gitBranch")
        records.append(
            UsageRecord(
                timestamp=str(entry.get("timestamp") or ""),
                session_id=str(entry.get("sessionId") or ""),
                branch=branch if isinstance(branch, str) and branch else NO_BRANCH_BUCKET,
                model=str(message.get("model") or ""),
                is_sidechain=bool(entry.get("isSidechain")),
                input_tokens=_int(usage, "input_tokens"),
                output_tokens=_int(usage, "output_tokens"),
                cache_read=_int(usage, "cache_read_input_tokens"),
                cache_write_5m=write_5m,
                cache_write_1h=write_1h,
            )
        )
    return records, anomalies


def model_scale(model: str) -> float:
    """Множитель базовой цены модели; неизвестная модель — 1.0 (и повод для аномалии)."""
    lowered = model.lower()
    for marker, scale in _MODEL_SCALE.items():
        if marker in lowered:
            return scale
    return _UNKNOWN_MODEL_SCALE


def is_known_model(model: str) -> bool:
    """Есть ли модель в таблице весов: новая модель делает сводную величину неверной."""
    lowered = model.lower()
    return any(marker in lowered for marker in _MODEL_SCALE)


def effective_tokens(record: UsageRecord) -> float:
    """Сводная величина в input-эквивалентах Opus 5 — взвешенная по относительной цене."""
    weighted = (
        record.input_tokens
        + record.output_tokens * _RATIO_OUTPUT
        + record.cache_read * _RATIO_CACHE_READ
        + record.cache_write_5m * _RATIO_CACHE_WRITE_5M
        + record.cache_write_1h * _RATIO_CACHE_WRITE_1H
    )
    return weighted * model_scale(record.model)


def health_anomalies(
    records: list[UsageRecord],
    parse_anomalies: list[Anomaly],
    *,
    files_seen: int,
    anomaly_rate_threshold: float = 0.05,
) -> list[Anomaly]:
    """Аномалии уровня выборки: schema drift (файлы есть, записей нет) и доля битых строк."""
    anomalies: list[Anomaly] = []
    if files_seen and not records:
        anomalies.append(
            Anomaly(
                "no_usage_records",
                f"файлов прочитано {files_seen}, usage-записей 0 — вероятен schema drift",
            )
        )
    total = len(records) + len(parse_anomalies)
    if total and len(parse_anomalies) / total > anomaly_rate_threshold:
        share = len(parse_anomalies) / total
        anomalies.append(
            Anomaly(
                "high_anomaly_rate", f"нераспарсенных строк {share:.0%} ({len(parse_anomalies)})"
            )
        )
    unknown = sorted({r.model for r in records if r.model and not is_known_model(r.model)})
    if unknown:
        anomalies.append(
            Anomaly("unknown_model", f"нет в таблице весов: {', '.join(unknown)} — цифры занижены")
        )
    return anomalies


def counts_as_turn(record: UsageRecord) -> bool:
    """Идёт ли запись в знаменатель `per_turn`.

    Turn — шаг **основного** цикла агента. Sidechain-записи (субагенты) дешёвые и
    многочисленные, а spawn субагента — рекомендованная тактика (`mindset.md §(2)`):
    в знаменателе они разбавляли бы `per_turn` и детектор выдавал бы `steady` на растущей
    плате. Плату они при этом вносят — она видна в `effective` и отдельной колонкой.
    Служебные `<synthetic>`-записи не биллятся вовсе, поэтому turn'ами тоже не считаются.
    """
    return not record.is_sidechain and model_scale(record.model) > 0


def _earliest(current: str, candidate: str) -> str:
    """Минимальный непустой timestamp: пустой иначе навсегда пиннит ветку в начало окна."""
    known = [value for value in (current, candidate) if value]
    return min(known) if known else ""


def aggregate_by_branch(records: Iterable[UsageRecord]) -> dict[str, BranchStats]:
    """Свернуть записи в агрегат по ветке; записи без ветки — в отдельный бакет, не выброс."""
    stats: dict[str, BranchStats] = {}
    for record in records:
        cost = effective_tokens(record)
        current = stats.get(record.branch)
        if current is None:
            stats[record.branch] = BranchStats(
                branch=record.branch,
                turns=int(counts_as_turn(record)),
                first_seen=record.timestamp,
                last_seen=record.timestamp,
                effective=cost,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cache_read=record.cache_read,
                cache_write=record.cache_write_5m + record.cache_write_1h,
                sidechain_effective=cost if record.is_sidechain else 0.0,
            )
            continue
        stats[record.branch] = BranchStats(
            branch=current.branch,
            turns=current.turns + int(counts_as_turn(record)),
            first_seen=_earliest(current.first_seen, record.timestamp),
            last_seen=max(current.last_seen, record.timestamp),
            effective=current.effective + cost,
            input_tokens=current.input_tokens + record.input_tokens,
            output_tokens=current.output_tokens + record.output_tokens,
            cache_read=current.cache_read + record.cache_read,
            cache_write=current.cache_write + record.cache_write_5m + record.cache_write_1h,
            sidechain_effective=current.sidechain_effective
            + (cost if record.is_sidechain else 0.0),
        )
    return stats


def issue_branches(stats: Iterable[BranchStats]) -> list[BranchStats]:
    """Только issue-ветки, упорядоченные по времени первой записи (`main` и no-branch — вне)."""
    kept = [s for s in stats if s.branch not in (MAIN_BUCKET, NO_BRANCH_BUCKET)]
    return sorted(kept, key=lambda s: (s.first_seen, s.branch))


def merge_ledger(
    ledger: dict[str, BranchStats], fresh: dict[str, BranchStats]
) -> dict[str, BranchStats]:
    """Объединить историю с текущими транскриптами: свежий агрегат замещает, не суммируется.

    Замещение верно, только пока свежий агрегат **полнее** исторического. У ветки старше окна
    чтения (`DEFAULT_MTIME_DAYS`) или ретенции Claude Code часть транскриптов уже исчезла,
    поэтому пересчёт по ней неполон: приняв его, ветка «худела» бы молча, а недосчёт читался
    бы как падение расхода — ровно тот отказ, ради которого ledger и заведён. Первыми
    страдали бы долгоживущие бакеты (`main` — 37 % turn'ов по замеру).
    """
    merged = dict(ledger)
    for branch, entry in fresh.items():
        known = merged.get(branch)
        if known is None or entry.turns >= known.turns:
            merged[branch] = entry
    return merged


def parse_ledger(lines: Iterable[str]) -> tuple[dict[str, BranchStats], list[Anomaly]]:
    """Прочитать ledger; битая строка — аномалия, а не молчаливый пропуск."""
    stats: dict[str, BranchStats] = {}
    anomalies: list[Anomaly] = []
    for index, raw in enumerate(lines):
        text = raw.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            anomalies.append(Anomaly("malformed_line", f"ledger line {index + 1}: {exc}"))
            continue
        if not isinstance(payload, dict) or payload.pop("schema", None) != LEDGER_SCHEMA:
            anomalies.append(
                Anomaly(
                    "ledger_schema", f"ledger line {index + 1}: ожидалась schema {LEDGER_SCHEMA}"
                )
            )
            continue
        try:
            entry = BranchStats(**payload)
        except TypeError as exc:
            anomalies.append(Anomaly("ledger_schema", f"ledger line {index + 1}: {exc}"))
            continue
        stats[entry.branch] = entry
    return stats, anomalies


def ledger_lines(stats: dict[str, BranchStats]) -> list[str]:
    """Сериализовать агрегаты в JSONL-строки ledger'а."""
    return [
        json.dumps({"schema": LEDGER_SCHEMA, **asdict(entry)}, ensure_ascii=False)
        for _, entry in sorted(stats.items())
    ]


def detect_growth(
    stats: Iterable[BranchStats],
    *,
    window: int = DEFAULT_WINDOW,
    rel_threshold: float = DEFAULT_REL_THRESHOLD,
    abs_floor: float = DEFAULT_ABS_FLOOR,
) -> Verdict:
    """Сравнить медиану per-turn последних `window` веток с предыдущими `window`.

    Медиана, а не среднее: одна длинная рефактор-сессия перевешивает выборку. Абсолютный пол
    вдобавок к относительному: алерт печатается в контекст каждого старта, поэтому шумный
    детектор сам становится статьёй расхода по приоритету (2).
    """
    ordered = issue_branches(stats)
    if len(ordered) < 2 * window:
        return Verdict("insufficient_data", 0.0, 0.0, (), ())
    baseline = ordered[-2 * window : -window]
    recent = ordered[-window:]
    baseline_median = statistics.median(s.per_turn for s in baseline)
    recent_median = statistics.median(s.per_turn for s in recent)
    grown = (
        recent_median >= baseline_median * (1 + rel_threshold)
        and recent_median - baseline_median >= abs_floor
    )
    return Verdict(
        "grown" if grown else "steady",
        recent_median,
        baseline_median,
        tuple(s.branch for s in recent),
        tuple(s.branch for s in baseline),
    )


def _k(value: float) -> str:
    return f"{value / 1000:.1f}k"


def format_report(stats: Iterable[BranchStats], verdict: Verdict, anomalies: list[Anomaly]) -> str:
    """Полная таблица по веткам + вердикт."""
    rows = sorted(stats, key=lambda s: (s.first_seen, s.branch))
    lines = [
        f"{'branch':<48} {'turns':>6} {'effective':>12} {'per-turn':>10} "
        f"{'cache_read':>12} {'sidechain':>11}"
    ]
    for entry in rows:
        lines.append(
            f"{entry.branch[:48]:<48} {entry.turns:>6} {_k(entry.effective):>12} "
            f"{_k(entry.per_turn):>10} {_k(entry.cache_read):>12} "
            f"{_k(entry.sidechain_effective):>11}"
        )
    lines.append("")
    if verdict.status == "insufficient_data":
        lines.append(f"вердикт: недостаточно данных (нужно {2 * DEFAULT_WINDOW} issue-веток)")
    else:
        delta = verdict.recent_median - verdict.baseline_median
        share = delta / verdict.baseline_median if verdict.baseline_median else 0.0
        lines.append(
            f"вердикт: {verdict.status} — per-turn медиана {_k(verdict.baseline_median)} → "
            f"{_k(verdict.recent_median)} ({share:+.0%})"
        )
    lines.extend(f"аномалия [{a.kind}]: {a.detail}" for a in anomalies)
    return "\n".join(lines)


def format_alert(verdict: Verdict, anomalies: list[Anomaly]) -> str:
    """Текст для hook-режима; пустая строка — норма, печатать нечего."""
    lines: list[str] = []
    if verdict.status == "grown":
        delta = verdict.recent_median - verdict.baseline_median
        share = delta / verdict.baseline_median if verdict.baseline_median else 0.0
        lines.append(
            f"token-trend: расход на turn вырос {_k(verdict.baseline_median)} → "
            f"{_k(verdict.recent_median)} ({share:+.0%}) на последних "
            f"{len(verdict.recent_branches)} ветках — `python scripts/token_trend.py --report`"
        )
    lines.extend(f"token-trend аномалия [{a.kind}]: {a.detail}" for a in anomalies)
    return "\n".join(lines)


def read_payload(stdin_text: str) -> dict:
    """Разобрать `SessionStart` JSON; толерантно к пустому/битому вводу → {}.

    Зеркалит `scripts/hooks.py`: ненулевой выход хука проглотил бы собственный алерт
    (`SessionStart` игнорирует stdout на exit 2) и показал бы юзеру ошибку каждую сессию.
    """
    stdin_text = (stdin_text or "").strip()
    if not stdin_text:
        return {}
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _iter_lines(paths: Iterable[Path]) -> Iterator[str]:
    for path in paths:
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            # Файл исчез между отбором и чтением — сессия удалена, ретенция сработала.
            continue
        with handle:
            yield from handle


def _recent_paths(transcripts: Path, cutoff: float) -> list[Path]:
    """Файлы транскриптов в окне по mtime; ledger — не транскрипт и сюда не попадает."""
    paths = []
    for path in transcripts.glob("*.jsonl"):
        if path.name == LEDGER_NAME:
            continue
        try:
            fresh = path.stat().st_mtime >= cutoff
        except OSError:
            # Между `glob` и `stat` файл может исчезнуть: в report-режиме это был бы traceback.
            continue
        if fresh:
            paths.append(path)
    return sorted(paths)


def collect(
    transcripts: Path, *, days: int = DEFAULT_MTIME_DAYS
) -> tuple[list[UsageRecord], list[Anomaly], int]:
    """Прочитать транскрипты в окне по mtime; вернуть записи, аномалии и число файлов.

    Дедупликация — сквозная по всем файлам: `resume`/`fork` копируют историю в новый
    транскрипт, и пофайловый разбор дал бы двойной счёт.

    Ledger лежит в этом же каталоге и переписывается каждую сессию, поэтому всегда свежий
    по mtime. Считая его транскриптом, после ретенции (файлы вычищены, ledger остался) мы
    получали бы `files_seen=1, records=0` — вечную ложную «schema drift» в контексте.
    """
    cutoff = time.time() - days * 86_400
    paths = _recent_paths(transcripts, cutoff)
    records, anomalies = parse_lines(_iter_lines(paths))
    return records, anomalies, len(paths)


def transcript_dir() -> Path:
    """Каталог транскриптов Claude Code для этого репо на текущей машине."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(_REPO_ROOT))
    return _CLAUDE_PROJECTS / slug


def run_hook(payload: dict, transcripts: Path, ledger_path: Path) -> str:
    """Текст для stdout хука: пусто в норме, в чужой среде и на не-startup источнике."""
    if payload.get("source") != "startup":
        return ""
    if not transcripts.is_dir():
        if not transcripts.parent.is_dir():
            # Чужая среда: транскриптов Claude Code тут нет вовсе (cloud-ревьюер, другая
            # машина). `.claude/settings.json` лежит в репо, и видимая ошибка попадала бы
            # в контекст каждой ревью-сессии навсегда.
            return ""
        # Своя машина, но каталога этого репо нет: апстрим сменил правило slug'а, репо
        # переехало. Промолчать здесь — дать метрике умереть навсегда и тихо (§IV).
        return format_alert(
            Verdict("insufficient_data", 0.0, 0.0, (), ()),
            [Anomaly("transcripts_not_found", f"нет каталога {transcripts} — slug-правило уехало")],
        )
    records, parse_anomalies, files_seen = collect(transcripts)
    anomalies = health_anomalies(records, parse_anomalies, files_seen=files_seen)
    ledger, ledger_anomalies = parse_ledger(_read_ledger_lines(ledger_path))
    merged = merge_ledger(ledger, aggregate_by_branch(records))
    write_ledger(ledger_path, merged)
    return format_alert(detect_growth(merged.values()), anomalies + ledger_anomalies)


def write_ledger(ledger_path: Path, stats: dict[str, BranchStats]) -> None:
    """Записать ledger атомарно: усечённый файл — потеря всей базы сравнения.

    `write_text` сначала обнуляет файл, поэтому краш или две одновременно стартующие сессии
    оставили бы метрику без истории — ровно тот отказ, ради которого ledger и заведён.
    """
    tmp = ledger_path.with_name(ledger_path.name + f".{os.getpid()}.tmp")
    tmp.write_text("\n".join(ledger_lines(stats)) + "\n", encoding="utf-8")
    os.replace(tmp, ledger_path)


def _read_ledger_lines(ledger_path: Path) -> list[str]:
    if not ledger_path.exists():
        return []
    return ledger_path.read_text(encoding="utf-8").splitlines()


def emit(text: str, stream: TextSink | None = None) -> None:
    """Напечатать текст в UTF-8 независимо от кодовой страницы консоли.

    Вывод кириллический, а стандартный stdout на Windows — cp1252 (`CLAUDE.md` §Среда):
    обычный `print` роняет процесс `UnicodeEncodeError`. Для хука это не косметика —
    ненулевой код заставил бы `SessionStart` выбросить stdout, и вместо алерта юзер
    увидел бы голое «hook error».
    """
    stream = stream if stream is not None else sys.stdout
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(text + "\n")
        return
    buffer.write((text + "\n").encode("utf-8"))
    buffer.flush()


def main() -> int:
    """CLI: `--hook` (тихий, всегда exit 0) и `--report` (таблица, режим по умолчанию)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hook", action="store_true", help="режим SessionStart: тихий, exit 0")
    parser.add_argument("--report", action="store_true", help="таблица по веткам (по умолчанию)")
    args = parser.parse_args()

    transcripts = transcript_dir()
    ledger_path = transcripts / LEDGER_NAME

    if args.hook:
        try:
            text = run_hook(read_payload(sys.stdin.read()), transcripts, ledger_path)
            if text:
                emit(text)
        except OSError as exc:
            # Сам сбой метрики виден, но код остаётся нулевым: иначе `SessionStart`
            # выбросит stdout и юзер увидит только «hook error» без содержания.
            # `emit` внутри try по той же причине — закрытый stdout не должен ронять хук.
            with contextlib.suppress(OSError):
                emit(f"token-trend аномалия [io]: {exc}", sys.stderr)
        return 0

    if not transcripts.is_dir():
        emit(f"нет каталога транскриптов: {transcripts}", sys.stderr)
        return 1
    records, parse_anomalies, files_seen = collect(transcripts)
    anomalies = health_anomalies(records, parse_anomalies, files_seen=files_seen)
    ledger, ledger_anomalies = parse_ledger(_read_ledger_lines(ledger_path))
    merged = merge_ledger(ledger, aggregate_by_branch(records))
    emit(
        format_report(merged.values(), detect_growth(merged.values()), anomalies + ledger_anomalies)
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
