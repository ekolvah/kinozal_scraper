"""Publish a carrier's review summary to the PR without giving the model a token.

`anthropics/claude-code-action` owns an MCP comment tool, so its findings reach the
PR by themselves. `openai/codex-action` has no such channel: it only runs `codex
exec` and returns its final message. Handing that carrier a `pull-requests: write`
token through `gh` inside a model-driven shell would widen authority far past what
the first carrier has — the model's context holds an untrusted diff and PR body.

So the carrier returns its summary as *data* (a field of the schema-validated
outcome), and this deterministic step publishes it. The body never reaches a shell:
it travels as a JSON file handed to `gh api --input`.
"""

from __future__ import annotations

from collections.abc import Sequence

MARKER = "<!-- agent-review-summary: {producer} -->"


def summary_body(payload: str, producer: str, head_sha: str) -> str:
    """Return the comment body for a carrier's structured outcome."""
    raise NotImplementedError


def main(argv: Sequence[str] | None = None) -> None:
    """Create or update this carrier's sticky summary comment."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
