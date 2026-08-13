"""Validate the user-scope Codex-to-Alloy metrics pipeline (#472)."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

LOOPBACK_METRICS_ENDPOINT = "http://127.0.0.1:4318/v1/metrics"
ALLOWED_ALLOY_ENV = {
    "GRAFANA_CLOUD_INSTANCE_ID",
    "GRAFANA_CLOUD_OTLP_ENDPOINT",
    "GRAFANA_CLOUD_OTLP_TOKEN",
}


def validate_config(config: Mapping[str, Any]) -> list[str]:
    """Return contract violations for a parsed Codex config."""
    errors: list[str] = []
    analytics = config.get("analytics")
    if not isinstance(analytics, Mapping) or analytics.get("enabled") is not True:
        errors.append("analytics.enabled must be true for app-server metrics")

    otel = config.get("otel")
    if not isinstance(otel, Mapping):
        return [*errors, "missing [otel] table"]

    environment = otel.get("environment")
    if not isinstance(environment, str) or not environment.strip():
        errors.append("otel.environment must be a non-empty source label")
    if otel.get("log_user_prompt") is not False:
        errors.append("otel.log_user_prompt must be explicitly false")
    if otel.get("exporter") != "none":
        errors.append('otel.exporter must be "none"; Codex logs can contain tool output')
    if otel.get("trace_exporter") != "none":
        errors.append('otel.trace_exporter must be "none"')

    metrics_exporter = otel.get("metrics_exporter")
    if not isinstance(metrics_exporter, Mapping) or set(metrics_exporter) != {"otlp-http"}:
        errors.append("otel.metrics_exporter must define only otlp-http")
        return errors

    http = metrics_exporter["otlp-http"]
    if not isinstance(http, Mapping):
        errors.append("otel.metrics_exporter.otlp-http must be a table")
        return errors
    if set(http) != {"endpoint", "protocol"}:
        errors.append("the Codex exporter must contain only endpoint and protocol")
    if http.get("endpoint") != LOOPBACK_METRICS_ENDPOINT:
        errors.append(f"the Codex metrics endpoint must be {LOOPBACK_METRICS_ENDPOINT}")
    if http.get("protocol") != "binary":
        errors.append('the metrics OTLP/HTTP protocol must be "binary"')
    return errors


def _alloy_code(text: str) -> str:
    """Remove line comments before checking the safety-critical graph."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("//", "#"))
    )


def validate_alloy_config(text: str) -> list[str]:
    """Return violations for the deliberately small Alloy bridge template."""
    errors: list[str] = []
    code = _alloy_code(text)
    required_components = (
        'otelcol.receiver.otlp "codex"',
        'otelcol.processor.deltatocumulative "codex"',
        'otelcol.processor.batch "codex"',
        'otelcol.exporter.otlphttp "grafana"',
        'otelcol.auth.basic "grafana"',
    )
    for component in required_components:
        if component not in code:
            errors.append(f"missing Alloy component: {component}")

    required_fragments = (
        'destination = "stderr"',
        'endpoint = "127.0.0.1:4318"',
        "metrics = [otelcol.processor.deltatocumulative.codex.input]",
        "metrics = [otelcol.processor.batch.codex.input]",
        "metrics = [otelcol.exporter.otlphttp.grafana.input]",
        'endpoint = sys.env("GRAFANA_CLOUD_OTLP_ENDPOINT")',
        "auth     = otelcol.auth.basic.grafana.handler",
        'username = sys.env("GRAFANA_CLOUD_INSTANCE_ID")',
        'password = sys.env("GRAFANA_CLOUD_OTLP_TOKEN")',
    )
    for fragment in required_fragments:
        if fragment not in code:
            errors.append(f"missing safe Alloy pipeline edge: {fragment}")

    if re.search(r"\b(?:0\.0\.0\.0|\[?::\]?):4318\b", code):
        errors.append("the Alloy OTLP receiver must not listen outside IPv4 loopback")
    if re.search(r"\b(?:logs|traces)\s*=", code):
        errors.append("the Alloy bridge must route metrics only")

    referenced_env = set(re.findall(r'sys\.env\("([A-Z0-9_]+)"\)', code))
    if referenced_env != ALLOWED_ALLOY_ENV:
        errors.append(
            "the Alloy bridge must use only the three approved Grafana environment variables"
        )
    if re.search(r"glc_[A-Za-z0-9_-]+", code) or re.search(r"Basic [A-Za-z0-9+/=]{16,}", code):
        errors.append("the Alloy config contains a credential-like literal")
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex-config",
        type=Path,
        default=Path.home() / ".codex" / "config.toml",
        help="Codex config to validate (default: ~/.codex/config.toml)",
    )
    parser.add_argument(
        "--alloy-config",
        type=Path,
        default=Path.home() / ".config" / "alloy" / "config.alloy",
        help="Alloy config to validate (default: ~/.config/alloy/config.alloy)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    missing = [path for path in (args.codex_config, args.alloy_config) if not path.is_file()]
    if missing:
        for path in missing:
            print(f"error: configuration not found: {path}", file=sys.stderr)
        return 1

    try:
        codex_text = args.codex_config.read_text(encoding="utf-8")
        codex_config = tomllib.loads(codex_text)
        alloy_text = args.alloy_config.read_text(encoding="utf-8")
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        print(f"error: cannot read configuration: {exc}", file=sys.stderr)
        return 2

    errors = [
        *(f"Codex: {error}" for error in validate_config(codex_config)),
        *(f"Alloy: {error}" for error in validate_alloy_config(alloy_text)),
    ]
    if errors:
        print("error: Codex OTel bridge violates the metrics-only contract:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("ok: Codex exports metrics only through the loopback Alloy bridge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
