---
name: dry-walkthrough
uses_capabilities: []
description: Plan validation executor. ALWAYS invoke this skill when instructed to validate or dry-walkthrough a plan. Do
  not read the plan or trace changes directly — use this skill first to load the validation workflow.
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''🔎 [SKILL: dry-walkthrough] Validating plan...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: delegated-worker
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: delegated-worker
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
  child_model_policies:
  - role: delegated-worker
    model_class: sonnet
---

# Dry Walkthrough Skill

Validate a proposed implementation plan by performing a dry walkthrough of each change without implementing. Fix issues directly in the plan and report what changed to the terminal.

## Key Principle

The plan file must remain a **clean, self-contained implementation instruction set**. No gap analysis, no commentary, no "issues found" sections in the plan itself. All reporting goes to terminal output.

**Your role is technical validation, not strategic decision-making.** Fix factual inaccuracies (wrong file paths, nonexistent functions, incorrect line numbers). Preserve all goals and scope.

## When to Use

- User says "dry walkthrough", "drywalkthrough", "dry walk", "dry run"
- User wants to "validate plan" or "check plan"
- User says "before implementing" and wants verification
- After creating a plan, before implementation

## Arguments

- `plan_path` — Required absolute path to the exact plan file to validate.
- `issue_url` (optional) — GitHub issue URL consumed by Step 4.6.
- `review_path` (optional) — Exact review-approach report whose accepted
  recommendations must be reflected by the plan.
- `audit_cycle_path` (optional) — Exact authority supplied to the server-side
  `audit_cycle_inventory` input preflight.
- `plan_disposition_path` (optional) — Exact `PlanDispositionReport` paired with the
  authority and plan by input preflight.

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Implement any part of the plan
- Add backward compatibility to the plan
- Add fallback mechanisms
- Write gap analysis or commentary INTO the plan file
- Add a rollback plan
- Add deprecation notes, stubs, code, warnings
- Include alternative approaches that will not be part of implementation in plan
- Remove or defer goals or phases from the plan
- Reduce the plan's scope to a "simpler fix" - the plan defines the problem scope, not you
- Consider effort as a reason for choosing one approach over another
- Detach child delegations instead of joining them (joining every child is required)
- Write plan content, corrections, or the verification marker to any file other than the original plan file path provided as input. If the Edit tool is denied on the plan file, do NOT create a copy elsewhere — output a failure message instead.
- Start independent child delegations sequentially
- Discover a latest plan, audit cycle, inventory, or disposition report
- Open audit-cycle artifacts directly in Step 4.7 or reinterpret the verified preflight result

**ALWAYS:**
- Keep the plan as clean implementation instructions only (information/background helpful to implementation is okay)
- Spawn all subagents via `child delegation under the declared `sonnet` model-class policy`
- Report all findings to terminal output (your response text)
- Fix issues by directly updating the plan content
- Verify assumptions against actual codebase
- Remove deprecation code/notes and rollback mechanisms
- Make sure the plan includes warning against using the codebase as a notepad with useless comments
- Prefer the long-term health of the project — and the smallest design that secures it. Proportionality is part of long-term health: every added line must earn its maintenance cost
- Start all independent child delegations before awaiting any result to maximize concurrency

## Context Limit Behavior

When context is exhausted mid-execution, plan file edits may be partially applied.
The recipe routes to `on_context_limit` (typically `register_clone_failure` or a
restart step), abandoning the partial walkthrough.

This skill modifies only the plan file (not source code), so partial edits have
limited blast radius. The downstream step will restart the walkthrough on retry.

## Dry Walkthrough Workflow

### Step 1: Load the Plan

Read only the exact `plan_path` supplied by the caller. If it is absent, stop with an input
error; never search `{{AUTOSKILLIT_TEMP}}` for a recent or singleton artifact.
When `review_path` is supplied, read that exact report and verify accepted recommendations
against the plan during the walkthrough; never discover a replacement review artifact.

### Multi-Part Plan Detection

After resolving the plan path, check whether this is a part file of a multi-part plan:

1. **Detect the part suffix:** If the plan filename contains `_part_` (e.g., `_part_a`, `_part_b`, `_part_1`), this is one part of a multi-part plan. Extract the part identifier (A, B, C… or number) from the suffix.

