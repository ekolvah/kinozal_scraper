#!/usr/bin/env python3
"""Raw token use in Claude Code development sessions and its growth detector (#464, #565).

**Why.** Priority (2) in the objective function had no measurement: static ratchet
`tests/test_always_load_budget.py` guards *declared* cost (always-load bytes), not observed
token volume.

**Data carrier.** Claude Code transcripts (`~/.claude/projects/<slug>/*.jsonl`) are cleaned
by `cleanupPeriodDays` (default 30 days), so are not durable storage. Per-branch aggregate
is appended to a nearby local ledger or comparison baseline silently vanishes after a month.

**Modes.** `--hook` is quiet for `SessionStart`: prints only anomaly and **always** exits 0
(`SessionStart` sends stdout to Claude context only at exit 0). `--report` prints branch table.

**What it does NOT answer (scope boundary).** Per-turn normalization removes the other
possible driver, more turns per task; it is visible only in report `turns`. Turn cost also
mechanically rises with session length because full context is reread, so a long branch
looks costlier than a short one under equal discipline. `grown` prompts breakdown review,
not judgment of a branch. Codex use (`~/.codex/sessions`) is excluded.
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
    """Minimal text-sink contract for real stdout and a test double."""

    def write(self, text: str, /) -> int: ...


# Buckets excluded from trend: `main` is heterogeneous (planning, review, reading), and
# no-branch records are sessions outside repository context. Both report, neither detects.
MAIN_BUCKET = "main"
NO_BRANCH_BUCKET = "(no-branch)"

LEDGER_NAME = "token_ledger.jsonl"
LEDGER_SCHEMA = 2

# Deliberately no ledger pruning: a branch line is ~200 B, so 72 current-history buckets are
# under 15 KB and the file grows by a few branches weekly. Parsing cost is years away; an
# expiry policy now would guess a window without data. Reconsider when hook `timeout` fires.

# Machine interface canon: alert prints into context and suggests an operator flag, so drift
# from `argparse` would produce `unrecognized arguments` at the critical moment. Guarded by
# `TestReportMode`.
CLI_FLAGS = ("--hook", "--report")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# Threshold and window came from implementation-stage data (numbers in PR body). On 2026-08-21,
# two five-branch windows measured 120,241.9 then 103,680.3 raw tokens/turn. The 100k floor is
# on the observed baseline's order, so it remains a distinct gate beyond 40% relative growth.
DEFAULT_WINDOW = 5
DEFAULT_REL_THRESHOLD = 0.4
DEFAULT_ABS_FLOOR = 100_000.0

# Transcript-reading window exceeds Claude Code retention (30 days): older data is already in
# ledger, so rereading it on every start is unnecessary.
DEFAULT_MTIME_DAYS = 45

# Mirrors hook `timeout` in `.claude/settings.json` (test-checked). Warn here before users kill
# the hook: a killed hook never reaches `write_ledger`, leaving the metric stalled silently.
HOOK_TIMEOUT_SECONDS = 20
_SLOW_COLLECT_SHARE = 0.6


@dataclass(frozen=True)
class UsageRecord:
    """One turn: deduplicated usage record from transcript."""

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
    """Visible metric failure: never silent (§IV)."""

    kind: str  # "malformed_line" | "no_usage_records" | "high_anomaly_rate" | "unknown_model"
    detail: str


@dataclass(frozen=True)
class BranchStats:
    """Per-branch usage aggregate: unit of both report and ledger."""

    branch: str
    turns: int
    first_seen: str
    last_seen: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    sidechain_tokens: int | None

    @property
    def total_tokens(self) -> int:
        """All independently observed token counters, with no price conversion."""
        return self.input_tokens + self.output_tokens + self.cache_read + self.cache_write

    @property
    def per_turn(self) -> float:
        """Observed tokens per main-loop turn."""
        return self.total_tokens / self.turns if self.turns else 0.0


@dataclass(frozen=True)
class Verdict:
    """Detection outcome: `grown` / `steady` / `insufficient_data`."""

    status: str
    recent_median: float
    baseline_median: float
    recent_branches: tuple[str, ...]
    baseline_branches: tuple[str, ...]


def _int(source: dict, key: str) -> int:
    value = source.get(key)
    return value if isinstance(value, int) else 0


def _dedup_key(entry: dict, fallback: int) -> object:
    """Deduplication key: `requestId`, otherwise `uuid`, otherwise position.

    Records without `requestId` exist (eight in the real sample): deduplication on a
    missing key would collapse them to one and look like falling usage.
    """
    request_id = entry.get("requestId")
    if isinstance(request_id, str) and request_id:
        return ("req", request_id)
    uuid = entry.get("uuid")
    if isinstance(uuid, str) and uuid:
        return ("uuid", uuid)
    return ("pos", fallback)


def parse_lines(lines: Iterable[str]) -> tuple[list[UsageRecord], list[Anomaly]]:
    """Parse transcript lines into records and anomalies, deduplicating by `requestId`."""
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
            # If upstream renames/removes the field, no records remain; `health_anomalies`
            # catches that because silence here does not mean “no growth.”
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
            # Without TTL details, price all cache entries at the 5-minute rate: a lower
            # cost estimate, not substitution of zero.
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


def is_expected_model(model: str) -> bool:
    """Recognize transcript model syntax without assigning it a price or weight."""
    return model == "<synthetic>" or model.lower().startswith("claude-")


def is_synthetic(record: UsageRecord) -> bool:
    """Service records have a literal model marker in Claude Code transcripts."""
    return record.model == "<synthetic>"


def health_anomalies(
    records: list[UsageRecord],
    parse_anomalies: list[Anomaly],
    *,
    files_seen: int,
    anomaly_rate_threshold: float = 0.05,
) -> list[Anomaly]:
    """Sample-level anomalies: schema drift (files exist, records do not) and bad-line share."""
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
    unknown = sorted({r.model or "(пусто)" for r in records if not is_expected_model(r.model)})
    if unknown:
        anomalies.append(Anomaly("unknown_model", f"unexpected model field: {', '.join(unknown)}"))
    return anomalies


def counts_as_turn(record: UsageRecord) -> bool:
    """Whether record enters the `per_turn` denominator.

    A turn is a **main** agent-loop step. Sidechain records (subagents) are cheap and many,
    while subagent spawning is recommended (`mindset.md`): in denominator they would dilute
    `per_turn` and report `steady` amid rising consumption. Their raw tokens remain in a separate
    column. Service `<synthetic>` records are not turns either.
    """
    return not record.is_sidechain and not is_synthetic(record)


def _earliest(current: str, candidate: str) -> str:
    """Minimum nonempty timestamp; empty would pin branch to window start forever."""
    known = [value for value in (current, candidate) if value]
    return min(known) if known else ""


def aggregate_by_branch(records: Iterable[UsageRecord]) -> dict[str, BranchStats]:
    """Fold records into branch aggregate; no-branch records get a bucket, not discard."""
    stats: dict[str, BranchStats] = {}
    for record in records:
        raw_tokens = (
            record.input_tokens
            + record.output_tokens
            + record.cache_read
            + record.cache_write_5m
            + record.cache_write_1h
        )
        current = stats.get(record.branch)
        if current is None:
            stats[record.branch] = BranchStats(
                branch=record.branch,
                turns=int(counts_as_turn(record)),
                first_seen=record.timestamp,
                last_seen=record.timestamp,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cache_read=record.cache_read,
                cache_write=record.cache_write_5m + record.cache_write_1h,
                sidechain_tokens=raw_tokens if record.is_sidechain else 0,
            )
            continue
        stats[record.branch] = BranchStats(
            branch=current.branch,
            turns=current.turns + int(counts_as_turn(record)),
            first_seen=_earliest(current.first_seen, record.timestamp),
            last_seen=max(current.last_seen, record.timestamp),
            input_tokens=current.input_tokens + record.input_tokens,
            output_tokens=current.output_tokens + record.output_tokens,
            cache_read=current.cache_read + record.cache_read,
            cache_write=current.cache_write + record.cache_write_5m + record.cache_write_1h,
            sidechain_tokens=(current.sidechain_tokens or 0)
            + (raw_tokens if record.is_sidechain else 0),
        )
    return stats


def issue_branches(stats: Iterable[BranchStats]) -> list[BranchStats]:
    """Issue branches with a nonempty denominator, ordered by first-record time.

    `main` and no-branch are heterogeneous and outside the trend. A `turns == 0` bucket (all
    records are subagent or service) is excluded for a different reason: its `per_turn` is zero,
    so in `statistics.median` it would be ranked alongside real branches — suppressing an alert
    in the recent window and inflating the percentage in the baseline. It would settle there
    forever: after retention, such a bucket can no longer be recomputed, and it does not leave
    the ledger.
    """
    kept = [s for s in stats if s.branch not in (MAIN_BUCKET, NO_BRANCH_BUCKET) and s.turns > 0]
    return sorted(kept, key=lambda s: (s.first_seen, s.branch))


def merge_ledger(
    ledger: dict[str, BranchStats], fresh: dict[str, BranchStats]
) -> dict[str, BranchStats]:
    """Merge history with current transcripts: fresh aggregate replaces, not sums.

    Replacement is valid only while fresh aggregate is **more complete** than history. Beyond
    reading window (`DEFAULT_MTIME_DAYS`) or Claude Code retention, transcripts disappear and
    recomputation is incomplete; accepting it silently shrinks a branch and reads undercount
    as falling usage—the failure ledger prevents. Long-lived buckets suffer first (`main` 37%).

    The tradeoff is explicit: long-lived bucket freezes at historical peak. `main` reading
    window (45 days) exceeds retention (30), so fresh recomputation is systematically smaller
    and its report line stops updating. Day-lived issue branches are unaffected; main is not trend.
    """
    merged = dict(ledger)
    for branch, entry in fresh.items():
        known = merged.get(branch)
        if known is None or entry.turns >= known.turns:
            merged[branch] = entry
    return merged


def parse_ledger(lines: Iterable[str]) -> tuple[dict[str, BranchStats], list[Anomaly]]:
    """Read ledger; corrupt line is anomaly, not silent skip."""
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
        if not isinstance(payload, dict):
            anomalies.append(
                Anomaly("ledger_schema", f"ledger line {index + 1}: expected JSON object")
            )
            continue
        try:
            schema = payload.pop("schema", None)
            entry = _migrate_ledger_entry(schema, payload)
        except (KeyError, TypeError) as exc:
            anomalies.append(Anomaly("ledger_schema", f"ledger line {index + 1}: {exc}"))
            continue
        if not _fields_well_typed(entry):
            # `"turns": "12"` passes constructor but fails in `merge_ledger` before
            # `write_ledger`, so file would never rewrite and metric would die
            # forever. As a normal anomaly, corrupt record is discarded and ledger
            # heals itself on next start.
            anomalies.append(
                Anomaly("ledger_schema", f"ledger line {index + 1}: неверные типы полей")
            )
            continue
        stats[entry.branch] = entry
    return stats, anomalies


def _migrate_ledger_entry(schema: object, payload: dict) -> BranchStats:
    """Read the raw portion of schema 1 without inventing its priced sidechain breakdown."""
    if schema == LEDGER_SCHEMA:
        return BranchStats(**payload)
    if schema == 1:
        return BranchStats(
            branch=payload["branch"],
            turns=payload["turns"],
            first_seen=payload["first_seen"],
            last_seen=payload["last_seen"],
            input_tokens=payload["input_tokens"],
            output_tokens=payload["output_tokens"],
            cache_read=payload["cache_read"],
            cache_write=payload["cache_write"],
            sidechain_tokens=None,
        )
    raise TypeError(f"expected schema {LEDGER_SCHEMA} or 1 (got {schema!r})")


def _fields_well_typed(entry: BranchStats) -> bool:
    """Ledger field types: `schema` version guards shape, not types."""
    return (
        isinstance(entry.branch, str)
        and isinstance(entry.turns, int)
        and isinstance(entry.first_seen, str)
        and isinstance(entry.last_seen, str)
        and isinstance(entry.input_tokens, int)
        and isinstance(entry.output_tokens, int)
        and isinstance(entry.cache_read, int)
        and isinstance(entry.cache_write, int)
        and (entry.sidechain_tokens is None or isinstance(entry.sidechain_tokens, int))
    )


def ledger_lines(stats: dict[str, BranchStats]) -> list[str]:
    """Serialize aggregates into ledger JSONL lines."""
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
    """Compare recent `window` per-turn median with preceding `window`.

    Median, not mean: one long refactor session should not outweigh sample. Absolute floor
    supplements relative threshold because alert enters every start context, so a noisy
    detector itself becomes a priority-(2) cost.
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
    """Full branch table and verdict."""
    rows = sorted(stats, key=lambda s: (s.first_seen, s.branch))
    lines = [
        f"{'branch':<48} {'turns':>6} {'tokens':>12} {'per-turn':>10} "
        f"{'cache_read':>12} {'sidechain':>11}"
    ]
    for entry in rows:
        # Dash, not `0.0k`: a bucket without main turns has no denominator, and zero beside
        # nonempty token total would read as “branch is free.”
        per_turn = _k(entry.per_turn) if entry.turns else "—"
        sidechain = _k(entry.sidechain_tokens) if entry.sidechain_tokens is not None else "—"
        lines.append(
            f"{entry.branch[:48]:<48} {entry.turns:>6} {_k(entry.total_tokens):>12} "
            f"{per_turn:>10} {_k(entry.cache_read):>12} "
            f"{sidechain:>11}"
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
    """Hook-mode text; empty string is normal when there is nothing to print."""
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
    """Parse `SessionStart` JSON; tolerate empty/corrupt input → {}.

    Mirrors `scripts/hooks.py`: nonzero hook exit would swallow its own alert
    (`SessionStart` ignores stdout at exit 2) and show the user an error every session.
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
            # File vanished between selection and reading: session was deleted by retention.
            continue
        with handle:
            yield from handle


def _recent_paths(transcripts: Path, cutoff: float) -> list[Path]:
    """Transcript files in mtime window; ledger is not a transcript and is excluded."""
    paths = []
    for path in transcripts.glob("*.jsonl"):
        if path.name == LEDGER_NAME:
            continue
        try:
            fresh = path.stat().st_mtime >= cutoff
        except OSError:
            # File may vanish between `glob` and `stat`; report mode would otherwise traceback.
            continue
        if fresh:
            paths.append(path)
    return sorted(paths)


def collect(
    transcripts: Path, *, days: int = DEFAULT_MTIME_DAYS
) -> tuple[list[UsageRecord], list[Anomaly], int]:
    """Read transcripts in mtime window; return records, anomalies, and file count.

    Deduplicate across all files: `resume`/`fork` copy history into a new transcript,
    so per-file parsing would double count.

    Ledger is in this directory and rewrites every session, so is always mtime-fresh. Treating
    it as transcript after retention (files removed, ledger remains) yields `files_seen=1,
    records=0`: permanent false “schema drift” in context.
    """
    cutoff = time.time() - days * 86_400
    paths = _recent_paths(transcripts, cutoff)
    records, anomalies = parse_lines(_iter_lines(paths))
    return records, anomalies, len(paths)


def transcript_dir() -> Path:
    """Claude Code transcript directory for this repository on the current machine."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(_REPO_ROOT))
    return _CLAUDE_PROJECTS / slug


def run_hook(payload: dict, transcripts: Path, ledger_path: Path) -> str:
    """Hook stdout text: empty normally, in foreign environment, and for non-startup source."""
    if payload.get("source") != "startup":
        return ""
    if not transcripts.is_dir():
        if not transcripts.parent.is_dir():
            # Foreign environment: no Claude Code transcripts here (cloud reviewer, another
            # machine). `.claude/settings.json` is in repository, so visible error would enter
            # every review-session context forever.
            return ""
        # Local machine but no repository directory: upstream changed slug rule or repository
        # moved. Silence would let the metric die forever and silently (§IV).
        return format_alert(
            Verdict("insufficient_data", 0.0, 0.0, (), ()),
            [Anomaly("transcripts_not_found", f"нет каталога {transcripts} — slug-правило уехало")],
        )
    started = time.monotonic()
    records, parse_anomalies, files_seen = collect(transcripts)
    elapsed = time.monotonic() - started
    anomalies = health_anomalies(records, parse_anomalies, files_seen=files_seen)
    if elapsed >= HOOK_TIMEOUT_SECONDS * _SLOW_COLLECT_SHARE:
        anomalies.append(
            Anomaly(
                "slow_collect",
                f"разбор занял {elapsed:.1f}s при лимите хука {HOOK_TIMEOUT_SECONDS}s — "
                "сузить окно чтения или проредить ledger",
            )
        )
    ledger, ledger_anomalies = parse_ledger(_read_ledger_lines(ledger_path))
    merged = merge_ledger(ledger, aggregate_by_branch(records))
    write_ledger(ledger_path, merged)
    return format_alert(detect_growth(merged.values()), anomalies + ledger_anomalies)


def write_ledger(ledger_path: Path, stats: dict[str, BranchStats]) -> None:
    """Write ledger atomically: truncated file loses all comparison baseline.

    `write_text` first empties file, so crash or two simultaneous sessions would leave metric
    without history—the exact failure ledger exists to prevent.
    """
    tmp = ledger_path.with_name(ledger_path.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text("\n".join(ledger_lines(stats)) + "\n", encoding="utf-8")
        os.replace(tmp, ledger_path)
    except BaseException:
        # Do not leave a tail in directory after interrupted write. It does not affect numbers
        # (not included by `*.jsonl` glob), but garbage would accumulate silently.
        tmp.unlink(missing_ok=True)
        raise


def _read_ledger_lines(ledger_path: Path) -> list[str]:
    """Ledger lines; corrupt bytes are replaced as in `_iter_lines`.

    Without `errors="replace"`, one invalid byte raises UnicodeDecodeError (ValueError, not
    OSError) past hook-mode handling. Worse, it happens **before** `write_ledger`, so file
    never rewrites and hook fails every session. Replaced byte reaches `parse_ledger` as normal anomaly.
    """
    if not ledger_path.exists():
        return []
    return ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()


def resolve_transcripts(payload: dict) -> Path:
    """Transcript directory: from SessionStart payload, otherwise slug rule.

    Claude Code supplies `transcript_path`, eliminating slug-normalization failure in hook
    mode. Slug derivation remains for payload-free `--report`, so divergence cannot be ignored
    (see `divergence_anomaly`).
    """
    raw = payload.get("transcript_path")
    if isinstance(raw, str) and raw:
        candidate = Path(raw).parent
        if candidate.is_dir():
            return candidate
    return transcript_dir()


def divergence_anomaly(resolved: Path) -> list[Anomaly]:
    """Anomaly when payload and slug rule point to different directories.

    Hook writes ledger where payload points, while `--report` follows slug rule. Silence
    would send the operator to “no transcript directory” at the metric's critical moment;
    launching from a repository subdirectory would silently split history and make each
    half look cheaper than the whole.
    """
    expected = transcript_dir()
    if resolved == expected:
        return []
    return [
        Anomaly(
            "transcripts_dir_mismatch",
            f"сессия пишет в {resolved}, а `--report` читает {expected} — история разъедется",
        )
    ]


def emit(text: str, stream: TextSink | None = None) -> None:
    """Print text in UTF-8 regardless of console code page.

    Output is Cyrillic while default Windows stdout is cp1252 (CLAUDE.md §Environment),
    so normal `print` raises UnicodeEncodeError. For hook this is not cosmetic: nonzero
    exit makes SessionStart discard stdout and user sees only “hook error.”
    """
    stream = stream if stream is not None else sys.stdout
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(text + "\n")
        return
    buffer.write((text + "\n").encode("utf-8"))
    buffer.flush()


def main() -> int:
    """CLI: `--hook` (quiet, always exit 0) and `--report` (default table mode)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hook", action="store_true", help="режим SessionStart: тихий, exit 0")
    parser.add_argument("--report", action="store_true", help="таблица по веткам (по умолчанию)")
    args = parser.parse_args()

    if args.hook:
        try:
            payload = read_payload(sys.stdin.read())
            transcripts = resolve_transcripts(payload)
            text = run_hook(payload, transcripts, transcripts / LEDGER_NAME)
            if payload.get("source") == "startup":
                mismatch = format_alert(
                    Verdict("insufficient_data", 0.0, 0.0, (), ()),
                    divergence_anomaly(transcripts),
                )
                text = "\n".join(part for part in (text, mismatch) if part)
            if text:
                emit(text)
        except Exception as exc:  # noqa: BLE001 - “hook always exits 0” contract; see below
            # Any exception, not only OSError: nonzero SessionStart discards stdout and user
            # sees bare “hook error” every session, with no chance of self-healing.
            # Use stdout, not stderr: at zero exit it enters context, while stderr displays
            # only on nonzero. Otherwise failure would be silent (§IV).
            with contextlib.suppress(Exception):
                emit(f"token-trend аномалия [{type(exc).__name__}]: {exc}")
        return 0

    transcripts = transcript_dir()
    ledger_path = transcripts / LEDGER_NAME
    if not transcripts.is_dir():
        with contextlib.suppress(OSError):
            emit(f"нет каталога транскриптов: {transcripts}", sys.stderr)
        return 1
    records, parse_anomalies, files_seen = collect(transcripts)
    anomalies = health_anomalies(records, parse_anomalies, files_seen=files_seen)
    ledger, ledger_anomalies = parse_ledger(_read_ledger_lines(ledger_path))
    merged = merge_ledger(ledger, aggregate_by_branch(records))
    report = format_report(
        merged.values(), detect_growth(merged.values()), anomalies + ledger_anomalies
    )
    try:
        emit(report)
    except OSError:
        # `--report | head` closes stdout mid-output; this is not metric failure.
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
