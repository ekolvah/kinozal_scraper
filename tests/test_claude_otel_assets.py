"""Structural and privacy contract for Claude Code OTel assets (#471)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "observability" / "claude-code"
OTEL_TEMPLATE = ASSET_DIR / "otel.env.example"
SIGNAL_CATALOGUE = ASSET_DIR / "signal-catalogue.json"
GRAFANA_DASHBOARD = ASSET_DIR / "dashboard.json"


def _load_json(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"missing repository asset: {path.relative_to(ROOT)}"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _template_environment() -> dict[str, str]:
    assert OTEL_TEMPLATE.is_file(), "missing values-free OTel setup template"
    environment: dict[str, str] = {}
    for raw_line in OTEL_TEMPLATE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        assert separator, f"invalid template assignment: {raw_line}"
        environment[name] = value
    return environment


def _catalogue_refs(catalogue: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for signal_kind in ("metrics", "events"):
        for signal in catalogue["signals"][signal_kind]:
            refs.add(f"{signal_kind}:{signal['name']}")
    return refs


def _dashboard_targets(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for row in dashboard["panels"]:
        targets.extend(row.get("targets", []))
        for panel in row.get("panels", []):
            targets.extend(panel.get("targets", []))
    return targets


class TestOtelTemplate:
    def test_exports_metrics_and_events_without_content_logging(self) -> None:
        environment = _template_environment()

        assert environment["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
        assert environment["OTEL_METRICS_EXPORTER"] == "otlp"
        assert environment["OTEL_LOGS_EXPORTER"] == "otlp"
        assert environment["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
        assert environment["OTEL_EXPORTER_OTLP_ENDPOINT"]
        assert environment["OTEL_EXPORTER_OTLP_HEADERS"]

        content_logging = {
            "OTEL_LOG_USER_PROMPTS",
            "OTEL_LOG_ASSISTANT_RESPONSES",
            "OTEL_LOG_TOOL_DETAILS",
            "OTEL_LOG_TOOL_CONTENT",
            "OTEL_LOG_RAW_API_BODIES",
        }
        assert content_logging.isdisjoint(environment)

    def test_template_contains_placeholders_not_credentials(self) -> None:
        environment = _template_environment()
        sensitive_values = {
            environment["OTEL_EXPORTER_OTLP_ENDPOINT"],
            environment["OTEL_EXPORTER_OTLP_HEADERS"],
        }

        assert all("<" in value and ">" in value for value in sensitive_values)
        assert not any(re.search(r"glc_[A-Za-z0-9_-]+", value) for value in sensitive_values)
        assert not any(re.search(r"Basic [A-Za-z0-9+/=]{16,}", value) for value in sensitive_values)


class TestSignalCatalogue:
    def test_catalogue_contains_names_and_attributes_but_no_values(self) -> None:
        catalogue = _load_json(SIGNAL_CATALOGUE)

        assert catalogue["schema_version"] == 1
        assert catalogue["capture"]["destination"] == "Grafana Cloud"
        assert catalogue["capture"]["status"] == "verified"
        assert catalogue["signals"]["metrics"]
        assert catalogue["signals"]["events"]

        for signal_kind in ("metrics", "events"):
            for signal in catalogue["signals"][signal_kind]:
                assert set(signal) == {"name", "backend_name", "attributes"}
                assert signal["name"]
                assert signal["backend_name"]
                assert signal["attributes"]
                assert all(isinstance(attribute, str) for attribute in signal["attributes"])

        serialized = json.dumps(catalogue)
        assert not re.search(r"[\w.+-]+@[\w.-]+", serialized)
        assert not re.search(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", serialized, re.IGNORECASE)
        assert '"value"' not in serialized
        assert '"values"' not in serialized
        assert '"sample"' not in serialized


class TestGrafanaDashboard:
    def test_dashboard_is_valid_importable_json(self) -> None:
        dashboard = _load_json(GRAFANA_DASHBOARD)

        assert dashboard["title"]
        assert isinstance(dashboard["schemaVersion"], int)
        assert dashboard["panels"]
        assert dashboard.get("id") is None
        assert dashboard.get("uid") is None

        variable_types = {variable["type"] for variable in dashboard["templating"]["list"]}
        assert {"datasource", "query"} <= variable_types

    def test_queries_use_only_captured_native_signals(self) -> None:
        catalogue = _load_json(SIGNAL_CATALOGUE)
        dashboard = _load_json(GRAFANA_DASHBOARD)
        known_refs = _catalogue_refs(catalogue)
        targets = _dashboard_targets(dashboard)

        assert targets
        assert all(target["catalogueRef"] in known_refs for target in targets)
        assert all(target.get("expr") or target.get("query") for target in targets)

    def test_minimum_decision_panels_are_present(self) -> None:
        dashboard = _load_json(GRAFANA_DASHBOARD)
        decisions = {row.get("decision") for row in dashboard["panels"]}

        assert decisions == {
            "session-cost",
            "api-token-mix",
            "context-compaction",
            "tool-health",
            "attribution",
        }


class TestNoBespokeAutomation:
    def test_assets_define_no_hook_scheduler_or_github_writer(self) -> None:
        expected_assets = {OTEL_TEMPLATE, SIGNAL_CATALOGUE, GRAFANA_DASHBOARD}
        actual_assets = {path for path in ASSET_DIR.iterdir() if path.is_file()}
        assert actual_assets == expected_assets

        dashboard = _load_json(GRAFANA_DASHBOARD)
        assert "alert" not in json.dumps(dashboard).lower()
        assert not any(
            path.suffix in {".py", ".sh", ".ps1", ".yml", ".yaml"} for path in ASSET_DIR.rglob("*")
        )