2. **⚠️ SCOPE BOUNDARY — CRITICAL:** If a part suffix is detected, immediately output to the terminal:
   > "⚠️ MULTI-PART PLAN DETECTED: Validating PART {X} ONLY. This session MUST NOT read, open, reference, or validate any other part files. Sibling part files visible in {{AUTOSKILLIT_TEMP}}/ or any other directory are entirely out of scope and must be ignored."

3. **Verify the scope warning block:** Check that the plan file contains the mandatory scope warning block immediately after the title line. The block must match this form:
   ```
   > **PART {X} ONLY. Do not implement any other part. Other parts are separate tasks requiring explicit authorization.**
   ```
   If the block is absent, or contains the wrong part label or wording, insert or correct it as your **first** edit to the plan file before proceeding to phase validation.

### Step 2: Extract and Validate Each Phase (SINGLE MESSAGE)

**Start ALL independent child delegations before awaiting any result — one per item — and join every child before synthesis.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

For each phase, verify using subagents:

```
1. Do the target files exist?
2. Do the referenced functions/classes exist?
3. Are the assumptions about current state correct?
4. Will the changes introduce circular dependencies?
5. Are there hidden dependencies not mentioned?
6. Does this violate any project rules?
7. Does the implmentation make sense given the reality of the current state of code?
8. Is every new component, class, or function actually wired into the call chain? Nothing should be created but left unconnected.
9. If the plan adds tests that call mutating methods on shared objects (singletons, global
   registries, server instances), does the plan account for state restoration? Scan the
   plan text for method calls on module-scope objects (e.g., enable(...), disable(...),
   register(...), connect(...)). If found, verify the plan specifies cleanup. Inspect
   the target test directory's existing isolation pattern (conftest autouse fixtures,
   setup/teardown) and confirm the plan's new tests comply. If the plan prescribes
   mutating shared state without specifying cleanup, flag it.
10. If the plan describes wrapping existing code in a block statement (with/for/if/try),
    does it specify the structural boundary the block covers (e.g., "extends from line N
    to the except handler at line M" or "covers the entire try body")? If the plan
    contains transformation language without an extent claim, flag it as incomplete.
    Verify the stated boundary exists in the target file at the described location.
```

### Step 3: Check Cross-Phase Dependencies

Verify phase ordering:
- Does Phase N depend on Phase N-1 completion?
- Are there implicit dependencies not stated?
- Could phases be reordered for safety?

### Step 4: Validate Against Project Rules

```
PROJECT RULES CHECKLIST:
[ ] No backward compatibility code
[ ] No fallbacks that hide errors
[ ] No stakeholder sections
[ ] No PR breakdown sections
[ ] Follows existing architectural patterns
[ ] Uses existing utilities (not reinventing) unless refactoring is part of plan or provides major improvement
[ ] Test command uses the project's configured `test_check.commands` (list of commands, if set) or `test_check.command` (from `.autoskillit/config.yaml`, default: `task test-check`) — no unconfigured direct test runner invocations (pytest, python -m pytest, etc.)
[ ] Worktree setup uses `worktree_setup.command` or `task install-worktree` — no hardcoded `uv venv`, `pip install`, or direct package manager invocations
[ ] Code samples comply with the Architectural Constraint Catalog in resolve-review/SKILL.md — read the catalog table (44 constraints enforced by pytest, not pre-commit) and verify no plan code sample violates a listed constraint (e.g., .write_text() instead of _atomic_write(), bare `import re` instead of `import regex as re`)
[ ] Plan-prescribed deliverable output paths are committable (not gitignored) — run `git check-ignore -v {path}` on each deliverable path; if any is ignored, flag and require the plan to use a tracked location
```

**Test command enforcement:** Scan the entire plan for any test invocation. Read the project's configured test commands from `.autoskillit/config.yaml`: check `test_check.commands` first (list of ordered commands, if set); fall back to `test_check.command` (single command, default: `task test-check`). If the plan contains `pytest`, `python -m pytest`, `make test`, or any other unconfigured test runner invocation, replace it with the config-driven command(s).

