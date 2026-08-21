"""Navigation policy: routes into the filesystem whose cost a cheaper call avoids.

Two routes, one policy. A shell command that reads a file has a tool that replaces it
(`navigation_hint`); a `Read` that pulls a whole large file into context has a cheaper
form of itself — a `Grep` or a slice (`read_budget_hint`). Both refusals name the
replacement, and both are advisory about cost, never about safety.

Deliberately separate from `scripts/agent_policy.py`. That module is the *security*
policy shared with Codex, and its `denied_reason()` asserts danger; this one asserts only
that a cheaper route exists. Routing token economy through the security carrier would emit
a false reason in Codex's PreToolUse hook.

Why a parser and not a `permissions.deny` pattern. One utility lives in two roles — reading
the filesystem (a tool replaces it) and trimming another command's output in a pipe (nothing
does) — and a static prefix pattern cannot tell them apart. Measured over the transcript
corpus, `head`/`tail` are 95%+ pipe stages while `grep` is 65% filesystem reads, so the
static list had to drop `head`/`tail` entirely and could only reach `grep -r`. Counting
operands separates the roles, which is what lets the policy cover the file-reading forms of
`grep`/`head`/`tail` and still leave every pipe stage alone. The second reason is §IV:
`permissions.deny` denies without a message, so the agent learns nothing from the refusal —
here the refusal names the replacement call.

Fail-open by construction: an unparseable command yields no decision. This is a token-economy
ratchet, not a sandbox, and blocking real work on a lexer limit would cost more than the
route it guards.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

_SEPARATORS = frozenset({"|", "||", "&&", ";", "&", "|&", "\n"})
_REDIRECTS = frozenset({">", ">>", ">|", ">&", "&>", "&>>", "<", "<<", "<<<", "<&"})
# Wrappers Claude Code itself strips before matching a permission rule; mirrored so a
# wrapped command is classified by what it actually runs.
_WRAPPERS = frozenset(
    {"timeout", "time", "nice", "nohup", "stdbuf", "command", "builtin", "noglob", "env", "xargs"}
)
_SHELLS = frozenset({"sh", "bash", "zsh"})
_MAX_DEPTH = 3

# Flags that consume the next token. Without this, `git log | grep -A 3 fix` would read `3`
# as a file operand and deny a legitimate pipe stage.
_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "grep": frozenset({"-e", "-m", "-A", "-B", "-C", "-d", "-f", "--regexp", "--include"}),
    "head": frozenset({"-n", "-c"}),
    "tail": frozenset({"-n", "-c"}),
    "sed": frozenset({"-e", "-f"}),
}

_BOUNDARY = "Trimming another command's output stays allowed (`cmd | head -40`)."


def _basename(token: str) -> str:
    """`/usr/bin/grep` and `grep.exe` both classify as `grep`."""
    name = token.replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-4] if name.endswith(".exe") else name


def _strip_wrappers(tokens: list[str]) -> list[str]:
    while tokens and _basename(tokens[0]) in _WRAPPERS:
        tokens = tokens[1:]
        while tokens and (tokens[0].startswith("-") or tokens[0].isdigit()):
            tokens = tokens[1:]
    return tokens


@dataclass(frozen=True)
class Stage:
    """One command between shell separators, split into the parts the rules read."""

    name: str
    flags: list[str]
    operands: list[str]
    heredoc: bool

    @property
    def short_chars(self) -> set[str]:
        """Letters of the clustered short flags, so `-rn` and `-n -r` read the same."""
        return {char for flag in self.flags if not flag.startswith("--") for char in flag[1:]}


def _split_arguments(name: str, rest: list[str]) -> Stage:
    """Partition a stage's arguments into flags, operands, and a heredoc marker.

    Redirection operators and their targets are not operands: `ls > out.txt` is still a
    bare `ls`, and `cat > f <<'EOF'` is a write, not a read.
    """
    value_flags = _VALUE_FLAGS.get(name, frozenset())
    flags: list[str] = []
    operands: list[str] = []
    heredoc = False
    index = 0
    while index < len(rest):
        token = rest[index]
        # `2>/dev/null` lexes as `2`, `>`, `/dev/null`; the fd number is not an operand.
        if token.isdigit() and rest[index + 1 : index + 2] and rest[index + 1] in _REDIRECTS:
            index += 1
            continue
        if token in _REDIRECTS:
            heredoc = heredoc or token.startswith("<<")
            index += 2
            continue
        if token.startswith("-") and token != "-":
            flags.append(token)
            index += 2 if token in value_flags else 1
            continue
        operands.append(token)
        index += 1
    return Stage(name=name, flags=flags, operands=operands, heredoc=heredoc)


def _stage_hint(tokens: list[str], depth: int) -> str | None:
    tokens = _strip_wrappers(tokens)
    if not tokens:
        return None
    name = _basename(tokens[0])
    if name in _SHELLS and depth < _MAX_DEPTH:
        # `sh -c "..."` is NOT unwrapped by Claude Code's permission matcher — it was the
        # documented hole in the static list. Recursing closes it.
        if "-c" in tokens[1:]:
            position = tokens.index("-c", 1)
            inner = tokens[position + 1] if position + 1 < len(tokens) else ""
            return _hint(inner, depth + 1)
        return None
    if name not in _RULES:
        return None
    return _RULES[name](_split_arguments(name, tokens[1:]))


def _hint_ls(stage: Stage) -> str | None:
    target = (stage.operands[0] if stage.operands else ".").rstrip("/")
    return f'`{stage.name}` lists the filesystem — use Glob (e.g. Glob("{target}/**/*.py")).'


def _hint_cat(stage: Stage) -> str | None:
    if stage.heredoc:
        return "`cat > file <<EOF` writes a file — use Write, or Edit for a partial change."
    if not stage.operands:
        return None  # `cmd | cat` reads stdin, not the filesystem.
    return (
        f'`cat` reads the whole file into context — use Read("{stage.operands[0]}"), '
        f"with offset/limit when only a fragment is needed."
    )


def _hint_grep(stage: Stage) -> str | None:
    recursive = bool({"r", "R"} & stage.short_chars) or "--recursive" in stage.flags
    if not recursive and len(stage.operands) < 2:
        return None  # a pipe stage: pattern only, no path to read.
    path = stage.operands[1] if len(stage.operands) > 1 else "."
    return (
        f'`grep` over the filesystem — use Grep(pattern=..., path="{path}"), which is '
        f"ripgrep-backed and supports glob/type filters, context modes and head_limit. "
        f"{_BOUNDARY}"
    )


def _hint_sed(stage: Stage) -> str | None:
    if "n" not in stage.short_chars or len(stage.operands) < 2:
        return None  # `cmd | sed -n '1,20p'` trims a pipe; only a file operand is navigation.
    return (
        f'`sed -n` prints a file range — use Read("{stage.operands[1]}") with offset/limit. '
        f"{_BOUNDARY}"
    )


def _hint_head_tail(stage: Stage) -> str | None:
    if not stage.operands:
        return None  # the dominant role: trimming a pipe.
    return (
        f'`{stage.name}` on a file — use Read("{stage.operands[0]}") with offset/limit. {_BOUNDARY}'
    )


_RULES = {
    "ls": _hint_ls,
    "find": _hint_ls,
    "cat": _hint_cat,
    "grep": _hint_grep,
    "sed": _hint_sed,
    "head": _hint_head_tail,
    "tail": _hint_head_tail,
}


def _hint(command: str, depth: int) -> str | None:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None  # unbalanced quote: a lexer limit, not a violation.
    stage: list[str] = []
    for token in [*tokens, "\n"]:
        if token in _SEPARATORS:
            hint = _stage_hint(stage, depth)
            if hint is not None:
                return hint
            stage = []
            continue
        stage.append(token)
    return None


def navigation_hint(command: str) -> str | None:
    """Return an actionable replacement message when a stage reads the filesystem."""
    if not isinstance(command, str) or not command.strip():
        return None
    hint = _hint(command, depth=0)
    return None if hint is None else f"{hint} Repository navigation goes through tools."


# The budget, in bytes of the slice `Read` will actually return.
#
# Derived from a measurement over the 203 tracked files on 2026-08-15, not from taste. The
# text corpus ran p50/p75/p90/p95 = 6457 / 13311 / 25694 / 41000 bytes; `principles.md` (16108)
# and `testing.md` (24113) are the two documents this repository orders read *whole*, and the
# smallest driver of the incident below was `tests/test_agent_orchestrator.py` (31410). 28000
# is the corridor between them: above everything prescribed whole, below every driver. A
# 16000 threshold was rejected for missing `principles.md` by 108 bytes — pure friction on the
# hottest agent route, saving nothing, since that content is prescribed in full.
#
# Tuning direction is DOWNWARD ONLY, and only on observation. A ratchet that starts loose and
# tightens on data costs less than one that starts tight and gets routed around.
_READ_BUDGET_BYTES = 28_000

# Formats where `offset`/`limit` are not line offsets into text, so the replacement this
# policy names would not apply.
_UNSLICEABLE = frozenset({".pdf", ".ipynb", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})


def _lines_that_fit(lines: list[bytes], start: int) -> int:
    """How many lines from `start` stay inside the budget — at least one, so the message
    never hands back an empty slice."""
    total = 0
    fits = 0
    for line in lines[start:]:
        total += len(line)
        if total > _READ_BUDGET_BYTES:
            break
        fits += 1
    return max(fits, 1)


def read_budget_hint(file_path: object, offset: object = None, limit: object = None) -> str | None:
    """Return a replacement message when a `Read` slice exceeds the byte budget.

    Why `Read` needs a gate of its own. The shell route into the filesystem was closed, but
    whole-file `Read` stayed ungated and turned out to be the expensive one. A measured run
    took 53 steps before a single line of code, with context growing from 29k to 166k, driven by
    ten whole-file reads. The harm is not re-sending those bytes — that goes through
    `cache_read` — it is the chain **ceiling → compaction → reading the same files again**;
    the session paid for its five largest files twice.

    Why the budget is on bytes and not on the presence of `limit`. `limit` counts LINES and
    `Read` truncates at 2000 of them, while the longest file here is 1196 lines: an explicit
    `limit=2000` returns byte-identical content to a whole-file read for every file in this
    repository. So the measurement is the slice `[offset, offset + limit)` as it lands on
    disk, computed here — the hook's own bytes never enter the agent's context.

    The token figure is APPROXIMATE in both directions. `bytes / 4` holds for Latin text; for
    Cyrillic, UTF-8 spends ~2 bytes per character at ~1 token per character, so the byte count
    overstates the size (the refusal fires early, on the policy's side) while the printed
    estimate understates the tokens. Neither number is exact and the message says so.

    Fail-open on every axis it cannot measure — a non-string path, a missing file, a
    directory, bytes that are not UTF-8, a format where slicing is meaningless. This policy
    claims only that a cheaper route exists, so anything unmeasurable is "no opinion".

    The hook is STATELESS: after a compaction, a legitimately large read pays the
    refusal-and-retry again. That is the accepted price of not carrying session state into a
    `PreToolUse` hook, recorded here so it is not reopened as a bug.

    Mechanics confirmed against the documentation, not guessed: `PreToolUse` matches ANY tool
    name, not just `Bash`, and `permissionDecisionReason` is delivered to the model —
    https://code.claude.com/docs/en/hooks

    Revision condition: the maintainer watches context growth before the first edit through
    the existing `scripts/token_trend.py`. If refusals fire and that growth does not fall,
    tighten the threshold or revert the rule; do not build a second measurer.
    """
    if not isinstance(file_path, str):
        return None
    if (offset is not None and not isinstance(offset, int)) or (
        limit is not None and not isinstance(limit, int)
    ):
        return None
    path = Path(file_path)
    try:
        if not path.is_file() or path.suffix.lower() in _UNSLICEABLE:
            return None
        raw = path.read_bytes()
        raw.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    lines = raw.splitlines(keepends=True)
    start = max(offset - 1, 0) if offset is not None else 0
    window = lines[start:] if limit is None else lines[start : start + max(limit, 0)]
    size = sum(len(line) for line in window)
    if size <= _READ_BUDGET_BYTES:
        return None
    return (
        f'Read("{file_path}") returns {size} bytes (~{size // 4} tokens, approximate) — over '
        f"the {_READ_BUDGET_BYTES}-byte budget. Grep for the symbol you need, or read the "
        f"largest slice that fits from here: offset={start + 1}, "
        f"limit={_lines_that_fit(lines, start)}. "
        "Rewriting the file whole? Read it in slices first — never Write over bytes you have "
        "not seen. Reading is budgeted like shell navigation."
    )
