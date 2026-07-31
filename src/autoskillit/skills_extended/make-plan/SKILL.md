---
name: make-plan
uses_capabilities: [agent_model, agent_subagent, write_audit_disposition_bundle]
activate_deps: [write-recipe]
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

## Arguments

- `task` — Task or source document for an ordinary plan.
- `issue_url` (optional) — GitHub issue context consumed as described below.
- `adversarial_review_level` (optional) — `auto`, `full`, or `none`.
- `audit_cycle_path` (optional) — The explicit current audit-cycle authority. Its presence,
  not prose flags or ambient files, activates remediation mode. Before reading any referenced
  artifact, verify that this authority is the server-published current `NO GO` head and that
  its generation, plan set, scope, part, round, parent, and audited-plan lineage match this run.

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

5. **Complexity-Gated Adversarial Review Decision**

Draft the complete plan from the selected approach using the Output format before calculating complexity or spawning adversarial review agents. Then determine the review level.

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
   depending on the complexity gate decision in Step 5). For each valid finding (where the agent identified a real gap, not a hypothetical):
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
# Do NOT use --amend — always create new commits.
```

These produce regular (single-parent) commits that `merge_worktree`'s rebase gate
handles correctly.

---

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
- Discover a latest audit, read an ambient `requirements_inventory.json`, or treat a loose
  remediation path as authority
- Emit `false_positive` or otherwise close an active `NO GO`; only a successor audit-impl
  authority may close that lineage
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
  plan_disposition_path = /absolute/cycle/directory/dispositions/{plan_digest}.json
  ```
  `plan_path` and `plan_parts` are mandatory for every run. In remediation mode,
  `plan_disposition_path` is also mandatory.
- Spawn all subagents via `Agent(model="sonnet")`
- Recommend the single best technical solution
- Ground decisions in design quality and correctness
- Include verification steps
- Be willing to recommend significant refactoring if that's the right answer
- Issue all Task calls in a single message to maximize parallelism
- The plan must cover every remediation item enumerated in the source issue; if an item cannot be delivered, stop and surface it — do not descope it in the plan
- Every new component, class, or function is wired into the call chain — nothing is created but left unconnected

**Requirement Echo Rule:** Every behavioral requirement stated in `## Summary` or `## Design Decisions` prose MUST be echoed as an explicit `## Implementation Steps` directive. After drafting the plan:

1. Enumerate every behavioral constraint in the prose sections above.
2. For each constraint, verify it maps to at least one explicit step directive.
3. If a constraint has no corresponding step, add one. Never leave behavioral requirements as prose-only.
4. Include a `## Requirements Map` section at the end of the plan listing each constraint with its corresponding step reference.

## Context Limit Behavior

Before a context-limited session terminates, preserve every completed plan file and,
in remediation mode, its matching disposition and association artifacts. Emit only
paths for artifacts whose final bytes and hashes have already been verified. Never
invent a partial disposition report or treat a prose-only plan as successful output;
the caller must retry planning when the required artifact tuple is incomplete.

## Output

If the plan exceeds 500 lines, split it into multiple files (`_part_a`, `_part_b`, etc.) at natural section boundaries. Use as many parts as needed.

**CRITICAL — Multi-part plan rules:**
- **Never include file paths or guessable names for other parts.** No paths, no filenames, no references that allow an agent to locate other part files.
- Include only a brief plain-text note about what subsequent parts cover (e.g., "Part B will cover X and Y — implement as a separate task").
- The title of each part file MUST include `— PART A ONLY` (or B, C, etc.) so scope is immediately visible.
- Each part file MUST open with the scope warning block shown in the multi-part template below.
- **Every part MUST independently pass `task test-check`.** A part that registers a symbol, adds a route, or changes behavior that causes a pre-existing test to fail must also update, remove, or `xfail`-bridge that test in the same part. Split boundaries must keep gate-prerequisites with the code that triggers them.
  - **`xfail(strict=True)` bridge:** When a test must temporarily fail during a multi-part implementation (e.g., a deletion-guard canary that will be removed in a later part), mark it with `pytest.mark.xfail(strict=True, reason="<symbol> registered in Part X; guard removed in Part Y (#NNNN)")` in the current part. `check_test_passed` ignores xfailed counts, so the gate passes. `strict=True` ensures the xfail mark is removed when the guard is deleted — if the test starts passing unexpectedly (because the later part landed), CI breaks until the mark is cleaned up. The bridge's `reason` must cite the open tracking issue (`#NNNN`) for the deferred work — enforced by an architectural guard. A bridge whose stated exit condition is satisfied within the same PR must be removed in that same PR.
  - **Deletion-guard canaries** (tests asserting a name/symbol/command is absent) must be removed or xfail-bridged in the same part that re-registers the name. Never defer canary removal to a later part.

