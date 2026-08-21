"""Unit tests for actual token consumption of development sessions (`scripts/token_trend.py`, #464).

Defect addressed: goal-function priority (2) was not measured, and “tasks seem to have become
more expensive” could neither be confirmed nor tied to a commit.

The level follows the bug taxonomy: all deterministic logic is pure functions over JSONL lines,
so tests use inline fixtures and do **not** read the real `~/.claude`. I/O remains a thin wrapper
(`collect`, `run_hook`), as in `scripts/hooks.py`.

§IV note: the metric has two distinct “no data” states. No transcript directory in a foreign environment
(cloud reviewer, other machine) is a normal silent no-op, otherwise error text would enter every review-session
context forever. A directory with its own files but no parsed usage record is upstream schema drift—a visible
anomaly; without it, the metric dies silently while tests are green.
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
    format_alert,
    format_report,
    health_anomalies,
    issue_branches,
    ledger_lines,
    merge_ledger,
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
    """One `assistant` transcript line in the form written by Claude Code."""
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
    """Aggregate with a specified per-turn cost—for detector tests."""
    return BranchStats(
        branch=branch,
        turns=turns,
        first_seen=first,
        last_seen=first,
        input_tokens=int(per_turn * turns),
        output_tokens=0,
        cache_read=0,
        cache_write=0,
        sidechain_tokens=0,
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
        """Real data has 8 such records: deduplication by `None` would collapse them to one."""
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
        """Normal line types remain silent—the boundary of the previous test."""
        _, anomalies = parse_lines([json.dumps({"type": "user", "message": {}}), _line()])
        assert anomalies == []

    def test_renamed_usage_field_yields_zero_records(self) -> None:
        """Upstream schema drift is visible as missing records, not as “no growth.”"""
        drifted = {"type": "assistant", "message": {"model": "m", "token_usage": {}}}
        records, _ = parse_lines([json.dumps(drifted)])
        assert records == []


class TestRawTokenAccounting:
    def test_total_is_the_sum_of_observed_counts(self) -> None:
        """A non-Opus model must not change the observed-token total."""
        records, _ = parse_lines(
            [
                _line(
                    model="claude-haiku-4-5",
                    input_tokens=7,
                    output_tokens=3,
                    cache_read=100,
                    cache_5m=20,
                    cache_1h=30,
                )
            ]
        )
        assert aggregate_by_branch(records)["issue-1-a"].total_tokens == 160

    def test_token_types_remain_separately_reported(self) -> None:
        records, _ = parse_lines(
            [_line(input_tokens=7, output_tokens=3, cache_read=100, cache_5m=20, cache_1h=30)]
        )
        stats = aggregate_by_branch(records)["issue-1-a"]
        assert (stats.input_tokens, stats.output_tokens, stats.cache_read, stats.cache_write) == (
            7,
            3,
            100,
            50,
        )
        assert stats.total_tokens == 160


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
        assert stats.per_turn == pytest.approx(stats.total_tokens / 2)

    def test_main_branch_bucketed_apart_from_issue_branches(self) -> None:
        """`main` is 37% of turns and heterogeneous: planning, review, and reading."""
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
        """Subagents are cheap turns: in the denominator, they would mask actual growth.

        Spawning a subagent is a recommended tactic (`mindset.md`), so a branch with them would
        dilute `per_turn` and the detector would report `steady` despite a rising cost.
        """
        records, _ = parse_lines(
            [
                _line(request_id="a", output_tokens=100),
                _line(request_id="b", sidechain=True, output_tokens=100),
            ]
        )
        stats = aggregate_by_branch(records)["issue-1-a"]
        assert stats.turns == 1
        assert stats.per_turn == pytest.approx(stats.total_tokens)

    def test_synthetic_records_excluded_from_denominator(self) -> None:
        """Service records are not billed: they are not turns and would lower `per_turn`."""
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
        """An empty timestamp would otherwise leave the branch at the start of the baseline window forever."""
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
        assert stats.sidechain_tokens is not None and stats.sidechain_tokens > 0
        assert stats.sidechain_tokens < stats.total_tokens


class TestHealth:
    def test_files_present_but_no_records_is_anomaly(self) -> None:
        anomalies = health_anomalies([], [], files_seen=5)
        assert [a.kind for a in anomalies] == ["no_usage_records"]

    def test_no_files_is_not_an_anomaly(self) -> None:
        assert health_anomalies([], [], files_seen=0) == []

    def test_missing_model_is_an_anomaly(self) -> None:
        """A renamed or empty `message.model` stays visible without price weights."""
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
        """A fresh aggregate replaces a ledger entry rather than adding to it."""
        ledger = {"issue-1-a": _stats("issue-1-a", turns=5)}
        fresh = {"issue-1-a": _stats("issue-1-a", turns=9)}
        assert merge_ledger(ledger, fresh)["issue-1-a"].turns == 9

    def test_partial_recount_does_not_shrink_history(self) -> None:
        """A branch is older than the read window: retention removed some transcripts.

        Its fresh aggregate is incomplete, and replacement would erase the complete entry—the branch
        would silently “lose weight,” and undercounting would read as reduced consumption. Long-lived buckets
        (`main` is 37% of measured turns) would suffer first.
        """
        ledger = {"main": _stats("main", turns=900)}
        fresh = {"main": _stats("main", turns=12)}
        assert merge_ledger(ledger, fresh)["main"].turns == 900

    def test_history_outlived_by_ledger_when_transcript_gone(self) -> None:
        """A branch erased by transcript retention remains in the comparison base."""
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


class TestLedgerMigration:
    @staticmethod
    def _schema_1_line() -> str:
        return json.dumps(
            {
                "schema": 1,
                "branch": "issue-legacy",
                "turns": 2,
                "first_seen": "2026-07-01",
                "last_seen": "2026-07-02",
                "effective": 123.0,
                "input_tokens": 7,
                "output_tokens": 3,
                "cache_read": 100,
                "cache_write": 50,
                "sidechain_effective": 40.0,
            }
        )

    def test_schema_1_line_keeps_reconstructible_raw_counts(self) -> None:
        restored, anomalies = parse_ledger([self._schema_1_line()])
        assert anomalies == []
        entry = restored["issue-legacy"]
        assert (entry.input_tokens, entry.output_tokens, entry.cache_read, entry.cache_write) == (
            7,
            3,
            100,
            50,
        )
        lines = ledger_lines(restored)
        assert json.loads(lines[0])["schema"] == 2
        round_tripped, read_anomalies = parse_ledger(lines)
        assert read_anomalies == []
        assert round_tripped == restored

    def test_legacy_sidechain_is_unavailable_not_zero(self) -> None:
        restored, _ = parse_ledger([self._schema_1_line()])
        assert restored["issue-legacy"].sidechain_tokens is None


class TestDetect:
    def test_absolute_floor_remains_a_gate_near_measured_baseline(self) -> None:
        """A 40% rise around the observed 100k baseline alone is still too noisy to alert."""
        old = [_stats(f"issue-o{i}", per_turn=100_000, first=f"2026-07-0{i + 1}") for i in range(5)]
        relative_only = [
            _stats(f"issue-r{i}", per_turn=140_000, first=f"2026-08-0{i + 1}") for i in range(5)
        ]
        enough_raw_growth = [
            _stats(f"issue-n{i}", per_turn=200_000, first=f"2026-08-0{i + 1}") for i in range(5)
        ]
        assert detect_growth(old + relative_only).status == "steady"
        assert detect_growth(old + enough_raw_growth).status == "grown"

    def test_growth_above_threshold_reports_verdict(self) -> None:
        floor = token_trend.DEFAULT_ABS_FLOOR
        old = [_stats(f"issue-o{i}", per_turn=floor, first=f"2026-07-0{i + 1}") for i in range(5)]
        new = [
            _stats(f"issue-n{i}", per_turn=floor * 3, first=f"2026-08-0{i + 1}") for i in range(5)
        ]
        assert detect_growth(old + new).status == "grown"

    def test_growth_below_threshold_is_silent(self) -> None:
        floor = token_trend.DEFAULT_ABS_FLOOR
        old = [
            _stats(f"issue-o{i}", per_turn=floor * 3, first=f"2026-07-0{i + 1}") for i in range(5)
        ]
        new = [
            _stats(f"issue-n{i}", per_turn=floor * 3 + floor / 10, first=f"2026-08-0{i + 1}")
            for i in range(5)
        ]
        assert detect_growth(old + new).status == "steady"

    def test_single_outlier_branch_does_not_trigger(self) -> None:
        """Median, not mean: one long refactoring session does not move the verdict."""
        old = [_stats(f"issue-o{i}", per_turn=100_000, first=f"2026-07-0{i + 1}") for i in range(5)]
        new = [_stats(f"issue-n{i}", per_turn=100_000, first=f"2026-08-0{i + 1}") for i in range(4)]
        new.append(_stats("issue-spike", per_turn=5_000_000, first="2026-08-05"))
        assert detect_growth(old + new).status == "steady"

    def test_relative_growth_below_absolute_floor_is_silent(self) -> None:
        """Doubling tiny branches is not degradation, but small-sample noise."""
        floor = token_trend.DEFAULT_ABS_FLOOR
        old = [
            _stats(f"issue-o{i}", per_turn=floor / 100, first=f"2026-07-0{i + 1}") for i in range(5)
        ]
        new = [
            _stats(f"issue-n{i}", per_turn=floor / 10, first=f"2026-08-0{i + 1}") for i in range(5)
        ]
        assert detect_growth(old + new).status == "steady"

    def test_insufficient_window_is_not_growth(self) -> None:
        few = [_stats(f"issue-o{i}", first=f"2026-08-0{i + 1}") for i in range(3)]
        assert detect_growth(few).status == "insufficient_data"

    def test_turnless_bucket_does_not_move_the_median(self) -> None:
        """A branch containing only subagents has `per_turn = 0`—in the median this would suppress the alert.

        It would persist forever: after retention there is nothing to recalculate that bucket from, and it
        does not leave the ledger.
        """
        old = [_stats(f"issue-o{i}", per_turn=100_000, first=f"2026-07-0{i + 1}") for i in range(5)]
        new = [_stats(f"issue-n{i}", per_turn=500_000, first=f"2026-08-0{i + 1}") for i in range(5)]
        turnless = BranchStats(
            branch="issue-subagents-only",
            turns=0,
            first_seen="2026-08-03",
            last_seen="2026-08-03",
            input_tokens=100_000,
            output_tokens=0,
            cache_read=0,
            cache_write=0,
            sidechain_tokens=100_000,
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
        """A foreign environment (cloud reviewer, other machine) does not add noise to every session context."""
        missing = tmp_path / "no-claude" / "projects" / "slug"
        assert run_hook({"source": "startup"}, missing, tmp_path / "ledger.jsonl") == ""

    def test_missing_slug_dir_surfaces_error(self, tmp_path: Path) -> None:
        """Own machine but no repository directory: slug rule drifted—the metric would die silently."""
        projects = tmp_path / "projects"
        projects.mkdir()
        assert run_hook({"source": "startup"}, projects / "slug", tmp_path / "ledger.jsonl") != ""

    def test_empty_dir_surfaces_error(self, tmp_path: Path) -> None:
        """Own environment but no records—the metric is broken, and that must be visible."""
        transcripts = self._dir_with(tmp_path, [json.dumps({"type": "user"})])
        assert run_hook({"source": "startup"}, transcripts, tmp_path / "ledger.jsonl") != ""

    @pytest.mark.parametrize("source", ["resume", "compact", "fork", None])
    def test_non_startup_source_is_silent(self, tmp_path: Path, source: str | None) -> None:
        """`compact`/`resume` do not repeat the alert within one task."""
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
        """Hook cost does not grow with history: old files are not parsed."""
        transcripts = self._dir_with(tmp_path, [_line()])
        stale = transcripts / "old.jsonl"
        stale.write_text(_line(request_id="old", branch="issue-old"), encoding="utf-8")
        os.utime(stale, (0, 0))
        records, _, files_seen = collect(transcripts, days=45)
        assert files_seen == 1
        assert {r.branch for r in records} == {"issue-1-a"}

    def test_ledger_in_transcript_dir_not_read_as_transcript(self, tmp_path: Path) -> None:
        """The ledger lives beside transcripts and is rewritten every session.

        If `collect` globbed it too, after retention (transcripts removed, ledger retained), it would yield
        `files_seen=1, records=0`—a permanent false “schema drift” in every session context.
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
        """The hook is killed at `timeout` without a trace, and `write_ledger` (after parsing) cannot run.

        The metric would stop silently forever, so approaching the limit is a visible anomaly.
        """
        transcripts = self._dir_with(tmp_path, [_line()])
        monkeypatch.setattr(token_trend, "HOOK_TIMEOUT_SECONDS", 0)
        assert "slow_collect" in run_hook(
            {"source": "startup"}, transcripts, tmp_path / "ledger.jsonl"
        )

    def test_vanished_file_does_not_crash_collect(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file can disappear between `glob` and `stat`—report mode would otherwise traceback."""
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
        """Ledger writing is atomic: a truncated file loses the entire comparison base."""
        transcripts = self._dir_with(tmp_path, [_line()])
        ledger = tmp_path / "ledger.jsonl"
        run_hook({"source": "startup"}, transcripts, ledger)
        run_hook({"source": "startup"}, transcripts, ledger)
        restored, anomalies = parse_ledger(ledger.read_text(encoding="utf-8").splitlines())
        assert anomalies == []
        assert "issue-1-a" in restored
        assert not list(ledger.parent.glob("*.tmp"))


class TestEmit:
    """Cyrillic output must not depend on the console code page (`CLAUDE.md` §Environment)."""

    class _Cp1252Stream:
        """stdout that makes `print` fail on Cyrillic—the exact Windows console behavior."""

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
    """Hook and report must use one directory, otherwise history splits in two."""

    @staticmethod
    def _run_hook_main(transcripts: Path, monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
        monkeypatch.setattr("sys.argv", ["token_trend.py", "--hook"])
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        assert token_trend.main() == 0

    def test_hook_writes_ledger_beside_payload_transcripts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`resolve_transcripts` wiring in `main`: the ledger sits with the same transcripts."""
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
        """The payload directory diverged from the slug rule—the operator must not be led into emptiness.

        `--report` (the only instruction printed by the alert) reads by the slug rule; silence would send the
        operator to `no transcript directory` with exit code 1.
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
    """`SessionStart` provides `transcript_path`—the most common path must not depend on the slug."""

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
    """Guard against dead code: a metric without an automatic trigger repeats eval’s fate (#361)."""

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
        """Parsing the transcript window costs wall-clock time in every session: it is explicitly bounded."""
        ours = [h for h in self._session_start_hooks() if "token_trend.py" in h.get("command", "")]
        assert ours and all(isinstance(hook.get("timeout"), int) for hook in ours)

    def test_declared_timeout_matches_the_code(self) -> None:
        """The script warns when approaching the limit, so it must know the limit value."""
        ours = [h for h in self._session_start_hooks() if "token_trend.py" in h.get("command", "")]
        assert all(h["timeout"] == token_trend.HOOK_TIMEOUT_SECONDS for h in ours)

    def test_session_start_hook_registered(self) -> None:
        assert any("token_trend.py" in command for command in self._session_start_commands())

    def test_registered_in_hook_mode(self) -> None:
        """Without `--hook`, the script would print a table into every session context."""
        commands = [c for c in self._session_start_commands() if "token_trend.py" in c]
        assert all("--hook" in command for command in commands)


def _alerting_lines() -> list[str]:
    """A branch set for which the detector must report `grown`."""
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
    """The metric’s main human-readable output is tested, not treated as “thin I/O.”"""

    def test_lists_branches_and_verdict(self) -> None:
        stats = [_stats("issue-1-a", turns=4, per_turn=25_000)]
        text = format_report(stats, detect_growth(stats), [])
        assert "issue-1-a" in text
        assert "вердикт" in text

    def test_shows_sidechain_share(self) -> None:
        """Subagents are a separate cost line; otherwise it is unclear what to fix."""
        stats = [
            BranchStats(
                branch="issue-1-a",
                turns=4,
                first_seen="2026-08-01",
                last_seen="2026-08-02",
                input_tokens=100_000,
                output_tokens=1,
                cache_read=1,
                cache_write=1,
                sidechain_tokens=40_000,
            )
        ]
        assert "sidechain" in format_report(stats, detect_growth(stats), []).lower()

    def test_report_header_is_raw_tokens_and_unavailable_sidechain_prints_dash(self) -> None:
        """`per-turn 0.0k` beside a non-empty `effective` reads as “the branch is free.”"""
        stats = [
            BranchStats(
                branch="issue-subagents-only",
                turns=0,
                first_seen="2026-08-01",
                last_seen="2026-08-01",
                input_tokens=100_000,
                output_tokens=0,
                cache_read=0,
                cache_write=0,
                sidechain_tokens=None,
            )
        ]
        lines = format_report(stats, detect_growth(stats), []).splitlines()
        header = lines[0].split()
        row = next(line for line in lines if "issue-subagents-only" in line).split()
        assert "tokens" in header
        assert "effective" not in header
        assert row[header.index("per-turn")] == "—"
        assert row[header.index("sidechain")] == "—"

    def test_anomalies_reach_the_report(self) -> None:
        text = format_report([], detect_growth([]), [Anomaly("no_usage_records", "0 файлов")])
        assert "no_usage_records" in text


class TestReportMode:
    """`--report` is promised to the operator in the alert: an unknown flag would lead to a dead end."""

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
        """Guard against alert text diverging from the real interface.

        At the moment the metric was created for, the alert must not send the operator to
        `unrecognized arguments`.
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
        """`SessionStart` returns stdout to context only on exit 0—a nonzero code would swallow the alert."""
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
        """An invalid byte in the ledger is `UnicodeDecodeError`, not `OSError`.

        Failure occurs before `write_ledger`, so the file is never rewritten: the hook would report
        “hook error” every session and the metric could not self-heal.
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
        """`"turns": "12"` passed the constructor and crashed `merge_ledger`—before `write_ledger`.

        Thus the file was never rewritten and the metric died forever. A malformed record must be a normal
        anomaly, so the ledger heals itself on the next startup.
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
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read": 0,
            "cache_write": 0,
            "sidechain_tokens": 0,
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
        """On exit 0 the channel to context is stdout; the user sees stderr only on failure."""
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
        """Closed stdout is the same failure mode described in `emit`’s docstring."""
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
