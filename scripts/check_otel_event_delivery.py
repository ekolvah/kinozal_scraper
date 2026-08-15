"""Report a Claude Code telemetry window where metrics arrive and events do not (#542).

An empty Loki panel is indistinguishable from "there was no spend": that is how event
delivery stayed broken for months while the metric half kept working (§IV). This check
reads both signals over one window through the Grafana datasource proxy and turns the
discrepancy into an exit code.

It is read-only — one GET per signal — and thresholdless: the finding is the observable
fact "sessions in metrics, zero event series in the same window", not a tuned lag limit.
ADR-0006 defers thresholds until a baseline exists; ADR-0010 records why this check may
hold live credentials at all.

Usage: python scripts/check_otel_event_delivery.py [--minutes N] [--env-file PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

REQUIRED_CREDENTIALS = ("GRAFANA_URL", "GRAFANA_SERVICE_ACCOUNT_TOKEN")
METRICS_UID = "grafanacloud-prom"
LOGS_UID = "grafanacloud-logs"
SESSIONS_BY_VERSION = "count by (service_version) (claude_code_session_count_total)"
EVENTS_BY_VERSION = (
    'sum by (service_version) (count_over_time({{service_name="claude-code"}}[{window}]))'
)
DEFAULT_WINDOW_MINUTES = 60
REQUEST_TIMEOUT_SECONDS = 30

ALIGNED = "aligned"
EVENTS_MISSING = "events-missing"
WINDOW_EMPTY = "window-empty"


class DeliveryUnavailable(RuntimeError):
    """The stack could not be read, which is never the same as a healthy window."""


@dataclass(frozen=True)
class DeliveryVerdict:
    """What one observation window says about event delivery."""

    state: str
    findings: tuple[str, ...]

    def exit_code(self) -> int:
        """Only a real discrepancy is a failure; an empty window is inconclusive, not red."""
        return 1 if self.state == EVENTS_MISSING else 0


def _labels(series: Mapping[str, Any]) -> str:
    labels = series.get("labels") or {}
    return ", ".join(f"{name}={value}" for name, value in sorted(labels.items())) or "unlabelled"


def signal_series(response: Mapping[str, Any], signal: str) -> list[dict[str, Any]]:
    """Return the series of one signal, rejecting any response that is not a readable 200."""
    status = response.get("status_code")
    if status != 200:
        raise DeliveryUnavailable(
            f"the {signal} query answered HTTP {status!r}; an unread window is not an empty one"
        )
    series = response.get("series")
    if not isinstance(series, list):
        raise DeliveryUnavailable(
            f"the {signal} response carries no series list; refusing to read that as zero"
        )
    return series


def delivery_verdict(observation: Mapping[str, Any]) -> DeliveryVerdict:
    """Classify one window of both signals into aligned / events-missing / window-empty."""
    metrics = signal_series(observation["metrics"], "metrics")
    events = signal_series(observation["events"], "events")
    window = observation.get("window") or {}

    if not metrics and not events:
        return DeliveryVerdict(
            WINDOW_EMPTY,
            (
                f"window empty: neither metrics nor events in {_window_text(window)}. "
                "No session ran, so delivery is unproven — this is not a healthy result.",
            ),
        )
    if metrics and not events:
        arrived = "; ".join(_labels(series) for series in metrics)
        return DeliveryVerdict(
            EVENTS_MISSING,
            (
                f"metrics arrived for {len(metrics)} series ({arrived}) in {_window_text(window)}, "
                "and 0 event series reached Loki over the same window.",
                "Sessions visible in metrics are invisible in events: the panels built on "
                "api_request / tool_result render as no-data rather than as this gap (#542).",
            ),
        )
    return DeliveryVerdict(ALIGNED, ())


def _window_text(window: Mapping[str, Any]) -> str:
    if "minutes" in window:
        return f"the last {window['minutes']} minutes"
    if "days" in window:
        return f"the last {window['days']} days"
    return "the observed window"


def read_credentials(environment: Mapping[str, str]) -> tuple[str, str]:
    """Return the Grafana base URL and service-account token, or fail loudly.

    The message names only the absent variables: echoing what was found would put a
    token into stdout and into whatever captured it.
    """
    missing = [name for name in REQUIRED_CREDENTIALS if not environment.get(name)]
    if missing:
        raise DeliveryUnavailable(
            f"missing credentials: {', '.join(missing)}. "
            "Delivery cannot be read, which is not the same as no gaps being found."
        )
    return environment["GRAFANA_URL"].rstrip("/"), environment["GRAFANA_SERVICE_ACCOUNT_TOKEN"]


def load_environment(env_file: Path | None) -> dict[str, str]:
    """Overlay the local .env carrier on the process environment, without exporting it."""
    values = dict(os.environ)
    if env_file is None or not env_file.is_file():
        return values
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if separator and name.strip() not in values:
            values[name.strip()] = value.strip().strip("'\"")
    return values


def _series_from_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize the Prometheus/Loki range shape into labels plus non-zero points."""
    results = ((payload.get("data") or {}).get("result")) or []
    series: list[dict[str, Any]] = []
    for entry in results:
        points = [
            [int(float(stamp)), str(value)]
            for stamp, value in entry.get("values", [])
            if float(value) != 0
        ]
        if points:
            series.append({"labels": entry.get("metric", {}), "non_zero": points})
    return series


