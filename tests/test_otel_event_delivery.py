"""A metrics window without events must reach the operator, not an empty panel (#542)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.check_otel_event_delivery import (
    DeliveryUnavailable,
    delivery_verdict,
    read_credentials,
    signal_series,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _observation(name: str) -> dict[str, Any]:
    captured: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return captured


class TestDeliveryVerdict:
    def test_metrics_without_events_is_reported(self) -> None:
        """The paired test: the captured gap is flagged, the captured valid records are not."""
        gap = delivery_verdict(_observation("otel-delivery-gap.json"))

        assert gap.state == "events-missing"
        assert gap.findings
        assert gap.exit_code() != 0
        reported = " ".join(gap.findings)
        assert "2.1.232" in reported, "the finding must name the metric series that did arrive"
        assert "0" in reported, "the finding must name the event count that did not"

        accepted = delivery_verdict(_observation("otel-delivery-accepted.json"))

        assert accepted.state == "aligned"
        assert accepted.findings == ()

    def test_aligned_signals_report_no_gap(self) -> None:
        """A window carrying both signals exits zero, or the check becomes always-red."""
        verdict = delivery_verdict(_observation("otel-delivery-accepted.json"))

        assert verdict.exit_code() == 0
        assert verdict.findings == ()

    def test_empty_window_is_reported_as_empty(self) -> None:
        """No sessions at all is inconclusive, and must not read as health (§IV)."""
        verdict = delivery_verdict(_observation("otel-delivery-empty.json"))

        assert verdict.state == "window-empty"
        assert verdict.state != "aligned"
        assert any("empty" in finding.lower() for finding in verdict.findings)


class TestDeliveryIO:
    def test_missing_credentials_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Absent credentials raise; they never report an absence of gaps."""
        monkeypatch.delenv("GRAFANA_URL", raising=False)
        monkeypatch.delenv("GRAFANA_SERVICE_ACCOUNT_TOKEN", raising=False)

        with pytest.raises(DeliveryUnavailable) as failure:
            read_credentials({})

        assert "GRAFANA_URL" in str(failure.value)

    def test_credentials_never_reach_the_message(self) -> None:
        """A partial configuration must not echo the token it did find."""
        with pytest.raises(DeliveryUnavailable) as failure:
            read_credentials({"GRAFANA_SERVICE_ACCOUNT_TOKEN": "glc_secret_value"})

        assert "glc_secret_value" not in str(failure.value)

    def test_http_error_is_not_silent_health(self) -> None:
        """A non-200 answer is unavailability, not an empty result set."""
        with pytest.raises(DeliveryUnavailable):
            signal_series({"status_code": 502, "series": []}, "events")

    def test_readable_response_yields_its_series(self) -> None:
        """The captured 200 shape is what the verdict consumes."""
        observation = _observation("otel-delivery-gap.json")

        assert signal_series(observation["metrics"], "metrics")
        assert signal_series(observation["events"], "events") == []
