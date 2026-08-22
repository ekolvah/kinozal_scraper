"""Keep the portable visibility-over-silence Ruff rules active."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

_REPO = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO / "pyproject.toml"

_SILENCE_CODES = {"BLE", "TRY400"}
_DISABLE_TOKENS = {"BLE", "BLE001", "TRY", "TRY400"}


def _lint_config() -> dict[str, Any]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data["tool"]["ruff"]["lint"])


class TestRuffSilenceRules:
    def test_silence_rules_active(self) -> None:
        lint = _lint_config()

        selected = set(lint.get("select", [])) | set(lint.get("extend-select", []))
        assert selected >= _SILENCE_CODES, (
            f"silence-detection codes must be in ruff select; missing: {_SILENCE_CODES - selected}"
        )

        ignored = set(lint.get("ignore", []))
        assert not (_DISABLE_TOKENS & ignored), (
            f"silence-detection codes must not appear in ruff `ignore`: {_DISABLE_TOKENS & ignored}"
        )

        per_file = lint.get("per-file-ignores", {})
        leaked = {
            path: sorted(_DISABLE_TOKENS & set(codes))
            for path, codes in per_file.items()
            if _DISABLE_TOKENS & set(codes)
        }
        assert not leaked, (
            f"silence-detection codes must not appear in `per-file-ignores`: {leaked}"
        )
