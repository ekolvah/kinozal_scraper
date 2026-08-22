---
description: Structure a GitHub issue body into its required sections before hand-off to the implementer
argument-hint: <issue-number>
---

# /agent-process:plan N — structure an issue body

Claude adapter for the `planner` role. The steps, limits, and section contracts are canonical in
Agent development process §Planner Runbook;
and are not copied here. This file contains only harness-specific material.

`$ARGUMENTS` = issue number.

0. If the session continues from a previous task (an issue/PR already reached hand-off), ask
   the user to run `/compact` before starting. You cannot invoke it; it is a built-in CLI command.
1. Follow the canonical planner runbook, including the discovery branch the issue's change class
   selects; `Read`/`Grep` supply the repository-context source only, and `WebSearch`/`WebFetch` the
   outside-the-repository one.
2. The **`discovery` subagent** performs the observation runbook step 2 delegates
   (persona: discovery), not the main session; its contract is
   Agent development process §Discovery Runbook.
   Record the block it returns unchanged. Its provenance line, the first line of that
   block: `discovery: Claude discovery subagent`.
3. The **`architect-reviewer` subagent** performs the architect review in runbook step 3
   (persona: architect-reviewer), not the main session. Its provenance
   line, the first line of `## Architect review`: `reviewer: Claude architect-reviewer subagent`.
4. Write the body with `gh issue edit $ARGUMENTS --body "<complete issue contract>"`.
5. On completion, provide the issue link and hand off to the `implementer`. The user selects the entry point: Codex
   `$implement-issue #$ARGUMENTS` (the repository default) or `/agent-process:implement $ARGUMENTS` in this session.
