"""Report a Claude Code telemetry window where metrics arrive and events do not (#542)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class DeliveryUnavailable(RuntimeError):
    """The stack could not be read, which is never the same as a healthy window."""


@dataclass(frozen=True)
class DeliveryVerdict:
    """What one observation window says about event delivery."""

    state: str
    findings: tuple[str, ...]

    def exit_code(self) -> int:
        raise NotImplementedError


def delivery_verdict(observation: Mapping[str, Any]) -> DeliveryVerdict:
    """Classify one window of both signals into aligned / events-missing / window-empty."""
    raise NotImplementedError


def read_credentials(environment: Mapping[str, str]) -> tuple[str, str]:
    """Return the Grafana base URL and service-account token, or fail loudly."""
    raise NotImplementedError


def signal_series(response: Mapping[str, Any], signal: str) -> list[dict[str, Any]]:
    """Return the series of one signal, rejecting any response that is not a readable 200."""
    raise NotImplementedError
