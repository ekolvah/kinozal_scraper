"""Unit-тесты фактического расхода токенов dev-сессий (`scripts/token_trend.py`, #464).

Дефект, который чинится: приоритет (2) цель-функции не измерялся ничем, и «кажется, задачи
стали дороже» нельзя было ни подтвердить, ни привязать к коммиту.

Уровень выбран по bug-taxonomy: вся детерминированная логика — чистые функции над строками
JSONL, поэтому тесты работают на inline-фикстурах и **не** читают реальный `~/.claude`.
I/O остаётся тонкой обёрткой (`collect`, `run_hook`), как в `scripts/hooks.py`.

§IV-заметка: у метрики два разных «нет данных». Отсутствие каталога транскриптов в чужой среде
(cloud-ревьюер, другая машина) — штатный тихий no-op: иначе текст ошибки попадал бы в контекст
каждой ревью-сессии навсегда. А вот каталог со своими файлами, из которых не разобралось ни
одной usage-записи, — schema drift апстрима, то есть видимая аномалия: без неё метрика умирает
молча при зелёных тестах.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from scripts import token_trend
from scripts.token_trend import (
    LEDGER_SCHEMA,
    MAIN_BUCKET,
    NO_BRANCH_BUCKET,
    Anomaly,
    BranchStats,
    UsageRecord,
    aggregate_by_branch,
    collect,
    detect_growth,
    effective_tokens,
    format_alert,
    health_anomalies,
    issue_branches,
    ledger_lines,
    merge_ledger,
    model_scale,
    parse_ledger,
    parse_lines,
    read_payload,
    run_hook,
)


def _entry(
    *,
    request_id: str | None = "req-1",
    branch: str | None = "issue-1-a",
    model: str = "claude-opus-5",
    sidechain: bool = False,
    timestamp: str = "2026-08-01T10:00:00.000Z",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_5m: int = 0,
    cache_1h: int = 0,
    uuid: str = "u-1",
) -> dict:
    """Одна `assistant`-строка транскрипта в форме, которую пишет Claude Code."""
    entry = {
        "type": "assistant",
        "timestamp": timestamp,
        "sessionId": "s-1",
        "isSidechain": sidechain,
        "uuid": uuid,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_5m + cache_1h,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": cache_5m,
                    "ephemeral_1h_input_tokens": cache_1h,
                },
            },
        },
    }
    if request_id is not None:
        entry["requestId"] = request_id
    if branch is not None:
        entry["gitBranch"] = branch
    return entry


def _line(**kwargs: object) -> str:
    return json.dumps(_entry(**kwargs))  # type: ignore[arg-type]


def _stats(
    branch: str, *, turns: int = 10, per_turn: float = 1000.0, first: str = "2026-08-01"
) -> BranchStats:
    """Агрегат с заданной ценой turn'а — для тестов детектора."""
    return BranchStats(
        branch=branch,
        turns=turns,
        first_seen=first,
        last_seen=first,
        effective=per_turn * turns,
        input_tokens=0,
        output_tokens=0,
        cache_read=0,
        cache_write=0,
        sidechain_effective=0.0,
    )


