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

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

MARKER = "<!-- agent-review-summary: {producer} -->"


def summary_body(payload: str, producer: str, head_sha: str) -> str:
    """Return the comment body for a carrier's structured outcome.

    A carrier that reviewed but returned nothing to say is the §IV failure itself:
    an empty comment is indistinguishable from a review that found nothing, so the
    absent summary is raised rather than published.
    """
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"review outcome carries no summary: invalid JSON ({exc})") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("review outcome carries no summary: payload is not an object")
    summary = parsed.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("review outcome carries no summary field")
    outcome = parsed.get("outcome")
    return (
        f"{MARKER.format(producer=producer)}\n"
        f"**{producer}** — outcome `{outcome}` for head `{head_sha}`\n\n"
        f"{summary}\n"
    )


def _gh_json(args: list[str]) -> object:
    """Run `gh api` and return its parsed payload; every failure is loud."""
    result = subprocess.run(
        ["gh", "api", *args], text=True, capture_output=True, encoding="utf-8", check=False
    )
    if result.stdout is None or result.stderr is None:
        # Broken capture, not an empty answer (#364/#410). Read as "no comments"
        # it would silently append a new summary on every re-run.
        print(
            f"error: gh api capture failed (rc={result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if result.returncode != 0:
        print(f"error: gh api {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(1)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"error: gh api {' '.join(args)} returned invalid JSON: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _existing_comment_id(repository: str, pr_number: str, marker: str) -> int | None:
    payload = _gh_json(
        [f"repos/{repository}/issues/{pr_number}/comments?per_page=100", "--paginate", "--slurp"]
    )
    if not isinstance(payload, list):
        print("error: comment listing returned an unexpected payload shape", file=sys.stderr)
        raise SystemExit(2)
    pages = payload if all(isinstance(page, list) for page in payload) else [payload]
    for comment in (record for page in pages for record in page):
        if not isinstance(comment, Mapping):
            print("error: comment listing returned an unexpected payload shape", file=sys.stderr)
            raise SystemExit(2)
        if marker in str(comment.get("body", "")):
            return int(comment["id"])
    return None


def main(argv: Sequence[str] | None = None) -> None:
    """Create or update this carrier's sticky summary comment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outcome", required=True, help="the carrier's structured outcome JSON")
    parser.add_argument("--producer", required=True, help="the carrier this summary belongs to")
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--pr", required=True, help="pull-request number")
    parser.add_argument("--head-sha", required=True, help="the revision the findings cover")
    args = parser.parse_args(argv)

    try:
        body = summary_body(args.outcome, args.producer, args.head_sha)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    marker = MARKER.format(producer=args.producer)
    comment_id = _existing_comment_id(args.repo, args.pr, marker)
    endpoint = (
        f"repos/{args.repo}/issues/comments/{comment_id}"
        if comment_id is not None
        else f"repos/{args.repo}/issues/{args.pr}/comments"
    )
    method = "PATCH" if comment_id is not None else "POST"

    with tempfile.TemporaryDirectory() as tmp:
        request = Path(tmp) / "comment.json"
        request.write_text(json.dumps({"body": body}, ensure_ascii=False), encoding="utf-8")
        _gh_json([endpoint, "--method", method, "--input", str(request)])
    print(f"ok: published {args.producer} review summary ({method} {endpoint})")


if __name__ == "__main__":
    main()