**Worktree setup enforcement:** Scan the plan for any worktree environment setup. The plan should reference the project's configured `worktree_setup.command` or `task install-worktree`. If the plan contains hardcoded `uv venv`, `uv pip install`, `pip install -e`, `npm install` (as worktree setup, not as a configured command), flag it and replace with the config-driven approach.

**Architectural constraint enforcement:** Read the Architectural Constraint Catalog table in `resolve-review/SKILL.md` (under the heading "Architectural Constraint Catalog — consult before classifying ACCEPT"). For each code sample or code block in the plan, verify it does not violate any cataloged constraint. If a violation is found, flag it with the constraint name and enforcing test file, and note the required alternative (e.g., "Plan line 260 uses `.write_text()` — REQ-AST-002 requires `_atomic_write()`").

### Step 4.3: Pipeline Gate Compatibility Check

Scan the plan text (this part only — do not read other parts) for any statement that a test is expected to fail after implementation. Detectable patterns include:
- "test will fail" / "test is expected to fail" / "test remains red"
- "exception of test_X" / "single expected failure"
- "deferred to Part [B/C/...]" in the context of test changes
- "removal deferred" / "deletion deferred" for test files

**If any such pattern is found:** Emit `Dry Walkthrough FAILED` with reason: "Plan declares expected test failure after implementation — gate incompatible. Each part must independently pass the test gate. Use xfail(strict=True) bridging or co-locate the test change."

This check does not require cross-part reading — a plan declaring a post-part red test in its own text is gate-incompatible by definition.

Stop execution — do not proceed to Step 5.

### Step 4.4: Size Budget and Proportionality Check

1. Verify the plan's `size_budget = {N}` line exists (immediately after the title). If
   missing, compute it from per-step estimated added-line counts (expected LoC × 1.5,
   rounded up to nearest 100) and add it during Step 5 fix-in-place — missing budget is
   a fix, not a failure.
2. Sum the per-step added-line estimates. If the sum exceeds `size_budget`, either raise
   the budget with a one-line justification or descope via `## Deferred Items` — the plan
   must leave this step internally consistent.
3. Proportionality screen: flag any new registry, enum, ID-wrapper class, state machine,
   protocol, or abstraction layer that the task text does not name and that fewer than two
   existing call sites need today. Move flagged machinery to `## Deferred Items`. This
   step is the one place in the plan pipeline authorized to reduce scope; it must not add
   scope.

### Step 4.5: Historical Regression Check

Run a lightweight two-part scan to detect whether the plan risks reintroducing
patterns that were previously fixed or conflicts with tracked GitHub issues.
This is a quick cross-reference sanity check — not a deep audit.

**Defaults:** Last 100 recent commits · Issues closed in last 30 days

**A. Git History Scan**

1. Extract the set of source files the plan proposes to touch by grepping the plan
   text for paths matching `src/**/*.py` and `tests/**/*.py`. Store as `PLAN_FILES`.

2. Scan recent commit messages on those files for fix/revert/remove/replace keywords:
   ```bash
   git log --oneline -100 --format="%H %s" --grep="fix\|revert\|remove\|replace\|delete" -- {PLAN_FILES}
   ```

3. For each matching commit, determine signal strength:
   - **Strong signal:** The plan proposes to add a function or class name that appears
     in the commit's diff as a deletion — check with:
     `git show {hash} | rg "^-def |^-class |^-async def "` and compare against
     function/class names the plan introduces.
   - **Weak signal:** Same file touched + fix/revert keyword in message, but no
     symbol-level match.

4. Classify:
   - **Strong signal → Actionable:** Insert a warning note into the affected plan step:
     `> ⚠️ Historical note: {symbol} was removed in {hash} ("{commit_message}") — verify this addition is intentional and does not reintroduce a known bug.`
   - **Weak signal → Informational:** Record for terminal output (collected in Part C).

**B. GitHub Issues Cross-Reference**

1. Check `gh` authentication:
   ```bash
   gh auth status 2>/dev/null
   ```
   If this fails, skip Part B and record an informational note:
   "GitHub issues scan skipped — gh not authenticated."