def fetch_observation(
    session: requests.Session, base_url: str, minutes: int, now: int
) -> dict[str, Any]:
    """Read both signals over one window through the existing datasource proxy (GET only)."""
    start, end = now - minutes * 60, now
    window = f"{minutes}m"
    requests_by_signal = {
        "metrics": (
            f"{base_url}/api/datasources/proxy/uid/{METRICS_UID}/api/v1/query_range",
            SESSIONS_BY_VERSION,
        ),
        "events": (
            f"{base_url}/api/datasources/proxy/uid/{LOGS_UID}/loki/api/v1/query_range",
            EVENTS_BY_VERSION.format(window=window),
        ),
    }
    observation: dict[str, Any] = {
        "window": {"minutes": minutes, "start_unix": start, "end_unix": end}
    }
    for signal, (url, query) in requests_by_signal.items():
        parameters = {"query": query, "start": start, "end": end, "step": 60}
        try:
            answer = session.get(url, params=parameters, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as failure:
            raise DeliveryUnavailable(
                f"the {signal} query could not be sent: {failure}"
            ) from failure
        if answer.status_code != 200:
            observation[signal] = {"status_code": answer.status_code, "series": []}
            continue
        try:
            payload = answer.json()
        except ValueError as failure:
            raise DeliveryUnavailable(f"the {signal} answer was not JSON: {failure}") from failure
        observation[signal] = {"status_code": 200, "series": _series_from_payload(payload)}
    return observation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=int, default=DEFAULT_WINDOW_MINUTES)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--observation", type=Path, help="judge a captured window instead of the stack"
    )
    arguments = parser.parse_args(argv)

    try:
        if arguments.observation:
            observation = json.loads(arguments.observation.read_text(encoding="utf-8"))
        else:
            base_url, token = read_credentials(load_environment(arguments.env_file))
            session = requests.Session()
            session.headers.update({"Authorization": f"Bearer {token}"})
            observation = fetch_observation(session, base_url, arguments.minutes, int(time.time()))
        verdict = delivery_verdict(observation)
    except DeliveryUnavailable as failure:
        print(f"unavailable: {failure}", file=sys.stderr)
        return 2

    if verdict.state == ALIGNED:
        print("ok: metrics and events both arrived in the window")
        return 0
    for finding in verdict.findings:
        print(f"{verdict.state}: {finding}")
    return verdict.exit_code()


if __name__ == "__main__":
    sys.exit(main())
