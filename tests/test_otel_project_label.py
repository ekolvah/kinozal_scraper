"""Project label of this repository's Claude Code telemetry.

Separate from `test_claude_otel_assets.py` on purpose: that file leaves with the telemetry
stack, while the label is a per-adopter setting that stays.
"""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS = Path(__file__).resolve().parents[1] / ".claude" / "settings.json"


def test_settings_carry_project_pairs() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    raw = settings.get("env", {}).get("OTEL_RESOURCE_ATTRIBUTES")
    assert raw, "`.claude/settings.json` sets no env.OTEL_RESOURCE_ATTRIBUTES"

    entries = raw.split(",")
    # The OTel SDK discards the whole value when any entry is not one key=value pair.
    malformed = [entry for entry in entries if entry.count("=") != 1]
    assert not malformed, f"entries the SDK cannot parse: {malformed}"

    pairs = dict(entry.split("=") for entry in entries)
    assert pairs.get("vcs.repository.name") == "ekolvah/kinozal_scraper"
    assert pairs.get("vcs.repository.url.full") == "https://github.com/ekolvah/kinozal_scraper"
