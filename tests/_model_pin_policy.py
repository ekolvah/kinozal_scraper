"""Single home of the "model is unpinned" deny-list for both guards (#374, #392).

There is one policy (`docs/architecture/ci-agent-review.md` §Model pinning) and two surfaces:
the `--model` CLI flag inside `claude_args` in
`.github/workflows/agent-review.yml`, and subagent frontmatter in
`.claude/agents/*.md`.

**Why one set rather than two.** This is a **deny-list**, not an allow-list: its
union is strictly more conservative because it can reject an extra value but
cannot let one through. A value unavailable on one surface costs nothing to ban
there as well. The sets previously lived as copies in two files and **already
drifted**: `fable` existed in the frontmatter set but not the CLI set, so
`--model fable` passed the cloud guard even though the action documentation says
the flag accepts the same values.
"""

from __future__ import annotations

# Anything the repository does NOT resolve: aliases move to a new generation
# with upstream, floating pointers move with its default, and `inherit` moves
# with the session model. Each lets gate quality change without a line in the
# diff (§IV: a change becomes indistinguishable from no change).
UNPINNED_MODEL_VALUES = frozenset(
    {
        "opus",
        "sonnet",
        "haiku",
        "fable",
        "default",
        "latest",
        "inherit",
    }
)
