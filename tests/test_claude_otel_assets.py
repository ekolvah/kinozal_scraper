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
GRAFANA_DASHBOARD = ROOT / "observability" / "agent-telemetry" / "dashboard.json"


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


def _targets_with_datasource(dashboard: dict[str, Any]) -> list[tuple[dict[str, Any], str | None]]:
    """Pair every target with its effective datasource type; a target overrides its panel."""
    pairs: list[tuple[dict[str, Any], str | None]] = []
    for row in dashboard["panels"]:
        for panel in (row, *row.get("panels", [])):
            panel_type = (panel.get("datasource") or {}).get("type")
            for target in panel.get("targets", []):
                target_type = (target.get("datasource") or {}).get("type")
                pairs.append((target, target_type or panel_type))
    return pairs


def _dashboard_targets(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    return [target for target, _ in _targets_with_datasource(dashboard)]


def _stream_selector_labels(expr: str) -> set[str]:
    """Label names inside the ``{...}`` stream selectors of a LogQL expression."""
    selectors = re.findall(r"\{([^{}]*)\}", expr)
    assert selectors, f"LogQL expression without a stream selector: {expr}"
    labels: set[str] = set()
    for selector in selectors:
        labels.update(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:=~|!~|!=|=)", selector))
    return labels


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

        assert catalogue["schema_version"] == 2
        assert catalogue["capture"]["destination"] == "Grafana Cloud"
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

    def test_each_signal_half_carries_its_own_observation(self) -> None:
        """A half's verified status may not be inferred from the other half (#549)."""
        catalogue = _load_json(SIGNAL_CATALOGUE)
        provenance = catalogue["capture"]["signal_provenance"]

        assert set(provenance) == set(catalogue["signals"])

        for entry in provenance.values():
            assert entry["status"] in {"verified", "unreproduced"}
            assert entry["observed"]
            assert entry["claude_code_versions"]
            assert all(isinstance(version, str) for version in entry["claude_code_versions"])
            if entry["status"] != "verified":
                absent_on = entry["absent_on"]
                assert absent_on["observed"]
                assert absent_on["claude_code_versions"]
                assert all(
                    isinstance(version, str) for version in absent_on["claude_code_versions"]
                )

        # The flat verdict this replaces may not resurface at the top level.
        assert "status" not in catalogue["capture"]
        assert "captured_at" not in catalogue["capture"]
        assert "claude_code_version" not in catalogue["capture"]


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
        targets = [
            target for target in _dashboard_targets(dashboard) if target.get("source") != "codex"
        ]

        assert targets
        assert all(target["catalogueRef"] in known_refs for target in targets)
        assert all(target.get("expr") or target.get("query") for target in targets)

    def test_loki_selectors_contain_only_index_labels(self) -> None:
        """Structured metadata inside ``{...}`` matches nothing and fails silently (#543)."""
        catalogue = _load_json(SIGNAL_CATALOGUE)
        index_labels = set(catalogue["capture"]["log_index_labels"]["labels"])
        assert index_labels

        # The preserved valid record from the same live response: an index label in the
        # selector stays legal, and this half runs before the negative one.
        assert not _stream_selector_labels('{service_name="claude-code"}') - index_labels

        dashboard = _load_json(GRAFANA_DASHBOARD)
        loki_targets = [
            target
            for target, datasource_type in _targets_with_datasource(dashboard)
            if datasource_type == "loki"
        ]
        assert loki_targets, "no Loki target resolved: the guard would pass vacuously"

        violations: list[tuple[str, list[str]]] = []
        for target in loki_targets:
            expr = target["expr"]
            outside_index = sorted(_stream_selector_labels(expr) - index_labels)
            if outside_index:
                violations.append((expr, outside_index))

        assert not violations, (
            "a Loki stream selector may carry only the index labels captured from the stack; "
            f"filter every other attribute after the selector: {violations}"
        )

    def test_minimum_decision_panels_are_present(self) -> None:
        dashboard = _load_json(GRAFANA_DASHBOARD)
        decisions = {row.get("decision") for row in dashboard["panels"]}

        assert {
            "session-cost",
            "api-token-mix",
            "context-compaction",
            "tool-health",
            "attribution",
        } <= decisions


class TestNoBespokeAutomation:
    def test_assets_define_no_hook_scheduler_or_github_writer(self) -> None:
        expected_assets = {OTEL_TEMPLATE, SIGNAL_CATALOGUE}
        actual_assets = {path for path in ASSET_DIR.iterdir() if path.is_file()}
        assert actual_assets == expected_assets

        dashboard = _load_json(GRAFANA_DASHBOARD)
        assert "alert" not in json.dumps(dashboard).lower()
        assert not any(
            path.suffix in {".py", ".sh", ".ps1", ".yml", ".yaml"} for path in ASSET_DIR.rglob("*")
        )
