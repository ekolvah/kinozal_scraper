---
status: "accepted"
date: 2026-08-12
decision-makers: ekolvah
---

# Codex publication authority is an opt-in user rule around a validating wrapper

## Context and Problem Statement

A requested Codex issue delivery must publish an issue branch, create its PR,
and stay active through required checks and the review gate. A generic `git
push` is also source export: approving it broadly can publish a protected ref,
force an unrelated branch, or target another repository. Requiring a new prompt
for every valid fixer head, however, interrupts the already-authorized delivery
workflow.

The decision must make the safe path repeatable without letting a trusted
repository silently grant itself network-mutation authority.

## Decision Drivers

* Issue publication must reach `ready-for-human` after one explicit setup.
* The repository, remote, branch, refspec, hooks, and merge boundary must be
  fixed rather than supplied by the model.
* Unknown GitHub or shell operations must retain the normal approval path.
* Personal configuration and credentials must stay outside the repository.
* The boundary must have deterministic tests and an operator-visible failure.

## Considered Options

* Auto-approve delivery mutations from a repository `PermissionRequest` hook
* Enable unrestricted network or `danger-full-access` for the checkout
* Approve each generated `git push` command independently
* Install an explicit user-layer rule that allows a validating repository wrapper

## Decision Outcome

Choose **an explicit user-layer rule that allows a validating repository
wrapper**. The repository ships `scripts/push_issue_branch.py`,
`scripts/publish_pr_report.py`, and an inactive rule template. The maintainer
reviews and installs that template in the user configuration; the repository
never activates it.

The push wrapper accepts no arguments. It reads `origin`, the current branch,
and worktree status; requires the canonical repository, a clean `issue-N-*`
branch, and then constructs the only push form itself. The PR wrapper also
accepts no arguments and reads only the fixed ignored `.codex/pr-body.md`
workspace path; it derives the issue, title, and matching current-branch PR.
The user rule forbids every direct `git push` and `gh pr merge`, while allowing
the wrappers and named read-only delivery entry points. The existing repository
PreToolUse policy and GitHub branch protection remain independent
defense-in-depth layers.

### Consequences

* Good, because a routine new or fixer head no longer needs a fresh source-export approval.
* Good, because the model cannot choose a remote, destination ref, force flag,
  hook bypass, or merge command through the allowed entry point.
* Good, because the repository cannot activate its own mutation authority.
* Bad, because each maintainer must perform and verify one user-level installation.
* Bad, because Codex rules are still documented as experimental and the
  template may need adjustment after a CLI policy-language change.

### Confirmation

Wrapper unit tests cover the canonical remote, issue branch, clean worktree,
exact generated command, and fixed non-symlink PR report path. Static tests pin the allow/forbid entries, and
`codex execpolicy check` validates the template when the CLI is present. The
shared deny tests cover protected-push argument variants. Required GitHub checks
and `review_gate` remain the final delivery evidence.

## Pros and Cons of the Options

### Auto-approve from a repository PermissionRequest hook

* Good, because no user rule needs installation.
* Bad, because trusting the repository would also let it grant itself remote
  mutation authority after a hook change; that collapses trust and approval
  into one decision.

### Enable unrestricted network or danger-full-access

* Good, because all delivery commands run without prompts.
* Bad, because every subprocess gains capability unrelated to the requested
  repository and branch.
* Bad, because it contradicts the issue's least-privilege requirement.

### Approve every generated push

* Good, because each action remains individually visible.
* Bad, because it interrupts long-running review/fix loops and accumulates
  branch-specific permission history rather than one auditable boundary.

### User rule plus validating wrapper

* Good, because installation is an explicit human decision and the allowed
  command exposes no sensitive arguments.
* Good, because direct push and merge stay forbidden in the same policy file.
* Bad, because wrapper changes require normal code review before the previously
  installed user rule should be trusted again.
