"""Anti-drift guard for the §IV/§V silence-detection ruff rules (#231).

§IV (visibility over silence) and §V (root cause before fix) are partly
machine-enforced via ruff `BLE001` (no blind `except`) and `TRY400`
(`logger.exception` in handlers — preserve the traceback). This guard asserts
those codes stay *active*: present in the effective select AND not silently
neutralised via `ignore` / `per-file-ignores`. Mirrors `test_settings_deny.py`
— it pins *enforcement*, not mere declaration, so a future agent cannot quietly
drop the gate through any of the disable vectors (#231 BLOCKING #1).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

_REPO = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO / "pyproject.toml"

# Codes that must be selected. Prefix forms are how ruff config carries them.
_SILENCE_CODES = {"BLE", "TRY400"}
# Any of these tokens, if present in ignore / per-file-ignores, disables a
# silence rule while leaving `select` untouched — the threat-model of #231.
_DISABLE_TOKENS = {"BLE", "BLE001", "TRY", "TRY400"}


def _lint_config() -> dict[str, Any]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data["tool"]["ruff"]["lint"])


class TestRuffSilenceRules:
    def test_silence_rules_active(self) -> None:
        lint = _lint_config()

        # (a) present in effective select (select ∪ extend-select) — robust to
        # a future move to extend-select (architect NICE #6).
        selected = set(lint.get("select", [])) | set(lint.get("extend-select", []))
        assert selected >= _SILENCE_CODES, (
            "silence-detection codes must be in ruff select (§IV/§V gate); "
            f"missing: {_SILENCE_CODES - selected}"
        )

        # (b) not neutralised via ignore (BLOCKING #1 disable vector).
        ignored = set(lint.get("ignore", []))
        assert not (_DISABLE_TOKENS & ignored), (
            "silence-detection codes must not appear in ruff `ignore` (silently "
            f"disables the §IV gate): {_DISABLE_TOKENS & ignored}"
        )

        # (c) not neutralised via per-file-ignores (BLOCKING #1 disable vector).
        per_file = lint.get("per-file-ignores", {})
        leaked = {
            path: sorted(_DISABLE_TOKENS & set(codes))
            for path, codes in per_file.items()
            if _DISABLE_TOKENS & set(codes)
        }
        assert not leaked, (
            "silence-detection codes must not appear in `per-file-ignores` "
            f"(silently disables the §IV gate): {leaked}"
        )
