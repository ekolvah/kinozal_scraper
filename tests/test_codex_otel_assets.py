"""Structural and privacy contract for Codex OTel assets (#472)."""

from __future__ import annotations

import importlib.util
import json
import re
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CODEX_ASSET_DIR = ROOT / "observability" / "codex"
CODEX_TEMPLATE = CODEX_ASSET_DIR / "otel.toml.example"
ALLOY_TEMPLATE = CODEX_ASSET_DIR / "config.alloy.example"
CODEX_CATALOGUE = CODEX_ASSET_DIR / "signal-catalogue.json"
AGENT_DASHBOARD = ROOT / "observability" / "agent-telemetry" / "dashboard.json"
CONFIG_GUARD = ROOT / "scripts" / "check_codex_otel_config.py"


def _load_json(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"missing repository asset: {path.relative_to(ROOT)}"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _load_toml(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"missing repository asset: {path.relative_to(ROOT)}"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _load_config_guard() -> ModuleType:
    assert CONFIG_GUARD.is_file(), f"missing config guard: {CONFIG_GUARD.relative_to(ROOT)}"
    spec = importlib.util.spec_from_file_location("check_codex_otel_config", CONFIG_GUARD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dashboard_panels(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    panels: list[dict[str, Any]] = []
    for panel in dashboard["panels"]:
        panels.append(panel)
        panels.extend(panel.get("panels", []))
    return panels


class TestCodexOtelTemplate:
    def test_exports_metrics_only_without_content_logs_or_traces(self) -> None:
        config = _load_toml(CODEX_TEMPLATE)

        assert config["analytics"]["enabled"] is True
        otel = config["otel"]
        assert otel["environment"] == "<codex-environment>"
        assert otel["log_user_prompt"] is False
        assert otel["exporter"] == "none"
        assert otel["trace_exporter"] == "none"
        assert set(otel["metrics_exporter"]) == {"otlp-http"}
        exporter = otel["metrics_exporter"]["otlp-http"]
        assert exporter == {
            "endpoint": "http://127.0.0.1:4318/v1/metrics",
            "protocol": "binary",
        }

    def test_template_contains_placeholders_not_credentials(self) -> None:
        config = _load_toml(CODEX_TEMPLATE)
        serialized = json.dumps(config)

        assert "<" in serialized and ">" in serialized
        assert "grafana" not in serialized.lower()
        assert not re.search(r"glc_[A-Za-z0-9_-]+", serialized)
        assert not re.search(r"Basic [A-Za-z0-9+/=]{16,}", serialized)


class TestAlloyConfig:
    def test_pipeline_converts_and_batches_before_cloud_export(self) -> None:
        guard = _load_config_guard()
        alloy = ALLOY_TEMPLATE.read_text(encoding="utf-8")

        assert guard.validate_alloy_config(alloy) == []
        assert 'otelcol.receiver.otlp "codex"' in alloy
        assert 'otelcol.processor.deltatocumulative "codex"' in alloy
        assert 'otelcol.processor.batch "codex"' in alloy
        assert 'otelcol.exporter.otlphttp "grafana"' in alloy
        assert 'destination = "stderr"' in alloy

    def test_receiver_is_loopback_and_cloud_secrets_are_environment_only(self) -> None:
        alloy = ALLOY_TEMPLATE.read_text(encoding="utf-8")

        assert 'endpoint = "127.0.0.1:4318"' in alloy
        assert 'sys.env("GRAFANA_CLOUD_OTLP_ENDPOINT")' in alloy
        assert 'sys.env("GRAFANA_CLOUD_INSTANCE_ID")' in alloy
        assert 'sys.env("GRAFANA_CLOUD_OTLP_TOKEN")' in alloy
        assert "glc_" not in alloy
        assert not re.search(r"Basic [A-Za-z0-9+/=]{16,}", alloy)


class TestCodexConfigGuard:
    def test_accepts_metrics_only_config(self) -> None:
        guard = _load_config_guard()
        config = _load_toml(CODEX_TEMPLATE)
        alloy = ALLOY_TEMPLATE.read_text(encoding="utf-8")

        assert guard.validate_config(config) == []
        assert guard.validate_alloy_config(alloy) == []

    def test_rejects_prompt_log_or_trace_export(self) -> None:
        guard = _load_config_guard()
        base = _load_toml(CODEX_TEMPLATE)
        unsafe_values = (
            ("log_user_prompt", True),
            ("exporter", {"otlp-http": {"endpoint": "https://example.test/v1/logs"}}),
            ("trace_exporter", {"otlp-http": {"endpoint": "https://example.test/v1/traces"}}),
        )

        for key, value in unsafe_values:
            config = json.loads(json.dumps(base))
            config["otel"][key] = value
            errors = guard.validate_config(config)
            assert errors, f"unsafe {key} unexpectedly passed"

    def test_rejects_direct_cloud_or_unsafe_alloy_pipeline(self) -> None:
        guard = _load_config_guard()
        direct = _load_toml(CODEX_TEMPLATE)
        direct["otel"]["metrics_exporter"]["otlp-http"]["endpoint"] = (
            "https://otlp-gateway.example.test/otlp/v1/metrics"
        )
        assert guard.validate_config(direct)

        alloy = ALLOY_TEMPLATE.read_text(encoding="utf-8")
        unsafe_variants = (
            alloy.replace('endpoint = "127.0.0.1:4318"', 'endpoint = "0.0.0.0:4318"'),
            alloy.replace(
                "metrics = [otelcol.processor.deltatocumulative.codex.input]",
                "metrics = [otelcol.processor.batch.codex.input]",
            ),
            alloy.replace(
                "metrics = [otelcol.processor.batch.codex.input]",
                "metrics = [otelcol.exporter.otlphttp.grafana.input]",
            ),
        )
        for unsafe in unsafe_variants:
            assert guard.validate_alloy_config(unsafe)


class TestCodexSignalCatalogue:
    def test_catalogue_contains_names_and_attributes_but_no_values(self) -> None:
        catalogue = _load_json(CODEX_CATALOGUE)

        assert catalogue["schema_version"] == 1
        assert catalogue["capture"]["destination"] == "Grafana Cloud"
        assert catalogue["capture"]["status"] == "verified"
        assert catalogue["capture"]["entry_point"] == "VS Code codex app-server"
        assert catalogue["capture"]["exporters"] == {
            "metrics": "otlp-http",
            "logs": "none",
            "traces": "none",
        }
        assert {"github_issue", "git_branch"} <= set(catalogue["capture"]["missing_dimensions"])
        assert set(catalogue["signals"]) == {"metrics"}
        assert catalogue["signals"]["metrics"]

        for signal in catalogue["signals"]["metrics"]:
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


class TestAgentDashboard:
    def test_codex_queries_use_only_captured_metrics(self) -> None:
        catalogue = _load_json(CODEX_CATALOGUE)
        dashboard = _load_json(AGENT_DASHBOARD)
        known_refs = {f"metrics:{signal['name']}" for signal in catalogue["signals"]["metrics"]}
        codex_targets = [
            target
            for panel in _dashboard_panels(dashboard)
            for target in panel.get("targets", [])
            if target.get("source") == "codex"
        ]

        assert codex_targets
        assert all(target["catalogueRef"] in known_refs for target in codex_targets)
        assert all(target.get("expr") for target in codex_targets)

    def test_missing_codex_sample_is_visible_not_zero(self) -> None:
        dashboard = _load_json(AGENT_DASHBOARD)
        codex_panels = [
            panel for panel in _dashboard_panels(dashboard) if panel.get("source") == "codex"
        ]

        assert codex_panels
        assert all("unavailable" in panel.get("description", "").lower() for panel in codex_panels)
        serialized = json.dumps(codex_panels).lower()
        assert "vector(0)" not in serialized
        assert '"novalue": "0"' not in serialized

    def test_dashboard_does_not_claim_issue_level_attribution(self) -> None:
        dashboard = _load_json(AGENT_DASHBOARD)
        limitations = dashboard["agentTelemetry"]["missingDimensions"]
        expressions = [
            target.get("expr", "")
            for panel in _dashboard_panels(dashboard)
            for target in panel.get("targets", [])
        ]

        assert {"github_issue", "git_branch"} <= set(limitations)
        assert any(
            "issue/branch attribution is unavailable"
            in panel.get("options", {}).get("content", "").lower()
            for panel in _dashboard_panels(dashboard)
        )
        assert not any(
            "github_issue" in expression or "git_branch" in expression for expression in expressions
        )