Save the plan to: `{{AUTOSKILLIT_TEMP}}/make-plan/{task_name}_plan_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

**Structured output:** After saving the file(s), emit the following lines so pipeline
orchestrators can capture the plan and, in remediation mode, its verified disposition:

**Verdict emission:** Every run MUST emit a `verdict` token as the first structured output line:
- `verdict = plan` — the only successful verdict. Emit `plan_path` and `plan_parts`;
  remediation mode must also emit `plan_disposition_path`.

**Remediation-mode authority and disposition production:**

1. Activate remediation mode only when `audit_cycle_path` is present. Missing authority is
   not equivalent to an authoritative empty inventory.
2. Verify the canonical `AuditCycleAuthority`, trusted current-head identity, and `NO GO`
   verdict before opening its inventory or remediation `ArtifactRef`. Reject a stale,
   superseded, cross-generation, cross-scope, cross-part, tampered, or `GO` authority.
3. Verify every referenced artifact's locator, byte size, content digest, schema, and
   containment from the exact bytes read.
4. For every inventory row, write exactly one row in the plan's `## Requirements Map`:

   ```
   ## Requirements Map

   | Requirement ID | Disposition | Implementation Step |
   |---|---|---|
   | REQ-001 | satisfied-by-round-1 | — |
   | REQ-007 | carried@step | Step 3 |
   ```

   `carried@step` must cite the concrete current `Step N`/`Step N.M` that implements the
   same REQ ID. `satisfied-by-round-N` must name the verified prior audit round. No other
   vocabulary, duplicate IDs, omitted rows, or invented padding is allowed.
5. After the final plan bytes are stable, call `write_audit_disposition_bundle(...)` with
   only the verified current `authority_path`, final plan path/media type/schema version,
   and the exact ordered child-owned disposition rows. Do not copy or submit execution,
   cycle, plan-set, scope, part, round, authority, inventory, findings, timestamp, report,
   association, root, or output-path identities. The server reloads the committed current
   authority, copies all verified v1 identity/digest fields, derives the new plan reference,
   injects one retry-stable timestamp, prepares both canonical files, and final-CAS commits
   their ledger projection against the still-current head. Verify the returned
   `PlanDispositionReport` against the plan with the production
   inventory-admission evaluator. The digest-keyed association remains server-owned at
   `associations/{verified_plan_content_digest}.json`.
6. Emit the server-returned `plan_disposition_path`. Never search for or synthesize a latest
   report or association; prepared files without a committed ledger projection are not
   published evidence.
7. Absence, duplication, evaluator rejection, or Markdown/report drift is an output-contract
   failure. Do not emit successful structured tokens.

For a single-part plan:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
verdict = plan
plan_path = {absolute_path}
plan_parts = {absolute_path}
plan_disposition_path = {absolute_path_when_in_remediation_mode}
```

After the structured paths, emit the completion marker supplied by the active order
as `%%ORDER_UP::<eight hexadecimal characters>%%`.

For a multi-part plan (list all part paths in alphabetical order):
```
verdict = plan
plan_path = {path_to_part_a}
plan_parts = {path_to_part_a}
{path_to_part_b}
{path_to_part_c}
plan_disposition_path = {absolute_path_when_in_remediation_mode}
```

**Plan structure (single-part):**
```markdown
# Implementation Plan: {Task Name}

## Summary
{Brief overview of what will be implemented}

## Tests
{Tests to write first — should fail now, pass after implementation}

## Implementation Steps
{Ordered steps, each making one or more of the above tests pass}

## Verification
{How to verify the implementation is correct}

## Requirements Map
| Requirement (from prose) | Implementation Step |
|---|---|
| {behavioral constraint from Summary/Design Decisions} | Step {N.M}: {step description} |
```

**Plan structure (multi-part — use for EACH part file):**
```markdown
# Implementation Plan: {Task Name} — PART {X} ONLY

> **PART {X} ONLY. Do not implement any other part. Other parts are separate tasks requiring explicit authorization.**

## Summary
{What THIS part covers. Explicitly note what is deferred: "Part B will cover X (separate task). Part C will cover Y (separate task)."}

## Tests
{Tests for THIS part only — should fail now, pass after THIS part's implementation}

## Implementation Steps
{Steps for THIS part only}

## Verification
{How to verify THIS part's implementation is correct}

## Requirements Map
| Requirement (from prose) | Implementation Step |
|---|---|
| {behavioral constraint from THIS part's Summary/Design Decisions} | Step {N.M}: {step description} |
```
