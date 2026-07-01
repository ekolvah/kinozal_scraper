"""Anti-drift guard for the §II import-linter contracts (#234).

§II (Protocol Boundaries + DI) is partly machine-enforced via `import-linter`
contracts in `.importlinter`:

- `pipeline-layers` (layers) pins the dependency *direction* — orchestrators
  (`*_pipeline`) may import the service adapters and the shared core, never the
  reverse, and no orchestrator/adapter imports a sibling.
- `adapter-no-auth` (forbidden) encodes the §II hard rule "implementations
  receive ready clients, not credentials — auth lives in the caller": the three
  adapter modules must not import `crypto`/`kinozal_auth`.

These tests pin *enforcement*, not mere declaration. A future agent must not be
able to quietly gut a contract while keeping its name (drop modules from
`source_modules`, remove `crypto` from `forbidden_modules`, …) — so the meta-
test asserts the load-bearing fields, not just that the contract names exist
(#234 architect-review SHOULD-FIX #1). Mirrors `test_ruff_silence_rules.py`.
"""

from __future__ import annotations

import configparser
from pathlib import Path

from scripts.ci_check import CHECKS

_REPO = Path(__file__).resolve().parents[1]
_IMPORTLINTER = _REPO / ".importlinter"

# Fully-qualified names: since #238 the code is the `kinozal_scraper` package,
# so the contracts (and these guards) reference `kinozal_scraper.<module>`.
_PKG = "kinozal_scraper"
_ORCHESTRATORS = {
    f"{_PKG}.kinozal_pipeline",
    f"{_PKG}.json_pipeline",
    f"{_PKG}.steam_pipeline",
    f"{_PKG}.github_trending_pipeline",
    f"{_PKG}.events_pipeline",
}
_ADAPTERS = {
    f"{_PKG}.sheets_storage",
    f"{_PKG}.telegram_notifier",
    f"{_PKG}.gemini_enricher",
}
_AUTH = {f"{_PKG}.crypto", f"{_PKG}.kinozal_auth"}


def _config() -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    cp.read(_IMPORTLINTER, encoding="utf-8")
    return cp


class TestImportContracts:
    def test_imports_check_registered(self) -> None:
        """The gate must be undeletable from the CHECKS registry."""
        assert "imports" in CHECKS, (
            "ci_check.CHECKS lost the 'imports' entry — the §II import-linter "
            "gate would no longer run (#234)."
        )

    def test_contracts_have_load_bearing_fields(self) -> None:
        """A contract kept by name but gutted of its modules is still a hole."""
        assert _IMPORTLINTER.exists(), f"{_IMPORTLINTER} is missing (#234)"
        cp = _config()

        forbidden_sec = cp["importlinter:contract:adapter-no-auth"]
        source = set(forbidden_sec["source_modules"].split())
        forbidden = set(forbidden_sec["forbidden_modules"].split())
        assert source >= _ADAPTERS, (
            f"adapter-no-auth.source_modules dropped adapters: {_ADAPTERS - source}"
        )
        assert forbidden >= _AUTH, (
            f"adapter-no-auth.forbidden_modules dropped auth modules: {_AUTH - forbidden}"
        )

        layers_sec = cp["importlinter:contract:pipeline-layers"]
        tokens = set(layers_sec["layers"].replace("|", " ").split())
        missing = (_ORCHESTRATORS | _ADAPTERS) - tokens
        assert not missing, f"pipeline-layers dropped layered modules: {missing}"

    def test_contracts_currently_kept(self) -> None:
        """The live gate: contracts hold against the real import graph.

        Delegates to `check_imports()` so the import-linter API surface lives in
        exactly one place (the gate owns contract evaluation — architect-review
        NICE). Imported inside the function so the RED run (before the dev-dep /
        the function exist) fails per-test rather than erroring at collection.
        """
        from scripts.ci_check import check_imports

        check_imports()  # raises SystemExit(1) if any contract is broken
