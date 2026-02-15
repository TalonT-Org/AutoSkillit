---
name: dry-walkthrough
description: Validate an implementation plan by tracing through each change without implementing. Use when user says dry walkthrough, drywalkthrough, validate plan, or check plan. Identifies gaps, fixes the plan directly, and reports changes to terminal.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '🔎 [SKILL: dry-walkthrough] Validating plan...'"
          once: true
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

## Critical Constraints

**NEVER:**
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

**ALWAYS:**
- Keep the plan as clean implementation instructions only (information/background helpful to implementation is okay)
- Report all findings to terminal output (your response text)
- Fix issues by directly updating the plan content
- Verify assumptions against actual codebase
- Remove deprecation code/notes and rollback mechanisms
- Make sure the plan includes warning against using the codebase as a notepad with useless comments
- Prefer the long term health of project over quick, easy, and minimal fixes

## Dry Walkthrough Workflow

### Step 1: Load the Plan

Read the plan from:
- Path provided by user
- Plan content pasted directly
- Most recent plan in temp/ subdirectories

### Step 2: Extract and Validate Each Phase

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
```

### Step 5: Fix the Plan

For each issue found:
1. Directly edit the plan file to fix it
2. Do NOT add any "gap analysis" or "issues" sections to the plan
3. The plan should read as if it was correct from the start

### Step 6: Mark Plan as Verified

After fixing all issues, add this exact line as the **first line** of the plan file:

```
Dry-walkthrough verified = TRUE
```

This marker indicates the plan has been validated and is ready for implementation. The implement-worktree skill checks for this marker before proceeding.

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

**Input:** User says "dry walkthrough temp/make-plan/api_retry_plan.md"

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

**Plan:** temp/make-plan/api_retry_plan.md
**Status:** REVISED

### Changes Made
1. Phase 2: Added reference to existing retry pattern in `src/db/client.py:45-67` - implementation should follow this pattern for consistency

### Verified
- `src/api/client.py` exists with expected `__init__` signature
- No circular dependency risk identified
- Test commands are correct

### Recommendation
Ready to implement. Review the updated Phase 2 to see the pattern reference.
```

**Plan file:** Updated cleanly with no gap analysis sections - just the corrected implementation instructions.