2. Fetch open and recently closed issues:
   ```bash
   gh issue list --state open --json number,title,body --limit 100
   gh issue list --state closed --json number,title,body,closedAt --limit 100
   ```
   Filter closed issues to those `closedAt` within the last 30 days.

3. Build a keyword set from the plan: target file basenames (without `.py`), function
   names mentioned in the plan, and key terms from described changes.

4. Cross-reference each issue's title and body against the keyword set:
   - **Closed issue match → Actionable:** The issue specifically fixed a pattern the
     plan proposes to introduce. Insert a warning note into the affected plan step:
     `> ⚠️ Historical note: Issue #{N} ("{title}") addressed this area — ensure the plan does not reintroduce the fixed pattern.`
   - **Open issue match → Informational:** Record for terminal output:
     "Issue #{N}: {title} — addresses the same area. Verify alignment before implementing."

**C. Collect informational findings**

Gather all weak-signal git findings and open-issue area overlaps into a list.
These are forwarded to Step 7 for inclusion in the `### Historical Context` terminal section.
If Part A and Part B produce no findings, record: "No historical regressions or issue overlaps detected."

### Step 4.6: Plan-vs-Issue Coverage Check

If `issue_url` or `issue_number` was provided to this skill, verify that the plan covers every remediation/requirement item enumerated in the source issue. Without this check, a planner that silently drops an item produces a plan that the implementation pipeline faithfully follows — leaving the issue item unaddressed.

1. **Guard:** If `issue_url` or `issue_number` is not provided, omit this check and record: "Plan-vs-issue coverage check omitted — no issue context provided."

2. **Fetch the issue body:**
   ```bash
   gh issue view {issue_number} --json body -q .body
   ```

3. **Extract enumerated items** from the issue body. Scan for patterns indicating structured remediation or requirement items:
   - `R0`, `R1`, `R2`, … (Rn pattern)
   - `REQ-*-NNN` patterns
   - Numbered lists under `## Remediation`, `## Requirements`, or `## Items` headings
   - Checkbox items (`- [ ]` or `- [x]`) under scope headings

4. **When the issue body contains no enumerated items:** The issue does not use structured enumeration — coverage validation is not applicable. Record: "No enumerated items detected in issue body — coverage check not applicable."

5. **Cross-reference each enumerated item** against plan phases and steps:
   - For each item, search the plan text for the item's label (e.g., "R0"), its description keywords, and its referenced file paths
   - Classify as `COVERED` (plan step explicitly addresses this item) or `UNMAPPED` (no plan step addresses it)

6. **If any items are UNMAPPED:** Do NOT stamp the plan. Output a blocking failure:
   ```
   ## Dry Walkthrough FAILED — Plan-vs-Issue Coverage Gap

   **Plan:** {path}
   **Issue:** #{issue_number}
   **Status:** FAILED — plan does not cover all issue-enumerated items

   ### Unmapped Items
   - {item_label}: {item_description} — not addressed by any plan step

   The plan must cover every remediation/requirement item enumerated in the source
   issue. If an item cannot be delivered, re-scope the issue body first (issue body
   is the source of truth per AGENTS.md §3.4) and re-plan. Do not descope items
   in the plan.
   ```
   Stop execution — do not proceed to Step 5.

7. **If all items are COVERED:** Record coverage confirmation and proceed to Step 5.

### Step 4.7: Plan-vs-Inventory Coverage Check

This check consumes only the server-provided `audit_cycle_inventory` preflight evidence. The
server has already verified recipe identity, current-head authority, exact artifact bytes,
plan/report/parent lineage, and the production evaluator decision. Do not open, search for,
or reconstruct any inventory, remediation, authority, or report.

1. If preflight is `OMIT`, record its stable reason (`no_authority`, `trusted_go`, or
   `trusted_go_successor`) and omit Step 4.7. Absence is distinct from an authoritative
   empty inventory; do not convert one into the other.
2. If preflight is `PASS`, copy its exact ordered rows into the walkthrough record. Preserve
   every `satisfied-by-round-N` and `carried@step` value and the cited implementation step
   without reclassification.