class TestParse:
    def test_assistant_usage_extracted(self) -> None:
        records, anomalies = parse_lines([_line(input_tokens=7, output_tokens=3, cache_read=100)])
        assert anomalies == []
        assert len(records) == 1
        assert (records[0].input_tokens, records[0].output_tokens, records[0].cache_read) == (
            7,
            3,
            100,
        )
        assert records[0].branch == "issue-1-a"
        assert records[0].model == "claude-opus-5"

    def test_duplicate_request_id_counted_once(self) -> None:
        records, _ = parse_lines(
            [_line(request_id="req-x", output_tokens=5), _line(request_id="req-x", output_tokens=5)]
        )
        assert len(records) == 1

    def test_records_without_request_id_not_collapsed(self) -> None:
        """8 таких записей есть в реальных данных: дедуп по `None` схлопнул бы их в одну."""
        records, _ = parse_lines(
            [
                _line(request_id=None, uuid="u-1", output_tokens=5),
                _line(request_id=None, uuid="u-2", output_tokens=5),
            ]
        )
        assert len(records) == 2

    def test_sidechain_records_flagged_separately(self) -> None:
        records, _ = parse_lines(
            [_line(request_id="a", sidechain=False), _line(request_id="b", sidechain=True)]
        )
        assert [r.is_sidechain for r in records] == [False, True]

    def test_malformed_line_surfaces_as_anomaly(self) -> None:
        records, anomalies = parse_lines(["{not json", _line()])
        assert len(records) == 1
        assert [a.kind for a in anomalies] == ["malformed_line"]

    def test_non_assistant_lines_are_not_anomalies(self) -> None:
        """Штатные типы строк молчат — граница предыдущего теста."""
        _, anomalies = parse_lines([json.dumps({"type": "user", "message": {}}), _line()])
        assert anomalies == []

    def test_renamed_usage_field_yields_zero_records(self) -> None:
        """Schema drift апстрима виден как отсутствие записей, а не как «роста нет»."""
        drifted = {"type": "assistant", "message": {"model": "m", "token_usage": {}}}
        records, _ = parse_lines([json.dumps(drifted)])
        assert records == []


class TestEffectiveCost:
    def test_cache_read_weighted_below_fresh_input(self) -> None:
        fresh = UsageRecord("t", "s", "b", "claude-opus-5", False, 1000, 0, 0, 0, 0)
        cached = UsageRecord("t", "s", "b", "claude-opus-5", False, 0, 0, 1000, 0, 0)
        assert effective_tokens(cached) == pytest.approx(effective_tokens(fresh) * 0.1)

    def test_one_hour_cache_write_costlier_than_five_minute(self) -> None:
        """В реальных данных доминирует `ephemeral_1h` — вес 2.0 против 1.25."""
        write_5m = UsageRecord("t", "s", "b", "claude-opus-5", False, 0, 0, 0, 1000, 0)
        write_1h = UsageRecord("t", "s", "b", "claude-opus-5", False, 0, 0, 0, 0, 1000)
        assert effective_tokens(write_1h) > effective_tokens(write_5m)

    def test_weights_selected_per_model(self) -> None:
        """Переезд на другую модель двигает сводную величину, а не прячется в ней."""
        assert model_scale("claude-sonnet-5") < model_scale("claude-opus-5")
        assert model_scale("claude-haiku-4-5") < model_scale("claude-sonnet-5")
        opus = UsageRecord("t", "s", "b", "claude-opus-5", False, 1000, 0, 0, 0, 0)
        haiku = UsageRecord("t", "s", "b", "claude-haiku-4-5", False, 1000, 0, 0, 0, 0)
        assert effective_tokens(haiku) < effective_tokens(opus)

    def test_unknown_model_scale_is_neutral(self) -> None:
        assert model_scale("some-future-model") == 1.0


class TestAggregate:
    def test_branch_totals_and_per_turn(self) -> None:
        records, _ = parse_lines(
            [
                _line(request_id="a", branch="issue-9-x", output_tokens=100),
                _line(request_id="b", branch="issue-9-x", output_tokens=100),
            ]
        )
        stats = aggregate_by_branch(records)["issue-9-x"]
        assert stats.turns == 2
        assert stats.output_tokens == 200
        assert stats.per_turn == pytest.approx(stats.effective / 2)

    def test_main_branch_bucketed_apart_from_issue_branches(self) -> None:
        """`main` — 37% turn'ов и разнороден: планирование, ревью, чтение."""
        records, _ = parse_lines(
            [_line(request_id="a", branch="main"), _line(request_id="b", branch="issue-9-x")]
        )
        stats = aggregate_by_branch(records)
        assert MAIN_BUCKET in stats
        assert [s.branch for s in issue_branches(stats.values())] == ["issue-9-x"]

    def test_records_without_branch_bucketed_not_dropped(self) -> None:
        records, _ = parse_lines([_line(branch=None)])
        stats = aggregate_by_branch(records)
        assert stats[NO_BRANCH_BUCKET].turns == 1
        assert issue_branches(stats.values()) == []

    def test_branches_ordered_by_first_timestamp(self) -> None:
        records, _ = parse_lines(
            [
                _line(request_id="a", branch="issue-2-b", timestamp="2026-08-02T00:00:00.000Z"),
                _line(request_id="b", branch="issue-1-a", timestamp="2026-08-01T00:00:00.000Z"),
            ]
        )
        ordered = issue_branches(aggregate_by_branch(records).values())
        assert [s.branch for s in ordered] == ["issue-1-a", "issue-2-b"]

    def test_sidechain_kept_as_separate_line(self) -> None:
        records, _ = parse_lines(
            [
                _line(request_id="a", output_tokens=10),
                _line(request_id="b", sidechain=True, output_tokens=10),
            ]
        )
        stats = aggregate_by_branch(records)["issue-1-a"]
        assert stats.sidechain_effective > 0
        assert stats.sidechain_effective < stats.effective


