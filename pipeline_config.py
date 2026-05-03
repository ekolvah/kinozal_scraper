from __future__ import annotations

import contextlib
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

_MACRO_RE = re.compile(r"\{\{(\w+)\}\}")

_SUPPORTED_VERSIONS = {1}
_REQUIRED_SOURCE_FIELDS = {
    "id",
    "type",
    "url",
    "limit",
    "sheet_tab",
    "dedupe_key",
    "fields",
    "message_template",
}
_SUPPORTED_TYPES = {"json", "html"}


class ConfigError(ValueError):
    pass


def build_macro_context(
    today: date | None = None, env: dict[str, str] | None = None
) -> dict[str, str]:
    if today is None:
        today = date.today()
    if env is None:
        env = dict(os.environ)

    return {
        "TODAY": today.isoformat(),
        "DATE_MINUS_7_DAYS": (today - timedelta(days=7)).isoformat(),
        "GITHUB_TOP_LIMIT": env.get("GITHUB_TOP_LIMIT", "10"),
        "STEAM_TOP_LIMIT": env.get("STEAM_TOP_LIMIT", "10"),
        "KINOZAL_TOP_URL": env.get("KINOZAL_TOP_URL", ""),
    }


def expand_macros(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _MACRO_RE.sub(lambda m: context.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: expand_macros(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_macros(item, context) for item in value]
    return value


def validate_sources_config(config: Any) -> None:
    if not isinstance(config, dict):
        raise ConfigError("Config must be a JSON object")

    version = config.get("version")
    if version not in _SUPPORTED_VERSIONS:
        raise ConfigError(
            f"Unsupported config version: {version!r}. Supported: {_SUPPORTED_VERSIONS}"
        )

    sources = config.get("sources")
    if not isinstance(sources, list):
        raise ConfigError("'sources' must be a list")

    for source in sources:
        if not isinstance(source, dict):
            raise ConfigError("Each source must be a JSON object")

        source_id = source.get("id", "<unknown>")

        missing = _REQUIRED_SOURCE_FIELDS - source.keys()
        if missing:
            raise ConfigError(f"Source '{source_id}' is missing required fields: {sorted(missing)}")

        if source["type"] not in _SUPPORTED_TYPES:
            raise ConfigError(f"Source '{source_id}' has unsupported type: {source['type']!r}")

        try:
            limit = int(source["limit"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"Source '{source_id}': 'limit' must be an integer, got {source['limit']!r}"
            ) from exc
        if limit <= 0:
            raise ConfigError(
                f"Source '{source_id}': 'limit' must be a positive integer, got {limit}"
            )


def load_sources_config(path: str | Path = "sources.json") -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc

    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc

    context = build_macro_context()
    config = cast(dict[str, Any], expand_macros(config, context))

    for source in config.get("sources", []):
        if isinstance(source, dict) and "limit" in source:
            with contextlib.suppress(TypeError, ValueError):
                source["limit"] = int(source["limit"])

    validate_sources_config(config)
    return config
