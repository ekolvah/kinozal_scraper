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
import re
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
    format_report,
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

    def test_sidechain_turns_excluded_from_denominator(self) -> None:
        """Субагенты — дешёвые turn'ы: в знаменателе они маскировали бы реальный рост.

        Spawn субагента — рекомендованная тактика (`mindset.md §(2)`), так что ветка с ними
        разбавляла бы `per_turn` и детектор выдавал бы `steady` на растущей плате.
        """
        records, _ = parse_lines(
            [
                _line(request_id="a", output_tokens=100),
                _line(request_id="b", sidechain=True, output_tokens=100),
            ]
        )
        stats = aggregate_by_branch(records)["issue-1-a"]
        assert stats.turns == 1
        assert stats.per_turn == pytest.approx(stats.effective)

    def test_synthetic_records_excluded_from_denominator(self) -> None:
        """Служебные записи не биллятся: turn'ом они не являются, а `per_turn` занижали бы."""
        records, _ = parse_lines(
            [
                _line(request_id="a", output_tokens=100),
                _line(request_id="b", model="<synthetic>"),
            ]
        )
        assert aggregate_by_branch(records)["issue-1-a"].turns == 1

    def test_branch_of_only_sidechain_records_has_no_zero_division(self) -> None:
        records, _ = parse_lines([_line(sidechain=True, output_tokens=10)])
        stats = aggregate_by_branch(records)["issue-1-a"]
        assert (stats.turns, stats.per_turn) == (0, 0.0)

    def test_empty_timestamp_does_not_pin_first_seen(self) -> None:
        """Пустой timestamp иначе навсегда оставлял бы ветку в начале baseline-окна."""
        records, _ = parse_lines(
            [
                _line(request_id="a", timestamp="", branch="issue-9-x"),
                _line(request_id="b", timestamp="2026-08-03T00:00:00.000Z", branch="issue-9-x"),
            ]
        )
        assert aggregate_by_branch(records)["issue-9-x"].first_seen == "2026-08-03T00:00:00.000Z"

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

    def test_empty_model_is_an_unknown_weight(self) -> None:
        """Пустая модель получает нейтральный вес — единственный неизвестный вес без сигнала."""
        records = [UsageRecord("t", "s", "b", "", False, 1, 0, 0, 0, 0)]
        assert [a.kind for a in health_anomalies(records, [], files_seen=1)] == ["unknown_model"]

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

    def test_partial_recount_does_not_shrink_history(self) -> None:
        """Ветка старше окна чтения: часть транскриптов вычищена ретенцией.

        Свежий агрегат по ней неполон, и замещение стёрло бы полную запись — ветка «худела»
        бы молча, а недосчёт читался бы как падение расхода. Долгоживущие бакеты (`main` —
        37 % turn'ов по замеру) страдали бы первыми.
        """
        ledger = {"main": _stats("main", turns=900)}
        fresh = {"main": _stats("main", turns=12)}
        assert merge_ledger(ledger, fresh)["main"].turns == 900

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

    def test_turnless_bucket_does_not_move_the_median(self) -> None:
        """Ветка из одних субагентов даёт `per_turn = 0` — в медиане это гасило бы алерт.

        И осело бы навсегда: после ретенции такой бакет уже нечем пересчитать, а из ledger'а
        он не уходит.
        """
        old = [_stats(f"issue-o{i}", per_turn=100_000, first=f"2026-07-0{i + 1}") for i in range(5)]
        new = [_stats(f"issue-n{i}", per_turn=500_000, first=f"2026-08-0{i + 1}") for i in range(5)]
        turnless = BranchStats(
            branch="issue-subagents-only",
            turns=0,
            first_seen="2026-08-03",
            last_seen="2026-08-03",
            effective=100_000.0,
            input_tokens=0,
            output_tokens=0,
            cache_read=0,
            cache_write=0,
            sidechain_effective=100_000.0,
        )
        assert detect_growth([*old, *new, turnless]) == detect_growth([*old, *new])

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

    def test_absent_projects_root_is_silent_noop(self, tmp_path: Path) -> None:
        """Чужая среда (cloud-ревьюер, другая машина) не шумит в контекст каждой сессии."""
        missing = tmp_path / "no-claude" / "projects" / "slug"
        assert run_hook({"source": "startup"}, missing, tmp_path / "ledger.jsonl") == ""

    def test_missing_slug_dir_surfaces_error(self, tmp_path: Path) -> None:
        """Своя машина, но каталога репо нет: slug-правило уехало — метрика умерла бы молча."""
        projects = tmp_path / "projects"
        projects.mkdir()
        assert run_hook({"source": "startup"}, projects / "slug", tmp_path / "ledger.jsonl") != ""

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

    def test_ledger_in_transcript_dir_not_read_as_transcript(self, tmp_path: Path) -> None:
        """Ledger живёт рядом с транскриптами и переписывается каждую сессию.

        Если бы `collect` глобил и его, то после ретенции (транскрипты вычищены, ledger
        остался) выходило бы `files_seen=1, records=0` — вечная ложная «schema drift»
        в контексте каждой сессии.
        """
        transcripts = tmp_path / "projects"
        transcripts.mkdir()
        (transcripts / token_trend.LEDGER_NAME).write_text(
            "\n".join(ledger_lines({"issue-1-a": _stats("issue-1-a")})), encoding="utf-8"
        )
        records, _, files_seen = collect(transcripts)
        assert (files_seen, records) == (0, [])

    def test_ledger_beside_transcripts_does_not_trigger_drift_alert(self, tmp_path: Path) -> None:
        transcripts = tmp_path / "projects"
        transcripts.mkdir()
        ledger = transcripts / token_trend.LEDGER_NAME
        (transcripts / "a.jsonl").write_text(_line(), encoding="utf-8")
        run_hook({"source": "startup"}, transcripts, ledger)
        assert "no_usage_records" not in run_hook({"source": "startup"}, transcripts, ledger)

    def test_slow_collect_surfaces_anomaly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Хук убивается по `timeout` без следа, и `write_ledger` (он после парсинга) не успеет.

        Метрика встала бы навсегда и молча, поэтому приближение к лимиту — видимая аномалия.
        """
        transcripts = self._dir_with(tmp_path, [_line()])
        monkeypatch.setattr(token_trend, "HOOK_TIMEOUT_SECONDS", 0)
        assert "slow_collect" in run_hook(
            {"source": "startup"}, transcripts, tmp_path / "ledger.jsonl"
        )

    def test_vanished_file_does_not_crash_collect(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Файл может исчезнуть между `glob` и `stat` — в report-режиме это был бы traceback."""
        transcripts = self._dir_with(tmp_path, [_line()])
        original = Path.stat

        def flaky(self: Path, *args: object, **kwargs: object) -> object:
            if self.name == "a.jsonl":
                raise FileNotFoundError(self)
            return original(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "stat", flaky)
        records, _, files_seen = collect(transcripts)
        assert (files_seen, records) == (0, [])

    def test_ledger_survives_repeated_writes(self, tmp_path: Path) -> None:
        """Запись ledger'а атомарна: усечённый файл — потеря всей базы сравнения."""
        transcripts = self._dir_with(tmp_path, [_line()])
        ledger = tmp_path / "ledger.jsonl"
        run_hook({"source": "startup"}, transcripts, ledger)
        run_hook({"source": "startup"}, transcripts, ledger)
        restored, anomalies = parse_ledger(ledger.read_text(encoding="utf-8").splitlines())
        assert anomalies == []
        assert "issue-1-a" in restored
        assert not list(ledger.parent.glob("*.tmp"))


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