class TestHealth:
    def test_files_present_but_no_records_is_anomaly(self) -> None:
        anomalies = health_anomalies([], [], files_seen=5)
        assert [a.kind for a in anomalies] == ["no_usage_records"]

    def test_no_files_is_not_an_anomaly(self) -> None:
        assert health_anomalies([], [], files_seen=0) == []

    def test_high_share_of_broken_lines_is_anomaly(self) -> None:
        records = [UsageRecord("t", "s", "b", "m", False, 1, 0, 0, 0, 0)]
        broken = [Anomaly("malformed_line", str(i)) for i in range(10)]
        kinds = [a.kind for a in health_anomalies(records, broken, files_seen=1)]
        assert "high_anomaly_rate" in kinds


class TestLedger:
    def test_aggregate_round_trips(self) -> None:
        fresh = {"issue-1-a": _stats("issue-1-a")}
        restored, anomalies = parse_ledger(ledger_lines(fresh))
        assert anomalies == []
        assert restored == fresh

    def test_existing_branch_not_double_counted(self) -> None:
        """Свежий агрегат замещает запись ledger'а, а не суммируется с ней."""
        ledger = {"issue-1-a": _stats("issue-1-a", turns=5)}
        fresh = {"issue-1-a": _stats("issue-1-a", turns=9)}
        assert merge_ledger(ledger, fresh)["issue-1-a"].turns == 9

    def test_history_outlived_by_ledger_when_transcript_gone(self) -> None:
        """Ветка, стёртая ретенцией транскриптов, остаётся в базе сравнения."""
        ledger = {"issue-old": _stats("issue-old", first="2026-06-01")}
        fresh = {"issue-new": _stats("issue-new", first="2026-08-01")}
        assert set(merge_ledger(ledger, fresh)) == {"issue-old", "issue-new"}

    def test_malformed_ledger_line_surfaces_as_anomaly(self) -> None:
        _, anomalies = parse_ledger(["{broken"])
        assert [a.kind for a in anomalies] == ["malformed_line"]

    def test_unknown_schema_version_surfaces_as_anomaly(self) -> None:
        line = json.dumps({"schema": LEDGER_SCHEMA + 1, "branch": "issue-1-a"})
        _, anomalies = parse_ledger([line])
        assert anomalies != []


