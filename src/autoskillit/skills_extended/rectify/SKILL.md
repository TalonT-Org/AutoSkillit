---
name: rectify
uses_capabilities: []
description: Deep investigation of test gaps and architectural weaknesses following an investigation, then devise a plan for
  architectural immunity rather than direct fixes. Use when user says "rectify", "rectify this", or wants to address root
  architectural causes after an investigation.
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''🏗️ [SKILL: rectify] Investigating architectural gaps and devising immunity plan...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: plan-foundation-auditor
    purpose: perform the named independent responsibility and return bounded evidence
  - name: plan-interface-mapper
    purpose: perform the named independent responsibility and return bounded evidence
  - name: plan-registry-tracer
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: plan-foundation-auditor
  - role: plan-interface-mapper
  - role: plan-registry-tracer
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
  child_model_policies:
  - role: plan-foundation-auditor
    model_class: sonnet
  - role: plan-interface-mapper
    model_class: sonnet
  - role: plan-registry-tracer
    model_class: sonnet
---

# Rectify Skill

Based on your investigation report, use subagents to investigate further how our tests missed this and if there are any other similar or related bugs. Walk over the codebase carefully.

Then devise a plan to resolve these issues. No bandaids, fallbacks, or other approaches that just fix the direct exact issue.

The approach should make it so the architecture, structure and/or pattern is innately immune to the issue in the first place and/or results in the issue being easily and instantly surfaced as an error caught by testing.

Explore the architecture of the systems involved very carefully and map the components they connect to with subagents.

Find what the architectural solution would be instead of just applying a direct fix to the immediate issue. The solution should solve more than just the issue at hand. Immunity must be proportionate: the smallest change that makes the bug class impossible, scoped to failures that can actually occur — every added line must earn its maintenance cost.

Do not change any code.

## When to Use

- After an investigation has been completed (usually via the `/autoskillit:investigate` skill)
- User says "rectify", "rectify this", or "address root cause"
- User wants to understand why tests missed something and how to prevent it architecturally

<!-- output-discipline:begin -->
### Output Discipline Policy v1

- Treat shell and tool output as a bounded resource. Choose the smallest useful producer and set a byte limit before running it.
- Bound discovery itself: use forms such as `rg -l PATTERN PATH 2>&1 | head -c N`, where `N` is within the configured inline-output ceiling, or redirect both descriptors to a project-temp artifact.
- For JSONL, use record-aware search with a per-record limit such as `rg -M 500`; never rely on a bare line cap because one record may contain an arbitrarily large payload.
- Route stdout and stderr from every stage of an output-producing pipeline into the terminal byte cap. Intermediate stderr must not bypass the cap.
- Follow `jq` field extraction with a byte cap. Selecting one field does not make its contents small.
- Redirect potentially unbounded output to `{{AUTOSKILLIT_TEMP}}/<skill>/out.txt` with both descriptors captured, then inspect only bounded searches or byte slices from that artifact.
- Read complete files only when their size is known to be small. Otherwise locate matches first and read only the bounded relevant region.
- Give every subagent an explicit maximum size for its final report and request only evidence needed by the parent synthesis.
- Before authorizing each deep-mode batch, reserve enough context for synthesis, report writing, and validation. Stop gathering and begin synthesis when another batch would cross that reserve.
- A command's success does not make oversized inline output safe. Preserve full evidence in project temp and return a bounded summary plus the artifact path.
<!-- output-discipline:end -->

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Propose bandaid fixes, fallbacks, or direct-only fixes
- Suggest backward compatibility shims
- Create files outside `{{AUTOSKILLIT_TEMP}}/rectify/` directory
- Detach child delegations instead of joining them (joining every child is required)
- Start independent child delegations sequentially

**ALWAYS:**
- Use subagents for parallel exploration
- Spawn all subagents via `child delegation under the declared `sonnet` model-class policy`
- Focus on architectural immunity over direct fixes
- Identify how tests missed the issue and similar/related bugs
- Map the components and their connections thoroughly
- Write the plan as markdown to `{{AUTOSKILLIT_TEMP}}/rectify/` directory (relative to the current working directory)
- Start all independent child delegations before awaiting any result to maximize concurrency
- After writing the plan file, emit the **absolute path** as a structured output token
  as your final output. The save path is relative (`{{AUTOSKILLIT_TEMP}}/rectify/...`) but
  the token **must** use the absolute path (prepend the full CWD):
  ```
  plan_path = /absolute/cwd/{{AUTOSKILLIT_TEMP}}/rectify/{filename}.md
  plan_parts = /absolute/cwd/{{AUTOSKILLIT_TEMP}}/rectify/{filename}.md
  ```
  This token is MANDATORY — the pipeline cannot capture the output without it.
