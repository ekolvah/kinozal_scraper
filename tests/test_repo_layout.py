from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Source modules live under src/, never at the repo root. The allowlist is
# intentionally empty: no tracked .py belongs directly in the root tree.
_ALLOWED_ROOT_PY: frozenset[str] = frozenset()


def _tracked_root_py() -> list[str]:
    """Tracked top-level `*.py` files (git, so untracked scraps never count —
    otherwise an untracked root .py would red this guard forever)."""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return sorted(name for name in out.splitlines() if name.endswith(".py") and "/" not in name)


class TestLayout:
    def test_no_tracked_source_py_in_repo_root(self) -> None:
        offenders = set(_tracked_root_py()) - _ALLOWED_ROOT_PY
        assert not offenders, f"source .py must live under src/, not repo root: {sorted(offenders)}"