class TestDetect:
    def test_growth_above_threshold_reports_verdict(self) -> None:
        old = [_stats(f"issue-o{i}", per_turn=1000, first=f"2026-07-0{i + 1}") for i in range(5)]
        new = [_stats(f"issue-n{i}", per_turn=100_000, first=f"2026-08-0{i + 1}") for i in range(5)]
        assert detect_growth(old + new).status == "grown"

    def test_growth_below_threshold_is_silent(self) -> None:
        old = [_stats(f"issue-o{i}", per_turn=100_000, first=f"2026-07-0{i + 1}") for i in range(5)]
        new = [_stats(f"issue-n{i}", per_turn=105_000, first=f"2026-08-0{i + 1}") for i in range(5)]
        assert detect_growth(old + new).status == "steady"

    def test_single_outlier_branch_does_not_trigger(self) -> None:
        """Медиана, а не среднее: одна длинная рефактор-сессия не двигает вердикт."""
        old = [_stats(f"issue-o{i}", per_turn=100_000, first=f"2026-07-0{i + 1}") for i in range(5)]
        new = [_stats(f"issue-n{i}", per_turn=100_000, first=f"2026-08-0{i + 1}") for i in range(4)]
        new.append(_stats("issue-spike", per_turn=5_000_000, first="2026-08-05"))
        assert detect_growth(old + new).status == "steady"

    def test_relative_growth_below_absolute_floor_is_silent(self) -> None:
        """Удвоение копеечных веток — не деградация, а шум на малой выборке."""
        old = [_stats(f"issue-o{i}", per_turn=100, first=f"2026-07-0{i + 1}") for i in range(5)]
        new = [_stats(f"issue-n{i}", per_turn=1000, first=f"2026-08-0{i + 1}") for i in range(5)]
        assert detect_growth(old + new).status == "steady"

    def test_insufficient_window_is_not_growth(self) -> None:
        few = [_stats(f"issue-o{i}", first=f"2026-08-0{i + 1}") for i in range(3)]
        assert detect_growth(few).status == "insufficient_data"

    def test_main_bucket_excluded_from_windows(self) -> None:
        main = _stats(MAIN_BUCKET, per_turn=5_000_000, first="2026-08-09")
        few = [_stats(f"issue-o{i}", first=f"2026-08-0{i + 1}") for i in range(3)]
        assert detect_growth([*few, main]).status == "insufficient_data"


class TestFormatAlert:
    def test_steady_verdict_prints_nothing(self) -> None:
        verdict = detect_growth(
            [_stats(f"issue-o{i}", first=f"2026-08-0{i + 1}") for i in range(3)]
        )
        assert format_alert(verdict, []) == ""

    def test_anomaly_printed_even_on_steady_verdict(self) -> None:
        verdict = detect_growth([])
        text = format_alert(verdict, [Anomaly("no_usage_records", "0 из 22 файлов")])
        assert "no_usage_records" in text


class TestReadPayload:
    @pytest.mark.parametrize("raw", ["", "   ", "{broken", "[]", "null"])
    def test_broken_stdin_yields_empty_dict(self, raw: str) -> None:
        assert read_payload(raw) == {}

    def test_valid_payload_parsed(self) -> None:
        assert read_payload('{"source": "startup"}') == {"source": "startup"}