class TestLedgerLocation:
    """Hook и report должны смотреть в один каталог, иначе история делится надвое."""

    @staticmethod
    def _run_hook_main(transcripts: Path, monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
        monkeypatch.setattr("sys.argv", ["token_trend.py", "--hook"])
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        assert token_trend.main() == 0

    def test_hook_writes_ledger_beside_payload_transcripts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проводка `resolve_transcripts` в `main`: ledger ложится к тем же транскриптам."""
        transcripts = tmp_path / "projects" / "slug"
        transcripts.mkdir(parents=True)
        (transcripts / "a.jsonl").write_text(_line(), encoding="utf-8")
        monkeypatch.setattr(token_trend, "transcript_dir", lambda: transcripts)
        self._run_hook_main(
            transcripts,
            monkeypatch,
            {"source": "startup", "transcript_path": str(transcripts / "a.jsonl")},
        )
        assert (transcripts / token_trend.LEDGER_NAME).exists()

    def test_diverging_dirs_surface_anomaly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Каталог из payload разошёлся со slug-правилом — оператора нельзя вести в пустоту.

        `--report` (единственная инструкция, которую печатает алерт) читает по slug-правилу:
        промолчав, мы отправили бы его в `нет каталога транскриптов` с кодом 1.
        """
        real = tmp_path / "projects" / "real"
        real.mkdir(parents=True)
        (real / "a.jsonl").write_text(_line(), encoding="utf-8")
        stale = tmp_path / "projects" / "stale-slug"
        stale.mkdir(parents=True)
        monkeypatch.setattr(token_trend, "transcript_dir", lambda: stale)
        self._run_hook_main(
            real, monkeypatch, {"source": "startup", "transcript_path": str(real / "a.jsonl")}
        )
        out = capsys.readouterr().out
        assert "transcripts_dir_mismatch" in out
        assert str(real) in out


class TestResolveTranscripts:
    """`SessionStart` приносит `transcript_path` — самый частый путь не должен зависеть от slug'а."""

    def test_payload_path_wins_over_slug_rule(self, tmp_path: Path) -> None:
        transcripts = tmp_path / "projects" / "slug"
        transcripts.mkdir(parents=True)
        payload = {"transcript_path": str(transcripts / "session.jsonl")}
        assert token_trend.resolve_transcripts(payload) == transcripts

    def test_falls_back_to_slug_when_payload_is_silent(self) -> None:
        assert token_trend.resolve_transcripts({}) == token_trend.transcript_dir()

    def test_falls_back_when_payload_path_does_not_exist(self, tmp_path: Path) -> None:
        payload = {"transcript_path": str(tmp_path / "gone" / "session.jsonl")}
        assert token_trend.resolve_transcripts(payload) == token_trend.transcript_dir()


class TestHookRegistration:
    """Гард против мёртвого кода: метрика без автотриггера повторит судьбу eval'а (#361)."""

    @staticmethod
    def _session_start_hooks() -> list[dict]:
        settings = json.loads(
            (Path(__file__).resolve().parent.parent / ".claude" / "settings.json").read_text(
                encoding="utf-8"
            )
        )
        return [
            hook
            for group in settings.get("hooks", {}).get("SessionStart", [])
            for hook in group.get("hooks", [])
        ]

    @classmethod
    def _session_start_commands(cls) -> list[str]:
        return [hook.get("command", "") for hook in cls._session_start_hooks()]

    def test_hook_declares_timeout(self) -> None:
        """Разбор окна транскриптов — плата wall-clock в каждой сессии: ограничена явно."""
        ours = [h for h in self._session_start_hooks() if "token_trend.py" in h.get("command", "")]
        assert ours and all(isinstance(hook.get("timeout"), int) for hook in ours)

    def test_declared_timeout_matches_the_code(self) -> None:
        """Скрипт предупреждает о приближении к лимиту — значит должен знать его величину."""
        ours = [h for h in self._session_start_hooks() if "token_trend.py" in h.get("command", "")]
        assert all(h["timeout"] == token_trend.HOOK_TIMEOUT_SECONDS for h in ours)

    def test_session_start_hook_registered(self) -> None:
        assert any("token_trend.py" in command for command in self._session_start_commands())

    def test_registered_in_hook_mode(self) -> None:
        """Без `--hook` скрипт печатал бы таблицу в контекст каждой сессии."""
        commands = [c for c in self._session_start_commands() if "token_trend.py" in c]
        assert all("--hook" in command for command in commands)


def _alerting_lines() -> list[str]:
    """Набор веток, на котором детектор обязан сказать `grown`."""
    lines = []
    for i in range(5):
        lines.append(
            _line(
                request_id=f"o{i}",
                branch=f"issue-o{i}",
                timestamp=f"2026-07-0{i + 1}T00:00:00.000Z",
                cache_read=1_000,
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
    return lines


class TestFormatReport:
    """Главный человекочитаемый выход метрики — под тестом, а не «тонкий I/O»."""

    def test_lists_branches_and_verdict(self) -> None:
        stats = [_stats("issue-1-a", turns=4, per_turn=25_000)]
        text = format_report(stats, detect_growth(stats), [])
        assert "issue-1-a" in text
        assert "вердикт" in text

    def test_shows_sidechain_share(self) -> None:
        """Субагенты — отдельная строка расхода, иначе непонятно, что чинить."""
        stats = [
            BranchStats(
                branch="issue-1-a",
                turns=4,
                first_seen="2026-08-01",
                last_seen="2026-08-02",
                effective=100_000.0,
                input_tokens=1,
                output_tokens=1,
                cache_read=1,
                cache_write=1,
                sidechain_effective=40_000.0,
            )
        ]
        assert "sidechain" in format_report(stats, detect_growth(stats), []).lower()

    def test_turnless_branch_shows_dash_not_zero(self) -> None:
        """`per-turn 0.0k` рядом с непустым `effective` читается как «ветка бесплатная»."""
        stats = [
            BranchStats(
                branch="issue-subagents-only",
                turns=0,
                first_seen="2026-08-01",
                last_seen="2026-08-01",
                effective=100_000.0,
                input_tokens=0,
                output_tokens=0,
                cache_read=0,
                cache_write=0,
                sidechain_effective=100_000.0,
            )
        ]
        lines = format_report(stats, detect_growth(stats), []).splitlines()
        header = lines[0].split()
        row = next(line for line in lines if "issue-subagents-only" in line).split()
        assert row[header.index("per-turn")] == "—"

    def test_anomalies_reach_the_report(self) -> None:
        text = format_report([], detect_growth([]), [Anomaly("no_usage_records", "0 файлов")])
        assert "no_usage_records" in text


class TestReportMode:
    """`--report` обещан оператору в алерте: неизвестный флаг отправил бы его в тупик."""

    def test_report_flag_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        transcripts = tmp_path / "projects"
        transcripts.mkdir()
        (transcripts / "a.jsonl").write_text(_line(), encoding="utf-8")
        monkeypatch.setattr(token_trend, "transcript_dir", lambda: transcripts)
        monkeypatch.setattr("sys.argv", ["token_trend.py", "--report"])
        assert token_trend.main() == 0
        assert "issue-1-a" in capsys.readouterr().out

    def test_alert_names_a_flag_the_cli_accepts(self) -> None:
        """Гард против расхождения текста алерта и реального интерфейса.

        В момент, ради которого метрика заведена, алерт не должен отправлять оператора
        в `unrecognized arguments`.
        """
        old = [_stats(f"issue-o{i}", per_turn=1_000, first=f"2026-07-0{i + 1}") for i in range(5)]
        new = [_stats(f"issue-n{i}", per_turn=500_000, first=f"2026-08-0{i + 1}") for i in range(5)]
        verdict = detect_growth(old + new)
        assert verdict.status == "grown"
        text = format_report(old + new, verdict, []) + "\n" + format_alert(verdict, [])
        flags = set(re.findall(r"--[a-z-]+", text))
        assert flags
        assert flags <= set(token_trend.CLI_FLAGS)


class TestExitCodes:
    def test_hook_exits_zero_even_when_alerting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`SessionStart` отдаёт stdout в контекст только на exit 0 — ненулевой код съел бы алерт."""
        transcripts = tmp_path / "projects"
        transcripts.mkdir()
        (transcripts / "a.jsonl").write_text("\n".join(_alerting_lines()), encoding="utf-8")
        monkeypatch.setattr(token_trend, "transcript_dir", lambda: transcripts)
        monkeypatch.setattr("sys.argv", ["token_trend.py", "--hook"])
        monkeypatch.setattr("sys.stdin", io.StringIO('{"source": "startup"}'))
        assert token_trend.main() == 0
        assert "вырос" in capsys.readouterr().out

    def test_hook_exits_zero_on_corrupt_ledger(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Невалидный байт в ledger'е — `UnicodeDecodeError`, а это не `OSError`.

        Падение происходит до `write_ledger`, поэтому файл никогда не перезапишется: хук
        выдавал бы «hook error» каждую сессию, и метрика не могла бы самовылечиться.
        """
        transcripts = tmp_path / "projects"
        transcripts.mkdir()
        (transcripts / "a.jsonl").write_text(_line(), encoding="utf-8")
        (transcripts / token_trend.LEDGER_NAME).write_bytes(b"\xff\xfe not utf-8\n")
        monkeypatch.setattr(token_trend, "transcript_dir", lambda: transcripts)
        monkeypatch.setattr("sys.argv", ["token_trend.py", "--hook"])
        monkeypatch.setattr("sys.stdin", io.StringIO('{"source": "startup"}'))
        assert token_trend.main() == 0

    def test_ledger_with_wrong_field_types_is_an_anomaly_and_gets_repaired(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`"turns": "12"` проходил конструктор и ронял `merge_ledger` — до `write_ledger`.

        Значит файл не переписывался никогда: метрика умирала навсегда. Битая запись должна
        быть штатной аномалией, тогда ledger лечится сам на следующем же старте.
        """
        transcripts = tmp_path / "projects"
        transcripts.mkdir()
        (transcripts / "a.jsonl").write_text(_line(), encoding="utf-8")
        broken = {
            "schema": token_trend.LEDGER_SCHEMA,
            "branch": "issue-1-a",
            "turns": "12",
            "first_seen": "2026-08-01",
            "last_seen": "2026-08-01",
            "effective": 1.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read": 0,
            "cache_write": 0,
            "sidechain_effective": 0.0,
        }
        ledger = transcripts / token_trend.LEDGER_NAME
        ledger.write_text(json.dumps(broken), encoding="utf-8")
        monkeypatch.setattr(token_trend, "transcript_dir", lambda: transcripts)
        monkeypatch.setattr("sys.argv", ["token_trend.py", "--hook"])
        monkeypatch.setattr("sys.stdin", io.StringIO('{"source": "startup"}'))

        assert token_trend.main() == 0
        assert "ledger_schema" in capsys.readouterr().out
        restored, anomalies = parse_ledger(ledger.read_text(encoding="utf-8").splitlines())
        assert anomalies == []
        assert restored["issue-1-a"].turns == 1

    def test_parse_ledger_rejects_wrong_field_types(self) -> None:
        line = json.dumps({"schema": LEDGER_SCHEMA, "branch": "b", "turns": "12"})
        stats, anomalies = parse_ledger([line])
        assert stats == {}
        assert [a.kind for a in anomalies] == ["ledger_schema"]

    def test_unexpected_failure_is_reported_to_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """На exit 0 канал в контекст — stdout; stderr пользователь увидит только на ошибке."""
        transcripts = tmp_path / "projects"
        transcripts.mkdir()
        (transcripts / "a.jsonl").write_text(_line(), encoding="utf-8")
        monkeypatch.setattr(token_trend, "transcript_dir", lambda: transcripts)

        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("детектор сломался")

        monkeypatch.setattr(token_trend, "detect_growth", boom)
        monkeypatch.setattr("sys.argv", ["token_trend.py", "--hook"])
        monkeypatch.setattr("sys.stdin", io.StringIO('{"source": "startup"}'))
        assert token_trend.main() == 0
        assert "детектор сломался" in capsys.readouterr().out

    def test_hook_exits_zero_when_stdout_is_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Закрытый stdout — тот же режим отказа, что описан в докстринге `emit`."""
        transcripts = tmp_path / "projects"
        transcripts.mkdir()
        (transcripts / "a.jsonl").write_text("\n".join(_alerting_lines()), encoding="utf-8")
        monkeypatch.setattr(token_trend, "transcript_dir", lambda: transcripts)
        monkeypatch.setattr("sys.argv", ["token_trend.py", "--hook"])
        monkeypatch.setattr("sys.stdin", io.StringIO('{"source": "startup"}'))

        def exploding(_text: str, _stream: object = None) -> None:
            raise OSError("stdout closed")

        monkeypatch.setattr(token_trend, "emit", exploding)
        assert token_trend.main() == 0
