"""Anti-drift guard for the secret-file ignores (#387, #386).

`.env` and `secret.key` carry live credentials (service-account JSON with
`private_key`, API keys, a site password). Before #387 they were ignored only via
`.git/info/exclude`, which is NOT versioned — so a fresh clone, a second machine
or a contributor got the repo unprotected.

A Telethon session file is a credential of the same weight and is covered here
too (#386): `anon.session.encrypted` — the *ciphertext* of a user-account session
— sat in this public repo for two years, and encryption does not make it safe to
publish (rotating the Fernet key cannot un-publish the blob). `secret.key` stays
ignored for the stale local copy of that key, not because anything still writes it.

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
    # No default (#410): `None` would mean broken capture, not "git said nothing."
    # The test stays on stdlib and does NOT import a helper from `scripts/`: this
    # is a repository-state regression, and coupling it to developer scripts
    # would widen its failure surface. Empty stdout at rc=1 ("path is not
    # ignored") is normal.
    assert result.stdout is not None, f"capture failed for `git check-ignore -v {path}`"
    return result.stdout.strip()


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "secret.key",
        "anon.session",
        "anon.session.encrypted",
        "b.session.encrypted",
    ],
)
def test_secret_files_are_ignored_by_tracked_gitignore(path: str) -> None:
    source = _ignore_source(path)
    assert source, f"{path} is not ignored at all — a `git add -A` would commit it"
    assert source.startswith(".gitignore:"), (
        f"{path} is ignored by {source.split(':')[0]!r}, not by the tracked .gitignore — "
        "an unversioned exclude does not protect a fresh clone (#387)"
    )


def test_no_session_credential_is_tracked() -> None:
    """No Telethon session file may be tracked — encrypted or not (#386).

    The ignore rules above only protect a file that does not exist yet; this
    asserts the repo state itself, because the way the credential got published
    was a `git add` of an *encrypted* blob that looked harmless. Re-adding one
    must redden CI, not pass review on the argument "but it's Fernet".
    """
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert tracked.stdout is not None, "capture failed for `git ls-files -z`"
    sessions = [name for name in tracked.stdout.split("\0") if ".session" in name]

    assert not sessions, f"Telethon session credential(s) tracked in the repo: {sessions}"


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