- The solution must solve more than just the immediate issue
- The plan must cover every remediation item enumerated in the source issue; if an item cannot be delivered, stop and surface it — do not descope it in the plan
- Every new component, class, or function is wired into the call chain — nothing is created but left unconnected

## Context Limit Behavior

When context is exhausted mid-execution, the plan file may already be written to disk
even though the `plan_path`/`plan_parts` token was never emitted. The recipe routes to
`on_context_limit` (a deterministic salvage step), which checks whether the captured
`plan_parts` paths exist as non-empty files on disk. If they do, the pipeline continues
as though this skill had succeeded; if not, it falls through to this skill's original
`on_failure` destination.

This skill writes only new plan files under `{{AUTOSKILLIT_TEMP}}/rectify/` (never
modifies source code), so a context-limit stumble has no blast radius beyond an
unclaimed plan file.

## Rectify Workflow

### Step 1: Identify the Investigation Context

Locate the most recent investigation report in `{{AUTOSKILLIT_TEMP}}/investigate/` or from conversation context. Extract:
- The root cause identified
- Affected components
- Test gaps noted
- Any recommendations made

**Path-existence guard:** Before issuing a `Read` call on a path that is not guaranteed to
exist (e.g., plan file arguments, `{{AUTOSKILLIT_TEMP}}/investigate/` reports, external file references), use
`Glob` or `ls` to confirm the path exists first. This prevents ENOENT errors that cascade into
sibling parallel-call cancellations.

### Step 2: Deep Exploration with Subagents (SINGLE MESSAGE)

**Start ALL independent child delegations before awaiting any result — one per item — and join every child before synthesis.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Launch parallel subagents to investigate (some of the listed aspects may require multiple subagents):

**Test Gap Analysis**
- How did existing tests miss this?
- What assumptions did the tests make that were wrong?
- Are there other tests making the same flawed assumptions?

**Similar/Related Bugs**
- Walk the codebase for similar patterns that could have the same issue
- Check if the root cause affects other components
- Look for code that relies on the same flawed assumption

**Architectural Mapping**
- Map the full component graph around the affected area
- Understand the boundaries, contracts, and data flow
- Identify where structural guarantees are missing

**Pattern Analysis**
- How do well-designed parts of the codebase prevent similar issues?
- What architectural patterns would make this class of bug impossible?
- Search externally for how other projects handle this structurally

### Step 3: Devise the Architectural Solution

Design an approach that provides **immunity** rather than a fix:
- The architecture/structure/pattern should make the bug class impossible or instantly caught
- The solution should address the broader pattern, not just the single instance
- Testing improvements should catch this and related issues by design

**Test-Driven Approach:** The plan must lead with tests. Before any implementation step, define a test that reproduces the issue or captures the gap. Each subsequent implementation step should make that test pass. This applies to the initial fix and to any broader architectural changes—write the failing test first, then the code that makes it green.

Draft the complete immunity plan from Step 3's selected approach using the Output template before spawning adversarial reviewers.

### Step 4: Foundation Audit

Spawn 1 Foundation Auditor via `a child assigned logical role `plan-foundation-auditor` under its declared model policy`. Pass the full draft immunity plan text and the codebase root. Prepend the contrastive frame to the prompt:

> "A junior engineer reviewed this immunity plan and found no structural flaws. What did they miss?"

The Foundation Auditor performs step-by-step control-flow analysis: enumerates functions, draws control flow with scope levels, builds reachability tables, audits guard coverage, and applies exploit-first verification. It must NOT suggest scope expansion — only identify gaps in what the plan already claims to do.

**Child continuation protocol:** If the subagent returns with a continuation hint (truncated at maxTurns), use `child continuation message` to resume it:
- `to`: the `agentId` from the continuation hint
- `message`: `"Finalize your analysis and provide your complete findings report."`
- `summary`: `"Continue rectify subagent to finalize findings"`

The `summary` field is **required** when `message` is a string — omitting it causes `InputValidationError`. If the resumed agent still returns truncated, proceed without its findings rather than retrying further.

After reading the agent's findings, revise the draft plan by incorporating all valid findings (real gaps, not hypotheticals) before proceeding to Step 5.

### Step 5: Interface Mapping

Spawn 1 Interface Mapper via `a child assigned logical role `plan-interface-mapper` under its declared model policy`. Pass the **revised** draft plan text (from Step 4) and the codebase root. Prepend the contrastive frame to the prompt:

> "A junior engineer reviewed this plan's variable usage and found it correct. What did they miss?"

The Interface Mapper traces variable SET/READ points with full hop-by-hop provenance, builds a Similar-Variable Confusion Matrix, and audits caller/callee contracts. It must NOT suggest scope expansion — only identify gaps in what the plan already claims to do.

**RULES FOR APPLYING INTERFACE MAPPING FINDINGS:** When the interface mapper identifies the correct variable for a step, apply the correction to ALL fields that consume that variable — cwd, skill_command arguments, branch references, SHA captures, output paths. Do not split the correct variable across some fields while leaving other fields on the wrong variable.

