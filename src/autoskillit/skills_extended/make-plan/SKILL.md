---
name: make-plan
activate_deps: [arch-lens, write-recipe]
description: Planning executor. ALWAYS invoke this skill when instructed to create, devise, or write an implementation plan. Do not explore the codebase or draft a plan directly — use this skill first to load the planning workflow.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '📋 [SKILL: plan] Creating implementation plan...'"
          once: true
---

# Implementation Plan Skill

Create focused, actionable implementation plans that recommend the technically best solution.

## When to Use

- User says "create a plan", "devise a plan", "write a plan"
- User wants an "implementation plan" for a feature or fix
- User asks to "plan out" a task or migration

## Core Values - CRITICAL

The ONLY criterion for choosing an approach is **technical quality and correctness of design**. A well-designed system is the goal. Nothing else matters.

**NEVER use these as reasons to choose or reject an approach:**
- Implementation effort or difficulty ("would require rewrite" is NOT a reason)
- Number of files changed
- Amount of existing code affected
- Number of tests that would need updating
- "Migration risk" or "rollback ease"
- "Preserves existing patterns" (existing patterns may be wrong)
- "Minimal changes needed"
- "Zero changes to X" (not a benefit - neutral at best)
- "Existing tests mostly pass" (tests validate desired behavior, they don't constrain design)

**Tests exist to validate that code works as intended.** When functionality changes, tests SHOULD change. "Would break tests" is never a reason to reject an approach.

**Git handles rollback.** Feature flags for rollback are unnecessary complexity.

**Existing code is not sacred.** If the existing architecture is flawed, the right answer is to fix it, not preserve it.

## GitHub Issue Input

If the ARGUMENTS contain a GitHub issue reference, call `fetch_github_issue` via the MCP
tool **before** beginning any analysis. Use the returned `content` field as the task description.

**Detection — scan ARGUMENTS for any of these patterns:**
- Full URL: `https://github.com/{owner}/{repo}/issues/{N}`
  (e.g. `https://github.com/acme/project/issues/42`)
- Shorthand: `{owner}/{repo}#{N}` (e.g. `acme/project#42`)
- Bare number with default repo: `#N` or `N` when `github.default_repo` is configured
- Orchestrator hint line: a line containing `GitHub Issue:` followed by a URL or shorthand

**Behavior:**
- If the entire ARGUMENTS is an issue reference → call `fetch_github_issue` and use the
  returned `content` as the complete task description.
- If ARGUMENTS contains a trailing `GitHub Issue: {url}` line (added by the pipeline
  orchestrator) → call `fetch_github_issue` for that URL and append the returned content
  as supplementary context appended after the task description.
- Call with `include_comments: true` for full context.
- If `fetch_github_issue` returns `success: false`, log the failure and proceed with the
  raw ARGUMENTS as-is.

## Planning Steps

1. **Understand related systems and validate details** (SINGLE MESSAGE) — **Issue ALL Task tool calls in a single message — one per item — so they execute in parallel. Do NOT iterate across multiple turns.** Do not output any prose between subagent dispatches. Use subagents to study the architecture, how components work together, their purpose, patterns, and standards. Validate any details provided in the task description. When the plan involves adding tests that call mutating methods on singleton or module-level objects (enable/disable, register/unregister, connect/disconnect), use a subagent to read the target test directory's existing isolation patterns (conftest fixtures, setup_method/teardown_method, autouse fixtures) before proceeding to Step 3.

2. **Explore and design approaches** - Use subagents to investigate different ways to solve the problem. Use subagents with web search to research modern solutions, approaches, designs, and architectures relevant to the problem. For each approach, focus on:
   - Does it solve the problem correctly?
   - Is it the right abstraction?
   - Does it enable future evolution of the system?
   - Is the design clean and understandable?

3. **Design tests first** - For the chosen approach, define tests that capture the intended behavior. These tests should fail against the current codebase and pass once the implementation is complete. The implementation steps should be ordered to make these tests pass.

   **Test isolation contract:** When the plan adds tests that call mutating methods on a singleton or module-level object, the plan must specify the isolation strategy — how state is reset between tests. Ensure new tests either inherit the existing isolation mechanism or explicitly define their own. Plans that prescribe calling mutating methods on shared objects without specifying cleanup are incomplete.

4. **Evaluate approaches on technical merit only** - Use subagents to assess each approach. Evaluation criteria:
   - **Correctness**: Does it fully solve the stated problem?
   - **Design quality**: Is this the right abstraction? Is it clean?
   - **Architectural fit**: Does it align with how the system SHOULD work (not how it currently works if current is flawed)?
   - **Maintainability**: Will future developers understand and extend it?

**DO NOT evaluate based on:** implementation effort, risk, number of changes, test breakage, or ease of rollback. These are not engineering criteria.

5. **Visualize with Architecture Lens** - After finalizing the plan, determine which architecture lens best illustrates the proposed changes, then create a mermaid diagram.

**5a. Select the lens based on what the plan primarily affects:**

| If the plan primarily involves... | Use Lens |
|-----------------------------------|----------|
| Adding/modifying containers, services, or integrations | C4 Container |
| Changing workflow logic, state machines, or decision flow | Process Flow |
| Altering data storage, transformations, or information flow | Data Lineage |
| Restructuring modules, changing dependencies, or layering | Module Dependency |
| Adding/modifying parallel execution or thread handling | Concurrency |
| Changing error handling, retry logic, or recovery paths | Error/Resilience |
| Modifying repository patterns or data access | Repository Access |
| Changing CLI commands, config, or monitoring | Operational |
| Adding/modifying validation, trust boundaries, or isolation | Security |
| Changing build tools, test framework, or quality gates | Development |
| Affecting multiple user journeys or cross-component flows | Scenarios |
| Modifying state contracts, field lifecycles, or resume logic | State Lifecycle |
| Changing deployment topology or infrastructure | Deployment |

**5b. Write your lens selection rationale to a file using the Write tool:**

- **Path:** `{{AUTOSKILLIT_TEMP}}/make-plan/arch_lens_selection_{YYYY-MM-DD_HHMMSS}.md`
- **Content:** Which lens was selected and why (1-2 sentences of rationale).

**5c. MANDATORY: LOAD the appropriate arch-lens skill using the Skill tool:**

| Lens | Skill to LOAD |
|------|---------------|
| C4 Container | `/autoskillit:arch-lens-c4-container` |
| Process Flow | `/autoskillit:arch-lens-process-flow` |
| Data Lineage | `/autoskillit:arch-lens-data-lineage` |
| Module Dependency | `/autoskillit:arch-lens-module-dependency` |
| Concurrency | `/autoskillit:arch-lens-concurrency` |
| Error/Resilience | `/autoskillit:arch-lens-error-resilience` |
| Repository Access | `/autoskillit:arch-lens-repository-access` |
| Operational | `/autoskillit:arch-lens-operational` |
| Security | `/autoskillit:arch-lens-security` |
| Development | `/autoskillit:arch-lens-development` |
| Scenarios | `/autoskillit:arch-lens-scenarios` |
| State Lifecycle | `/autoskillit:arch-lens-state-lifecycle` |
| Deployment | `/autoskillit:arch-lens-deployment` |

If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, proceed without the architectural diagram.

**5d. Create the diagram following the loaded skill's instructions:**
- Focus on the PROPOSED changes (use `newComponent` class for new elements)
- Show how new components integrate with existing architecture
- Use `●` prefix for modified existing components
- Use `★` prefix for new components

Include the diagram in the plan document under a "## Proposed Architecture" section.
More than one lens diagram is okay if it is complex plan (don't do more than 3, and make sure to load each appropriate skill).

**5e. Complexity-Gated Adversarial Review Decision**

Before spawning adversarial review agents, determine the review level.

**Reading the override:** Check if ARGUMENTS contains a line matching
`adversarial_review_level=<value>`. Valid values: `auto`, `full`, `none`.
If not found, default to `auto`.

**If `full`:** Proceed to Steps 6, 7, and 8 as written (spawn all 3 agents).

**If `none`:** Skip Steps 6, 7, and 8 entirely. Proceed to Step 9 with no
adversarial reports.

**If `auto`:** Estimate plan complexity from the draft plan you just wrote:

1. **Expected lines of code changed**: Count the total LoC across all
   implementation steps based on the code blocks and edit descriptions.
2. **Number of files touched**: Count distinct file paths in implementation steps.
3. **Number of modules/packages crossed**: Count distinct top-level packages
   (e.g., `core/`, `config/`, `server/` each count as one module).

Classify using this table:

| Complexity | Expected LoC | Files | Modules | Agents to Spawn |
|------------|-------------|-------|---------|-----------------|
| Trivial | < 50 | ≤ 2 | ≤ 1 | None — skip Steps 6-8 |
| Low | 50–150 | ≤ 5 | ≤ 2 | Registry Tracer only (Step 8) |
| Medium | 150–300 | ≤ 10 | ≤ 4 | Registry Tracer (Step 8) + Foundation Auditor (Step 6) |
| High | 300+ | any | any | All 3 agents (Steps 6, 7, 8) |

Use the **highest** complexity band that any single metric reaches. For example,
if LoC is 40 (trivial) but files is 6 (medium), classify as medium.

**Log the decision:** Before proceeding, write one line to the plan file under
a `## Adversarial Review` heading:

> Complexity classification: {level} ({loc} LoC, {files} files, {modules} modules).
> Adversarial agents: {list of agents to spawn, or "none"}.

6. **Foundation Audit** - Spawn 1 Foundation Auditor via `Agent(subagent_type="autoskillit:plan-foundation-auditor")`. Pass the full draft plan text and the codebase root. Prepend the contrastive frame to the prompt:

   > "A junior reviewer found this plan's control flow acceptable — what did they miss?"

   The Foundation Auditor performs step-by-step control-flow analysis: enumerates functions, draws control flow with scope levels, builds reachability tables, audits guard coverage, and applies exploit-first verification. It must NOT suggest scope expansion — only identify gaps in what the plan already claims to do.

**SendMessage continuation protocol:** If the subagent returns with a continuation hint (truncated at maxTurns), use `SendMessage` to resume it:
- `to`: the `agentId` from the continuation hint
- `message`: `"Finalize your analysis and provide your complete findings report."`
- `summary`: `"Continue plan review subagent to finalize findings"`

The `summary` field is **required** when `message` is a string — omitting it causes `InputValidationError`. If the resumed agent still returns truncated, proceed without its findings rather than retrying further.

7. **Interface Mapping** - Spawn 1 Interface Mapper via `Agent(subagent_type="autoskillit:plan-interface-mapper")`. Pass the full draft plan text and the codebase root. Prepend the contrastive frame to the prompt:

   > "A junior reviewer found this plan's variable usage correct — what did they miss?"

   The Interface Mapper traces variable SET/READ points with full hop-by-hop provenance, builds a Similar-Variable Confusion Matrix, and audits caller/callee contracts. It must NOT suggest scope expansion — only identify gaps in what the plan already claims to do.

   **RULES FOR APPLYING INTERFACE MAPPING FINDINGS:** When the interface mapper identifies the correct variable for a step, apply the correction to ALL fields that consume that variable — cwd, skill_command arguments, branch references, SHA captures, output paths. Do not split the correct variable across some fields while leaving other fields on the wrong variable.

**SendMessage continuation protocol:** If the subagent returns with a continuation hint (truncated at maxTurns), use `SendMessage` to resume it:
- `to`: the `agentId` from the continuation hint
- `message`: `"Finalize your analysis and provide your complete findings report."`
- `summary`: `"Continue plan review subagent to finalize findings"`

The `summary` field is **required** when `message` is a string — omitting it causes `InputValidationError`. If the resumed agent still returns truncated, proceed without its findings rather than retrying further.

8. **Registry Trace** - Spawn 1 Registry Tracer via `Agent(subagent_type="autoskillit:plan-registry-tracer")`. Pass the full draft plan text and the codebase root. Prepend the contrastive frame to the prompt:

   > "A junior reviewer found this plan's registry coverage complete — what did they miss?"

   The Registry Tracer uses three-layer tracing (LSP primary, tree-sitter structural, grep fallback) to find every file referencing symbols the plan touches. It checks participation in registry-sync patterns (RETIRED NAME SETS, RE-EXPORT CHAINS, TOOL REGISTRIES, RULE REGISTRATION, DUAL-COPY CONSTANTS, IMPORT LAYER CONSTRAINTS, TYPED ALIASES, DERIVED ARTIFACTS), then performs a two-layer completeness check (source-code layer vs. test/fixture layer). It must NOT suggest scope expansion — only identify gaps in what the plan already claims to do.

   **RULES FOR APPLYING REGISTRY TRACE FINDINGS:** Verify BOTH fixture/test completeness AND registry completeness before finalizing. A plan that addresses only one interpretation of a rename (manifest-focused OR workspace-focused) and misses the cross-cutting update is incomplete. Apply the two-family check: if references appear in only one layer (source-code or test/fixture), perform targeted follow-up searches in the other layer before concluding.

**SendMessage continuation protocol:** If the subagent returns with a continuation hint (truncated at maxTurns), use `SendMessage` to resume it:
- `to`: the `agentId` from the continuation hint
- `message`: `"Finalize your analysis and provide your complete findings report."`
- `summary`: `"Continue plan review subagent to finalize findings"`

The `summary` field is **required** when `message` is a string — omitting it causes `InputValidationError`. If the resumed agent still returns truncated, proceed without its findings rather than retrying further.

9. **Plan Revision** - Read all available adversarial reports (0, 1, 2, or 3
   depending on the complexity gate decision in Step 5e). For each valid finding (where the agent identified a real gap, not a hypothetical):
   - Add missing consumers to implementation steps
   - Add missing entity categories to search/update operations
   - Replace invalid assumptions with verified facts
   - Add missing registry updates and derived artifact regeneration steps
   - When a variable correction applies, propagate to ALL consuming fields (cwd, arguments, branch refs, SHA captures, output paths)

If no adversarial agents were spawned (trivial complexity or `none` override),
proceed directly to finalizing the plan.

Then finalize the plan and emit `plan_path` as normal.

## Conflict-Resolution Plan Requirements

When the task involves resolving conflicts to apply changes from one branch onto another
(i.e., the input is a conflict report produced by `merge-pr`), the plan
**MUST produce a worktree with a linear commit history**.

`merge_worktree` rebases the worktree branch before merging. Standard
`git rebase` cannot replay merge commits; a worktree containing them will
fail with `WORKTREE_INTACT_MERGE_COMMITS_DETECTED`.

**NEVER prescribe in conflict-resolution plans:**
```
git merge --no-ff origin/{branch}            # creates merge commit — rebase fails
git merge --no-commit --no-ff origin/{branch}  # same problem
```

**ALWAYS use linear approaches instead:**
```
# Option A: Per-file checkout (copies contents without merge relationship)
git checkout origin/{branch} -- path/to/file.py

# Option B: Cherry-pick (replays individual commits as regular commits)
git cherry-pick {commit-hash}

# Option C: Squash merge (single linear commit from all changes)
git merge --squash origin/{branch}
git commit -m "feat: apply changes from {branch}"
```

These produce regular (single-parent) commits that `merge_worktree`'s rebase gate
handles correctly.

---

## Skill Loading Checklist

Before writing the final plan, verify:

- [ ] Determined which architecture lens best fits the proposed changes
- [ ] LOADED the corresponding `/autoskillit:arch-lens-*` skill using the Skill tool
- [ ] The arch-lens skill LOADED the `/autoskillit:mermaid` skill for styling
- [ ] Diagram uses ONLY the classDef styles from the mermaid skill (no invented colors)
- [ ] Diagram includes a color legend table
- [ ] Every new component, class, or function is wired into the call chain — nothing is created but left unconnected
- [ ] Adversarial review pass completed per complexity gate (Steps 5e-9) — skipped agents documented in plan

## Critical Constraints

**NEVER use EnterPlanMode.** This skill IS the planning process. Execute the planning steps directly — explore with subagents, design the approach, write the plan file to `{{AUTOSKILLIT_TEMP}}/make-plan/` (relative to the current working directory). Do not enter plan mode, do not call ExitPlanMode. Just do the work and deliver the plan.

**NEVER include:**
- Multiple alternative approaches (recommend ONE only)
- Stakeholder sections
- PR breakdown sections
- Backward compatibility considerations
- Fallback mechanisms
- Justifications based on effort, risk, or preserving existing code

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Change any code
- Choose an approach because it's easier
- Reject an approach because it's harder
- Create files outside `{{AUTOSKILLIT_TEMP}}/make-plan/` directory
- Propagate pipeline stamps or markers from input files into the plan output. Specifically, never include `Dry-walkthrough verified = TRUE` as the first line of the output plan — this stamp is written exclusively by the dry-walkthrough skill after validation
- **Use `git merge` in implementation plans.** When a plan needs to bring in changes from another branch, use `git cherry-pick <commit>` for individual commits or `git checkout <branch> -- <file>` for specific files. `merge_worktree` requires linear commit history — merge commits cannot be rebased and will cause `WORKTREE_INTACT_MERGE_COMMITS_DETECTED` failure. See "Conflict-Resolution Plan Requirements" section for full guidance.
- Run subagents in the background (`run_in_background: true` is prohibited)
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Write to `{{AUTOSKILLIT_TEMP}}/make-plan/` directory (relative to the current working directory)
- After writing the plan file, emit the **absolute path** as a structured output token
  as your final output. The save path is relative (`{{AUTOSKILLIT_TEMP}}/make-plan/...`) but
  the token **must** use the absolute path (prepend the full CWD):
  ```
  plan_path = /absolute/cwd/{{AUTOSKILLIT_TEMP}}/make-plan/{filename}.md
  plan_parts = /absolute/cwd/{{AUTOSKILLIT_TEMP}}/make-plan/{filename}.md
  ```
  This token is MANDATORY — the pipeline cannot capture the output without it.
- Spawn all subagents via `Agent(model="sonnet")`
- Recommend the single best technical solution
- Ground decisions in design quality and correctness
- Include verification steps
- Be willing to recommend significant refactoring if that's the right answer
- Issue all Task calls in a single message to maximize parallelism

## Output

If the plan exceeds 500 lines, split it into multiple files (`_part_a`, `_part_b`, etc.) at natural section boundaries. Use as many parts as needed.

**CRITICAL — Multi-part plan rules:**
- **Never include file paths or guessable names for other parts.** No paths, no filenames, no references that allow an agent to locate other part files.
- Include only a brief plain-text note about what subsequent parts cover (e.g., "Part B will cover X and Y — implement as a separate task").
- The title of each part file MUST include `— PART A ONLY` (or B, C, etc.) so scope is immediately visible.
- Each part file MUST open with the scope warning block shown in the multi-part template below.

Save the plan to: `{{AUTOSKILLIT_TEMP}}/make-plan/{task_name}_plan_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

**Structured output:** After saving the file(s), emit the following lines so pipeline orchestrators can capture both fields:

For a single-part plan:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
plan_path = {absolute_path}
plan_parts = {absolute_path}
```

For a multi-part plan (list all part paths in alphabetical order):
```
plan_path = {path_to_part_a}
plan_parts = {path_to_part_a}
{path_to_part_b}
{path_to_part_c}
```

**Plan structure (single-part):**
```markdown
# Implementation Plan: {Task Name}

## Summary
{Brief overview of what will be implemented}

## Proposed Architecture
{Mermaid diagram showing the proposed changes using the selected lens}

**Lens Used:** {lens name} - {why this lens was chosen}

## Tests
{Tests to write first — should fail now, pass after implementation}

## Implementation Steps
{Ordered steps, each making one or more of the above tests pass}

## Verification
{How to verify the implementation is correct}
```

**Plan structure (multi-part — use for EACH part file):**
```markdown
# Implementation Plan: {Task Name} — PART {X} ONLY

> **PART {X} ONLY. Do not implement any other part. Other parts are separate tasks requiring explicit authorization.**

## Summary
{What THIS part covers. Explicitly note what is deferred: "Part B will cover X (separate task). Part C will cover Y (separate task)."}

## Proposed Architecture
{Mermaid diagram showing the proposed changes using the selected lens}

**Lens Used:** {lens name} - {why this lens was chosen}

## Tests
{Tests for THIS part only — should fail now, pass after THIS part's implementation}

## Implementation Steps
{Steps for THIS part only}

## Verification
{How to verify THIS part's implementation is correct}
```