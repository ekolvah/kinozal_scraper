"""Integration guard for stable Ruff first-party import classification (#440).

With the top-level package already present, Ruff used to infer unresolved
``scripts.*`` and ``kinozal_scraper.*`` leaf imports as third-party. Creating
the leaf module later changed the verdict for an unchanged test file, so a warm
local cache could disagree with cold CI. These tests exercise the real Ruff
binary and require both project namespaces to stay first-party throughout.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO / "pyproject.toml"
_MISSING_MODULE = "issue_440_future_module"


def _run_ruff(project: Path, candidate: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--select",
            "I001",
            "--config",
            str(_PYPROJECT),
            str(candidate),
        ],
        cwd=project,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def _assert_stable_first_party(tmp_path: Path, namespace: str, package_dir: Path) -> None:
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    candidate = tests_dir / "test_candidate.py"
    candidate.write_text(
        f"import pytest\nfrom {namespace}.{_MISSING_MODULE} import VALUE\n",
        encoding="utf-8",
    )

    before = _run_ruff(tmp_path, candidate)

    (package_dir / f"{_MISSING_MODULE}.py").write_text("VALUE = True\n", encoding="utf-8")
    after = _run_ruff(tmp_path, candidate)

    for phase, result in (("before", before), ("after", after)):
        assert result.stdout is not None
        assert result.stderr is not None
        assert result.returncode == 1, (
            f"{namespace} must be first-party {phase} the leaf module exists; "
            f"stdout={result.stdout!r}; stderr={result.stderr!r}"
        )
        assert "I001" in result.stdout


class TestStableFirstPartyClassification:
    def test_scripts_import_is_first_party_before_module_exists(self, tmp_path: Path) -> None:
        _assert_stable_first_party(tmp_path, "scripts", tmp_path / "scripts")

    def test_package_import_is_first_party_before_module_exists(self, tmp_path: Path) -> None:
        _assert_stable_first_party(
            tmp_path,
            "kinozal_scraper",
            tmp_path / "src" / "kinozal_scraper",
        )