class TestHookMode:
    @staticmethod
    def _dir_with(tmp_path: Path, lines: list[str]) -> Path:
        transcripts = tmp_path / "projects"
        transcripts.mkdir()
        (transcripts / "a.jsonl").write_text("\n".join(lines), encoding="utf-8")
        return transcripts

    def test_silent_when_within_threshold(self, tmp_path: Path) -> None:
        transcripts = self._dir_with(tmp_path, [_line()])
        assert run_hook({"source": "startup"}, transcripts, tmp_path / "ledger.jsonl") == ""

    def test_alert_printed_when_exceeded(self, tmp_path: Path) -> None:
        lines = []
        for i in range(5):
            lines.append(
                _line(
                    request_id=f"o{i}",
                    branch=f"issue-o{i}",
                    timestamp=f"2026-07-0{i + 1}T00:00:00.000Z",
                    cache_read=1000,
                )
            )
        for i in range(5):
            lines.append(
                _line(
                    request_id=f"n{i}",
                    branch=f"issue-n{i}",
                    timestamp=f"2026-08-0{i + 1}T00:00:00.000Z",
                    cache_read=100_000_000,
                )
            )
        transcripts = self._dir_with(tmp_path, lines)
        assert run_hook({"source": "startup"}, transcripts, tmp_path / "ledger.jsonl") != ""

    def test_absent_transcript_dir_is_silent_noop(self, tmp_path: Path) -> None:
        """Чужая среда (cloud-ревьюер, другая машина) не шумит в контекст каждой сессии."""
        missing = tmp_path / "nope"
        assert run_hook({"source": "startup"}, missing, tmp_path / "ledger.jsonl") == ""

    def test_empty_dir_surfaces_error(self, tmp_path: Path) -> None:
        """Своя среда, но записей нет — метрика сломалась, и это должно быть видно."""
        transcripts = self._dir_with(tmp_path, [json.dumps({"type": "user"})])
        assert run_hook({"source": "startup"}, transcripts, tmp_path / "ledger.jsonl") != ""

    @pytest.mark.parametrize("source", ["resume", "compact", "fork", None])
    def test_non_startup_source_is_silent(self, tmp_path: Path, source: str | None) -> None:
        """`compact`/`resume` не повторяют алерт внутри одной задачи."""
        transcripts = self._dir_with(tmp_path, [json.dumps({"type": "user"})])
        payload = {} if source is None else {"source": source}
        assert run_hook(payload, transcripts, tmp_path / "ledger.jsonl") == ""

    def test_malformed_stdin_does_not_break_hook(self, tmp_path: Path) -> None:
        transcripts = self._dir_with(tmp_path, [_line()])
        assert run_hook(read_payload("{broken"), transcripts, tmp_path / "ledger.jsonl") == ""

    def test_ledger_written_on_startup(self, tmp_path: Path) -> None:
        transcripts = self._dir_with(tmp_path, [_line()])
        ledger = tmp_path / "ledger.jsonl"
        run_hook({"source": "startup"}, transcripts, ledger)
        assert ledger.exists()

    def test_collect_skips_files_outside_mtime_window(self, tmp_path: Path) -> None:
        """Стоимость самого хука не растёт с историей: старые файлы не парсятся."""
        transcripts = self._dir_with(tmp_path, [_line()])
        stale = transcripts / "old.jsonl"
        stale.write_text(_line(request_id="old", branch="issue-old"), encoding="utf-8")
        os.utime(stale, (0, 0))
        records, _, files_seen = collect(transcripts, days=45)
        assert files_seen == 1
        assert {r.branch for r in records} == {"issue-1-a"}


class TestEmit:
    """Вывод кириллицы не должен зависеть от кодовой страницы консоли (`CLAUDE.md` §Среда)."""

    class _Cp1252Stream:
        """stdout, который роняет `print` на кириллице — ровно поведение Windows-консоли."""

        def __init__(self) -> None:
            self.buffer = io.BytesIO()

        def write(self, text: str) -> int:
            return self.buffer.write(text.encode("cp1252"))

    def test_cyrillic_survives_cp1252_console(self) -> None:
        stream = self._Cp1252Stream()
        token_trend.emit("расход вырос", stream)
        assert "расход вырос" in stream.buffer.getvalue().decode("utf-8")

    def test_stream_without_buffer_still_works(self) -> None:
        stream = io.StringIO()
        token_trend.emit("ok", stream)
        assert stream.getvalue() == "ok\n"


class TestHookRegistration:
    """Гард против мёртвого кода: метрика без автотриггера повторит судьбу eval'а (#361)."""

    @staticmethod
    def _session_start_commands() -> list[str]:
        settings = json.loads(
            (Path(__file__).resolve().parent.parent / ".claude" / "settings.json").read_text(
                encoding="utf-8"
            )
        )
        return [
            hook.get("command", "")
            for group in settings.get("hooks", {}).get("SessionStart", [])
            for hook in group.get("hooks", [])
        ]

    def test_session_start_hook_registered(self) -> None:
        assert any("token_trend.py" in command for command in self._session_start_commands())

    def test_registered_in_hook_mode(self) -> None:
        """Без `--hook` скрипт печатал бы таблицу в контекст каждой сессии."""
        commands = [c for c in self._session_start_commands() if "token_trend.py" in c]
        assert all("--hook" in command for command in commands)


class TestExitCodes:
    def test_hook_exits_zero_even_when_alerting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`SessionStart` отдаёт stdout в контекст только на exit 0 — ненулевой код съел бы алерт."""
        monkeypatch.setattr(token_trend, "transcript_dir", lambda: tmp_path / "missing")
        monkeypatch.setattr("sys.argv", ["token_trend.py", "--hook"])
        monkeypatch.setattr("sys.stdin", io.StringIO('{"source": "startup"}'))
        assert token_trend.main() == 0
