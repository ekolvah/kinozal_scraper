# Agent development process

**Question this document answers:** Which parts of the development process this repository
owns on top of the agent-process plugin.

The process is the installed agent-process plugin's `agent-process` skill
([ADR-0013](../adr/0013-adopt-agent-process-plugin-v2.md)): `/opsx:propose` writes an OpenSpec
change and obtains the plugin's architect review, `agent-process start_change <change>` creates
the linked branch, `/opsx:apply` implements the change test-first (`agent-process check_red`),
`agent-process archive_change <change>` archives it, `gh pr create` opens the PR, and
`agent-process wait_for_pr <PR>` waits for its checks and review threads. Its steps, gates, and
review contract are the plugin's and are not restated here. The merge gate is the repository
ruleset ([`ci-branch-protection.md`](ci-branch-protection.md)).

This document holds only what the plugin does not: the repository's governance conventions.

## Governance conventions

1. Keep one PR to one logical unit. A temporary CI unblock for an unrelated failure may
   accompany the blocked change only when it has a tracked follow-up for the root cause.
2. Assign exactly one type label when creating an issue: `bug` for broken behaviour; then
   `perf` / `security` / `enhancement` for user-visible work; otherwise `refactor`, `testing`,
   `ci`, `documentation`, or `chore` by the changed area. Non-type labels are outside this
   taxonomy.
3. Ask the user for issue priority, then set the GitHub Project field with
   `python scripts/set_issue_priority.py <N> <High|Medium|Low>`. Propose High for user-facing
   bugs and development-process work, Medium for agentic capability work outside the process,
   and Low otherwise; name the rule used.
4. If a `requirements*.in` file changes, run `pip-compile` for its matching lockfile in the
   same commit.
5. Do not push to `main`, force-push, bypass hooks, or merge your own PR; the maintainer merges.
   Local agent hooks are defense in depth; the ruleset is authoritative.
6. Trivial non-behavioural one-line changes may skip the change workflow only with the explicit
   rationale recorded in the issue or PR.
