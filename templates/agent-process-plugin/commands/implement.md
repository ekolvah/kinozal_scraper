---
description: Deliver a planned issue to a PR — branch, RED→GREEN, docs, CI gate, and review/fix loop
argument-hint: <issue-number>
---

# /agent-process:implement N — deliver an issue to a PR

Claude adapter for the `implementer` and `fixer` roles. The steps, gates, exit codes, and loop
termination rule are canonical in
Agent development process §Deterministic Delivery Flow
and
Agent development process §Review Gate Verdicts;
and are not copied here. This file contains only harness-specific material.

`$ARGUMENTS` = issue number.

0. If the session continues from a previous task (an issue/PR already reached hand-off), ask
   the user to run `/compact` before starting. You cannot invoke it; it is a built-in CLI command.
1. Follow the canonical delivery flow. Do not invent a plan: a failing
   `validate_issue_sections.py` returns work to the `planner`; it is not a reason to complete the plan yourself.
2. Edit files with `Edit`/`Write`, not a heredoc script (Mindset).
3. Right after the canonical flow's RED commit, apply the RED→GREEN boundary recipe in
   Mindset (pointer only, do not restate it here).
4. `ci_check.py` and `git push` take minutes — make **one foreground invocation with an increased `timeout`**,
   with no background execution or polling loop (timings and pitfalls: `CLAUDE.md` §Environment).
5. The **exit code of `python -m scripts.review_gate <PR>`** ends the loop, not your reading of
   findings. Actions for each verdict are in §Review-gate verdicts; do not copy that table here.
6. On completion, provide the PR link, the gate verdict, and an explicit “merge is your step”.
