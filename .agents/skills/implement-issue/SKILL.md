---
name: implement-issue
description: Implement a reviewed GitHub issue in this repository. Use when the user asks to implement, fix, or deliver issue #N after planning; validate the issue contract, follow RED-to-GREEN, open the PR, and process its CI or review findings.
---

# Implement issue

Treat [the agent process](../../../docs/architecture/agent-process.md) as the
workflow contract. This skill is the Codex adapter for the `implementer` and
`fixer` roles; do not replace a missing plan with an invented implementation.

1. Run `python scripts/validate_issue_sections.py <N>`. If it fails, stop and
   direct the task to a `planner`; do not create a branch or edit production
   code.
2. Read the issue and the repository areas it names. Create the branch only
   with `python scripts/issue_branch.py <N>`.
3. Write the exact tests named in `## Test plan`, then run
   `python scripts/check_red.py <test paths>`. Commit successful RED evidence
   as `test: failing tests for #<N>`. A signature-only stub is permitted only
   when necessary for a test to import.
4. Implement `## Implementation outline`, running focused tests until they are
   green. Update `## Docs to update` and any ADR named by the issue.
5. Run `python scripts/ci_check.py` once in the foreground. Fix root causes,
   not symptoms.
6. Create the PR with `python scripts/open_pr.py`, using the repository
   template. Fill `## Agent record` with your implementation identity, any
   reviewer/fixer identities, and the actual CI evidence.
7. Watch PR checks. Investigate and fix up to three iterations that reduce the
   failure count. Process one review pass in a separate commit, then hand off
   the merge decision to a human.

Never bypass hooks, force-push, push to `main`, self-merge, or use an agent
statement in place of a required script or GitHub check.

<!--

## Structuring This Skill

[TODO: Choose the structure that best fits this skill's purpose. Common patterns:

**1. Workflow-Based** (best for sequential processes)
- Works well when there are clear step-by-step procedures
- Example: DOCX skill with "Workflow Decision Tree" -> "Reading" -> "Creating" -> "Editing"
- Structure: ## Overview -> ## Workflow Decision Tree -> ## Step 1 -> ## Step 2...

**2. Task-Based** (best for tool collections)
- Works well when the skill offers different operations/capabilities
- Example: PDF skill with "Quick Start" -> "Merge PDFs" -> "Split PDFs" -> "Extract Text"
- Structure: ## Overview -> ## Quick Start -> ## Task Category 1 -> ## Task Category 2...

**3. Reference/Guidelines** (best for standards or specifications)
- Works well for brand guidelines, coding standards, or requirements
- Example: Brand styling with "Brand Guidelines" -> "Colors" -> "Typography" -> "Features"
- Structure: ## Overview -> ## Guidelines -> ## Specifications -> ## Usage...

**4. Capabilities-Based** (best for integrated systems)
- Works well when the skill provides multiple interrelated features
- Example: Product Management with "Core Capabilities" -> numbered capability list
- Structure: ## Overview -> ## Core Capabilities -> ### 1. Feature -> ### 2. Feature...

Patterns can be mixed and matched as needed. Most skills combine patterns (e.g., start with task-based, add workflow for complex operations).

Delete this entire "Structuring This Skill" section when done - it's just guidance.]

## [TODO: Replace with the first main section based on chosen structure]

[TODO: Add content here. See examples in existing skills:
- Code samples for technical skills
- Decision trees for complex workflows
- Concrete examples with realistic user requests
- References to scripts/templates/references as needed]

## Resources (optional)

Create only the resource directories this skill actually needs. Delete this section if no resources are required.

### scripts/
Executable code (Python/Bash/etc.) that can be run directly to perform specific operations.

**Examples from other skills:**
- PDF skill: `fill_fillable_fields.py`, `extract_form_field_info.py` - utilities for PDF manipulation
- DOCX skill: `document.py`, `utilities.py` - Python modules for document processing

**Appropriate for:** Python scripts, shell scripts, or any executable code that performs automation, data processing, or specific operations.

**Note:** Scripts may be executed without loading into context, but can still be read by Codex for patching or environment adjustments.

### references/
Documentation and reference material intended to be loaded into context to inform Codex's process and thinking.

**Examples from other skills:**
- Product management: `communication.md`, `context_building.md` - detailed workflow guides
- BigQuery: API reference documentation and query examples
- Finance: Schema documentation, company policies

**Appropriate for:** In-depth documentation, API references, database schemas, comprehensive guides, or any detailed information that Codex should reference while working.

### assets/
Files not intended to be loaded into context, but rather used within the output Codex produces.

**Examples from other skills:**
- Brand styling: PowerPoint template files (.pptx), logo files
- Frontend builder: HTML/React boilerplate project directories
- Typography: Font files (.ttf, .woff2)

**Appropriate for:** Templates, boilerplate code, document templates, images, icons, fonts, or any files meant to be copied or used in the final output.

---

**Not every skill requires all three types of resources.**
-->
