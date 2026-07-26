"""Anti-drift guard for the secret-file ignores (#387).

`.env` and `secret.key` carry live credentials (service-account JSON with
`private_key`, API keys, a site password, and the Fernet key that decrypts
`anon.session.encrypted`). Before #387 they were ignored only via
`.git/info/exclude`, which is NOT versioned — so a fresh clone, a second machine
or a contributor got the repo unprotected.

These tests assert the ignore actually comes from the tracked `.gitignore`, so
the protection travels with the repo and cannot be silently dropped by a future
`.gitignore` cleanup. They query real `git check-ignore` rather than parsing the
file: pattern semantics (negation, precedence) are git's, and re-implementing
them in the test would assert our reading of the rules instead of their effect.

NOTE this is not a claim that secrets cannot leak — it is one layer. GitHub
push protection (enabled 2026-07-25) covers provider-shaped tokens server-side;
neither layer catches an arbitrary `PASSWORD=` line, which is why the ignore
must hold.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _ignore_source(path: str) -> str:
    """The file:line rule that decides `path`, or '' when nothing matches.

    `git check-ignore -v` reports the LAST matching pattern — the one git itself
    applies. Deliberately WITHOUT `--no-index`: that flag also hides
    `.git/info/exclude`, so a regression would report "ignored by nothing"
    instead of the actually-useful "ignored by an unversioned exclude", which is
    the exact failure mode #387 is about.
    """
    result = subprocess.run(
        ["git", "check-ignore", "-v", path],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return (result.stdout or "").strip()


@pytest.mark.parametrize("path", [".env", ".env.local", "secret.key"])
def test_secret_files_are_ignored_by_tracked_gitignore(path: str) -> None:
    source = _ignore_source(path)
    assert source, f"{path} is not ignored at all — a `git add -A` would commit it"
    assert source.startswith(".gitignore:"), (
        f"{path} is ignored by {source.split(':')[0]!r}, not by the tracked .gitignore — "
        "an unversioned exclude does not protect a fresh clone (#387)"
    )


def test_env_example_template_stays_committable() -> None:
    """`.env.*` must not swallow a values-free template.

    Without the `!.env.example` negation the omission would be silent: someone
    adds the template, it never appears in `git status`, and the next contributor
    has no list of required variables.
    """
    source = _ignore_source(".env.example")
    assert "!.env.example" in source or not source, (
        f".env.example is ignored by {source!r} — the template must stay committable"
    )
