from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Source modules live under src/, never at the repo root. The allowlist is
# intentionally empty: no tracked .py belongs directly in the root tree.
_ALLOWED_ROOT_PY: frozenset[str] = frozenset()


def _tracked_py() -> list[str]:
    """All tracked `*.py` paths (git, so untracked scraps never count —
    otherwise an untracked .py would red these guards forever)."""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return sorted(name for name in out.splitlines() if name.endswith(".py"))


def _tracked_root_py() -> list[str]:
    return sorted(name for name in _tracked_py() if "/" not in name)


class TestLayout:
    def test_no_tracked_source_py_in_repo_root(self) -> None:
        offenders = set(_tracked_root_py()) - _ALLOWED_ROOT_PY
        assert not offenders, f"source .py must live under src/, not repo root: {sorted(offenders)}"

    def test_no_flat_source_py_under_src(self) -> None:
        """Source lives in the `kinozal_scraper` package, not flat under src/ (#237).

        After the package migration every source module is
        `src/kinozal_scraper/<name>.py`; a bare `src/<name>.py` would be an
        un-packaged module invisible to package-level tooling (import-linter,
        native mypy resolution). Guards against drift back to the flat layout.
        """
        offenders = sorted(
            name
            for name in _tracked_py()
            if name.startswith("src/") and "/" not in name[len("src/") :]
        )
        assert not offenders, (
            f"source .py must live under src/kinozal_scraper/, not flat src/: {offenders}"
        )