**Child continuation protocol:** If the subagent returns with a continuation hint (truncated at maxTurns), use `child continuation message` to resume it:
- `to`: the `agentId` from the continuation hint
- `message`: `"Finalize your analysis and provide your complete findings report."`
- `summary`: `"Continue rectify subagent to finalize findings"`

The `summary` field is **required** when `message` is a string — omitting it causes `InputValidationError`. If the resumed agent still returns truncated, proceed without its findings rather than retrying further.

After reading the agent's findings, revise the draft plan by incorporating all valid findings before proceeding to Step 6.

### Step 6: Registry Trace

Spawn 1 Registry Tracer via `a child assigned logical role `plan-registry-tracer` under its declared model policy`. Pass the **revised** draft plan text (from Step 5) and the codebase root. Prepend the contrastive frame to the prompt:

> "A junior engineer reviewed this plan's registry coverage and found it complete. What did they miss?"

The Registry Tracer uses three-layer tracing (LSP primary, tree-sitter structural, grep fallback) to find every file referencing symbols the plan touches. It checks participation in registry-sync patterns (RETIRED NAME SETS, RE-EXPORT CHAINS, TOOL REGISTRIES, RULE REGISTRATION, DUAL-COPY CONSTANTS, IMPORT LAYER CONSTRAINTS, TYPED ALIASES, DERIVED ARTIFACTS), then performs a two-layer completeness check (source-code layer vs. test/fixture layer). It must NOT suggest scope expansion — only identify gaps in what the plan already claims to do.

**RULES FOR APPLYING REGISTRY TRACE FINDINGS:** Verify BOTH fixture/test completeness AND registry completeness before finalizing. A plan that addresses only one interpretation of a rename (manifest-focused OR workspace-focused) and misses the cross-cutting update is incomplete. Apply the two-family check: if references appear in only one layer (source-code or test/fixture), perform targeted follow-up searches in the other layer before concluding.

**Child continuation protocol:** If the subagent returns with a continuation hint (truncated at maxTurns), use `child continuation message` to resume it:
- `to`: the `agentId` from the continuation hint
- `message`: `"Finalize your analysis and provide your complete findings report."`
- `summary`: `"Continue rectify subagent to finalize findings"`

The `summary` field is **required** when `message` is a string — omitting it causes `InputValidationError`. If the resumed agent still returns truncated, proceed without its findings rather than retrying further.

After reading the agent's findings, apply all valid findings. The plan is now fully reviewed and ready for file write.

---

## Output

If the plan exceeds 500 lines, split it into multiple files (`_part_a`, `_part_b`, etc.). Each part must be a **self-contained, independently implementable plan** executed sequentially. Split by functional scope (e.g., Part A = "fix core bug + tests", Part B = "add guards + enforcement"), NOT by document structure. Each file must have its own failing tests, implementation steps, and verification.

**Multi-part plan rules:**
- Never include file paths or guessable names for other parts.
- Include only a brief plain-text note about what subsequent parts cover (e.g., "Part B will cover X — implement as a separate task").
- The title of each part file MUST include `— PART A ONLY` (or B, C, etc.).
- Each part file MUST open with: `> **PART {X} ONLY. Do not implement any other part. Other parts are separate tasks requiring explicit authorization.**`

Save the plan to: `{{AUTOSKILLIT_TEMP}}/rectify/rectify_{topic}_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

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

The `size_budget` value must be rendered as plain digits only — no thousands
separators, no markdown decoration. The downstream gate regex accepts bare
digits only and silently falls back to its ingredient default otherwise.

Each implementation step must carry an estimated added-line count, and every
new module or class must cite the requirement that forces it. Move anything
justified as "future-proofing", "robustness", or "while we're here" to a
`## Deferred Items` section instead of the plan body.

**Plan structure:**
```markdown
# Rectify: {Topic}
size_budget = {N — plain digits only, e.g. 1500}

**Date:** {YYYY-MM-DD}
**Investigation Reference:** {link to or name of the investigation report}

## Summary
{Brief overview of the architectural weakness and proposed immunity}

## How Tests Missed This
{Analysis of the test gap - what assumptions were wrong}

## Related Issues Found
{Other instances of the same or similar weakness in the codebase}

## Architectural Analysis
{Map of affected components and their connections}

## Immunity Plan

### Step 1: Failing Tests (~N added lines)
{Tests that reproduce the issue and capture the gap — these must be written first}

### Step 2: Implementation (~N added lines)
{The architectural solution that makes this class of bug impossible or instantly caught, structured so each change makes a failing test pass}

## Verification
{How to verify the architectural changes provide the intended immunity}

## Deferred Items
{Items deferred for proportionality — not needed for this immunity}
```