3. If preflight is `REJECT`, or any returned row is `UNMAPPED`, do not stamp or edit the plan
   to cure the decision. Output:

   ```
   ## Dry Walkthrough FAILED — Plan-vs-Inventory Coverage Gap

   **Plan:** {path}
   **Status:** FAILED — audit-cycle inventory admission rejected
   **Reason:** {stable_preflight_reason}

   ### Unmapped Items
   - {exact evaluator row and detail}

   The evaluator result is authoritative. Resolve disputed findings through a successor
   audit-impl verdict; do not drop or pad requirements here.
   ```

   Stop execution — do not proceed to Step 5.

4. Only `PASS` and `OMIT` may proceed to Step 5. This check composes independently with the
   plan-vs-issue check in Step 4.6.

### Step 5: Fix the Plan

For each issue found:
1. Directly edit the plan file to fix it
2. Do NOT add any "gap analysis" or "issues" sections to the plan
3. The plan should read as if it was correct from the start
4. Never add carry-forward padding, add/remove Requirements Map rows, or change a
   `satisfied-by-round-N`/`carried@step` result to make Step 4.7 pass. Step 5 cannot
   override the evaluator.

### Step 6: Mark Plan as Verified

After fixing all issues, add this exact line as the **first line** of the plan file:

```
Dry-walkthrough verified = TRUE
```

This marker indicates the plan has been validated and is ready for implementation. The implement-worktree skill checks for this marker before proceeding.

### Step 6.1: Verify Stamp Landed

After adding the marker line, immediately read the first line of the plan file back:

1. Read the plan file
2. Check that the first line is exactly `Dry-walkthrough verified = TRUE`
3. If the marker is NOT present (edit was blocked by write guard or failed for any reason):
   - Do NOT attempt to write the marker or plan content to any other file
   - Do NOT create a copy of the plan at a different path
   - Output this exact failure message to the terminal:

   ```
   ## Dry Walkthrough FAILED

   **Plan:** {path}
   **Status:** FAILED — Could not stamp plan file

   The verification marker could not be written to the plan file.
   This session must end as a failure. The implement step will not accept
   an unstamped plan.
   ```

   - Stop execution immediately — do not proceed to Step 7

### Step 7: Report to Terminal

After updating the plan, output a summary to the terminal (your response text):

```
## Dry Walkthrough Complete

**Plan:** {path}
**Status:** {PASS - Ready to implement / REVISED - See changes below}

### Changes Made
1. {What was changed and why}
2. {What was changed and why}

### Verified
- {Key assumption that was confirmed}
- {Key assumption that was confirmed}

### Historical Context
- {finding}: {description}
  (or: No historical regressions or issue overlaps detected.)

### Recommendation
{Implement as-is / Review changes before implementing}
```

## Output Rules

| Content | Where it goes |
|---------|---------------|
| Fixed plan content | Written to plan file (Edit tool) |
| Gap analysis | Terminal output (your response text) |
| Change summary | Terminal output (your response text) |
| Recommendations | Terminal output (your response text) |

## Example

**Input:** User says "dry walkthrough {{AUTOSKILLIT_TEMP}}/make-plan/api_retry_plan.md"

**Process:**
1. Read the plan
2. Validate Phase 1: File exists, function exists - PASS
3. Validate Phase 2: Found similar pattern in `src/db/client.py` not referenced - needs fix
4. Validate Phase 3: Test command correct - PASS
5. Edit the plan to add reference to existing pattern
6. Output summary to terminal

**Terminal Output:**
```
## Dry Walkthrough Complete

**Plan:** {{AUTOSKILLIT_TEMP}}/make-plan/api_retry_plan.md
**Status:** REVISED

### Changes Made
1. Phase 2: Added reference to existing retry pattern in `src/db/client.py:45-67` - implementation should follow this pattern for consistency

### Verified
- `src/api/client.py` exists with expected `__init__` signature
- No circular dependency risk identified
- Test commands are correct

### Historical Context
- Issue #302: "consolidate retry logic" — addresses the same area. Verify alignment before implementing.

### Recommendation
Ready to implement. Review the updated Phase 2 to see the pattern reference.
```

**Plan file:** Updated cleanly with no gap analysis sections - just the corrected implementation instructions.
