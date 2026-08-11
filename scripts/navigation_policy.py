"""Navigation policy: shell routes into the filesystem that a Claude tool replaces (#485).

Deliberately separate from `scripts/agent_policy.py`. That module is the *security*
policy shared with Codex, and its `denied_reason()` asserts danger; this one asserts only
that a cheaper route exists. Routing token economy through the security carrier would emit
a false reason in Codex's PreToolUse hook.
"""

from __future__ import annotations


def navigation_hint(command: str) -> str | None:
    """Return an actionable replacement message when a stage reads the filesystem."""
    raise NotImplementedError
