---
name: investigate
description: Deep investigation of errors, bugs, or codebase questions without making any code changes. Use when user mentions investigate, understand, explore, analyze, or pastes error tracebacks. Spawns parallel subagents for comprehensive exploration.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '🔍 [SKILL: investigate] Starting investigation...'"
          once: true
---

# Investigation Skill

Perform deep codebase investigation without making any changes. This skill uses parallel subagents to explore multiple aspects simultaneously.

## When to Use

- User pastes an error traceback and wants root cause analysis
- User wants to understand how a system/module works
- User asks "how did tests miss this" or similar
- User says "investigate", "explore", "understand", or "analyze"
- User explicitly says "do not change any code"

## Deep Analysis Mode

### Activation

Deep analysis mode is activated when ANY of the following conditions are met:

1. The `--depth deep` flag is present in ARGUMENTS
2. The user explicitly says "investigate deeply" or requests "deep analysis"
3. An explicit batch count is requested (e.g., "run 3 batches")
4. The user requests "maximum thoroughness"
5. The recipe passes `--depth deep` via the `skill_command`

### Model

When deep analysis mode is active, all subagents spawned via the Task tool always use `model: "sonnet"`. The main skill session model is controlled by the recipe or user — typically `opus[1m]` for the main session in deep mode.

### When NOT Activated

Deep analysis mode is never enabled by default. When none of the activation conditions above are met, the skill routes to Standard Mode (Steps 1–4).

## GitHub Issue Input

If the ARGUMENTS contain a GitHub issue reference, call `fetch_github_issue` via the MCP
tool **before** beginning any analysis. Use the returned `content` field as the investigation topic.

**Detection — scan ARGUMENTS for any of these patterns:**
- Full URL: `https://github.com/{owner}/{repo}/issues/{N}`
  (e.g. `https://github.com/acme/project/issues/42`)
- Shorthand: `{owner}/{repo}#{N}` (e.g. `acme/project#42`)
- Bare number with default repo: `#N` or `N` when `github.default_repo` is configured
- Orchestrator hint line: a line containing `GitHub Issue:` followed by a URL or shorthand

**Behavior:**
- If the entire ARGUMENTS is an issue reference → call `fetch_github_issue` and use the
  returned `content` as the complete investigation topic.
- If ARGUMENTS contains a trailing `GitHub Issue: {url}` line (added by the pipeline
  orchestrator) → call `fetch_github_issue` for that URL and append the returned content
  as supplementary context appended after the investigation topic.
- Call with `include_comments: true` for full context.
- If `fetch_github_issue` returns `success: false`, log the failure and proceed with the
  raw ARGUMENTS as-is.

## Critical Constraints

**NEVER:**
- Modify any source code files
- Suggest backward compatibility solutions
- Suggest fallbacks that hide errors
- Create files outside `{{AUTOSKILLIT_TEMP}}/investigate/` directory
- Choose or accept approaches, solutions, and/or fixes that are chosen simply because they are easier
- File GitHub issues automatically (issue filing is always a separate, user-directed action)

**ALWAYS:**
- Use subagents for parallel exploration
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write findings as a markdown report with unique name to `{{AUTOSKILLIT_TEMP}}/investigate/` directory (relative to the current working directory)
- After writing the investigation report, emit the **absolute path** as a structured output
  token as your final output. Resolve the relative `{{AUTOSKILLIT_TEMP}}/investigate/...`
  save path to absolute by prepending the full CWD:
  ```
  investigation_path = /absolute/cwd/{{AUTOSKILLIT_TEMP}}/investigate/{filename}.md
  ```
  This token is MANDATORY — the pipeline cannot proceed without it.
- Identify how tests missed the issue (if applicable)
- Check for similar existing patterns in codebase
- Ensure approaches, solutions, and fixes are the appropriate long-term solutions with proper architecture

## Standard Mode Workflow (Steps 1–4)

**Path-existence guard:** Before issuing a `Read` call on a path that is not guaranteed to
exist (e.g., plan file arguments, `{{AUTOSKILLIT_TEMP}}/investigate/` reports, external file references), use
`Glob` or `ls` to confirm the path exists first. This prevents ENOENT errors that cascade into
sibling parallel-call cancellations.

### Step 1: Parse the Investigation Target

Identify what needs investigation:
- **Error Investigation**: Extract error type, message, and stack trace
- **Module Investigation**: Identify the module/component to understand
- **Question Investigation**: Clarify the specific question being asked

### Step 2: Launch Parallel Subagents

Spawn explore subagents to investigate different aspects simultaneously (some aspects may and should require multiple subagents):

**Core Implementation**
- Find the primary source files
- Understand the main logic flow
- Identify key functions/classes

**Dependencies & Consumers**
- What depends on this code?
- What does this code depend on?
- Map the dependency graph

**Test Coverage**
- Find all tests for this code
- Identify what scenarios are tested
- Find gaps in test coverage

**Error Context (if error investigation)**
- Trace the error through the stack
- Find where the bad state originated
- Identify the root cause

**Similar Patterns**
- Search for similar code elsewhere
- How do other parts handle this?
- Are there established patterns?

**Architecture Context**
- Read relevant architecture.md files
- Understand design decisions
- Check for documented constraints

**External Research (Web Search)**
- Search for error messages in external sources
- Look up known issues in libraries/frameworks
- Find documentation for relevant APIs
- Check GitHub issues for similar problems
- Search for Stack Overflow discussions

### Step 3: Synthesize Findings

After subagents complete, consolidate into structured findings:

1. **Summary**: One paragraph overview
2. **Root Cause** (if error): The actual source of the problem
3. **Affected Components**: List of files/modules involved
4. **Data Flow**: How data moves through the system
5. **Test Gap Analysis**: Why tests didn't catch this
6. **Similar Patterns**: How similar issues are handled elsewhere
7. **Historical Context**: Whether this root cause has been investigated or fixed before (populated by Step 3.5)
8. **External Research**: Relevant findings from web search (if applicable)
9. **Scope Boundary**: What was investigated vs. what remains unexplored
10. **Confidence Levels**: Per-finding confidence — SUPPORTED (direct code evidence or experimental confirmation), UNSUPPORTED (contradicted by evidence), NEEDS-EVIDENCE (theoretical reasoning, not yet confirmed)
11. **Recommendations**: Suggested approaches (NOT implementations)

### Step 3.5 — Historical Recurrence Check

Before writing the report, check whether the root cause identified in Step 3 has been investigated or fixed before. This catches recurring bugs where a prior fix was incomplete, symptom-only, or applied at the wrong layer. Zero overhead for first-occurrence bugs: if nothing matches, skip the analysis subagent and record a single-line result.

#### Part A: Mine Past Investigation Logs

Derive the Claude project log directory from the current working directory:

```bash
PROJECT_PATH=$(pwd)
LOG_DIR="$HOME/.claude/projects/-${PROJECT_PATH//\//-}"
LOG_DIR="${LOG_DIR//--/-}"
```

Search for `.jsonl` files containing prior `/autoskillit:investigate` invocations, **excluding subagent log subdirectories** (`*/subagents/*`) so prior subagent conversations are not double-counted:

```bash
find "$LOG_DIR" -name "*.jsonl" -not -path "*/subagents/*" -print0 | \
  xargs -0 grep -l '/autoskillit:investigate' 2>/dev/null
```

For each matching log file, extract the investigation topic, root cause conclusion, and affected components by scanning for keywords `"root cause"`, `"Root Cause"`, `"fix"`, and `"summary"` in assistant messages. Compare against the current investigation's root cause and affected components — overlapping components or error patterns indicate a recurrence.

#### Part B: Check Git History for Prior Fix Commits

Using the affected components from Step 3, extract the primary source file paths. Then search bounded recent history (last 100 commits) for commits whose messages signal a prior fix or revert on those files:

> `{AFFECTED_FILES}` expands to a space-separated list of file paths relative to the repo root (e.g. `src/autoskillit/execution/headless.py tests/execution/test_headless.py`). Pass each path as a separate argument — do not wrap the list in quotes as a single string.

```bash
git log --oneline -100 --format="%H %s" \
  --grep="fix\|revert\|remove\|replace" -- {AFFECTED_FILES}
```

For each matching commit, read the diff to check for symbol-level overlap with the current root cause:

```bash
git show {HASH} -- {AFFECTED_FILES}
```

Cross-reference: if a commit message references the same error type, component name, or function that the current investigation identified as the root cause, treat it as a prior fix for the same or a closely related issue.

#### Part C: Conditional Analysis (only if history found)

If Part A or Part B found matches, spawn a single subagent (using `model: "sonnet"` via the Task tool) to:

- Read the prior fix diffs via `git show {commit_hash}`
- Read any prior investigation report files discovered during log scanning
- Compare the prior fix approach against the current root cause
- Identify what the prior fix missed (incomplete coverage, wrong layer, symptom-only fix, missing regression test)
- Determine whether this represents a recurring pattern that needs architectural remediation

If neither Part A nor Part B produced matches, skip the subagent entirely and record: **"No prior investigations or fixes found for this root cause."** This guarantees zero overhead for first-occurrence bugs.

#### Rectify Flag

When prior fixes are found and the analysis shows the root cause is recurring, explicitly flag: *"This is a recurring pattern — consider running `/autoskillit:rectify` for architectural immunity after resolving the immediate issue."*

### Step 4: Write Report

Write findings to: `{{AUTOSKILLIT_TEMP}}/investigate/investigation_{topic}_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

Report structure:
```markdown
# Investigation: {Topic}

**Date:** {YYYY-MM-DD}
**Scope:** {What was investigated}
**Mode:** {Standard | Deep Analysis}

## Summary
{One paragraph overview}

## Root Cause
{If error investigation - the actual source}

## Affected Components
- {file1}: {role} [SUPPORTED / NEEDS-EVIDENCE]
- {file2}: {role} [SUPPORTED / NEEDS-EVIDENCE]

## Data Flow
{How data moves through the system}

## Test Gap Analysis
{Why existing tests didn't catch this}

## Similar Patterns
{How similar scenarios are handled elsewhere}

## Historical Context
{If prior fixes found:}
- Prior investigation dates and report paths
- Prior fix commits/PRs with hashes and summaries
- Analysis of why prior fixes were insufficient
- Whether this represents a recurring pattern (flag for /autoskillit:rectify)
{If no prior history:}
No prior investigations or fixes found for this root cause.

## External Research
{Relevant findings from web search - library bugs, known issues, documentation insights}
{Include source URLs for reference}

## Scope Boundary

**Investigated:** {What was actually explored in this investigation}
**Not yet explored:** {Areas identified but not yet investigated — may warrant follow-up}

## Recommendations
{Suggested approaches - NOT code changes}
{In deep analysis mode: single recommendation, not a menu of options}
{Include killed alternatives with reasons if deep mode}
```

After writing the report file, emit the structured output token as the very last line
of your text output:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
investigation_path = {absolute_path_to_investigation_report_file}
```

## Deep Analysis Mode Workflow (Steps D1–D6)

When deep analysis mode is activated, Steps D1–D6 replace standard Steps 1–3.5. Step 4 (Write Report) is still executed at the end of D5, and Step D6 adds post-report validation before emitting `investigation_path`.

### Step D1: Parse Target + Propose Batch Plan

Parse the investigation target (same as Step 1). Then propose an adaptive batch plan:

- Minimum 2 batches; typically 3–4 for complex investigations
- State the planned batch count and scope for each batch
- Present as "Proposed investigation plan: Batch 1 — {scope}, Batch 2 — {scope}, ..."

**Headless auto-approve:** Check the `AUTOSKILLIT_HEADLESS` environment variable. If set to a truthy value (`1`, `true`, `yes`), automatically approve the proposed batch plan without prompting the user. In interactive mode (AUTOSKILLIT_HEADLESS unset or falsy), present the plan and await user confirmation before proceeding to D2.

### Step D2: Batch 1 — Broad Parallel Exploration

Launch a minimum of 4 parallel subagents (model: "sonnet") covering:

- **Code path tracing**: Trace execution paths through the primary affected components
- **Log and history analysis**: Scan session logs and git history for prior occurrences
- **Related component mapping**: Map all components that interact with the target
- **External research**: Web search for known issues, library bugs, documentation

Simultaneously with Batch 1 subagents, run historical recurrence check (Step 3.5 Parts A+B) in parallel. After Batch 1 completes, produce inter-batch synthesis: summarize confirmed findings, open questions, and new investigative leads.

After inter-batch synthesis, run Part C of Step 3.5 conditionally: if Parts A+B found prior history, spawn the conditional analysis subagent. Otherwise skip and proceed to D3.

### Step D3: Batch 2+ — Informed Deepening

For each subsequent batch (Batch 2, Batch 3, ...):

1. Open with an explicit synthesis from prior batches — what was confirmed, what remains uncertain
2. Each batch must include mandatory code exploration (local code search, file reads, symbol tracing) and mandatory web research (search for external documentation, known issues, library behavior)
3. After each batch completes, produce inter-batch synthesis (confirmed findings, open questions, new leads)

**Early termination:** When all findings across all open questions are SUPPORTED (backed by direct code evidence) and no new investigative leads have emerged in the last batch, stop iterating and proceed to D4.

**Empty batch handling:** If a batch produces no new findings (all subagents report the same conclusions as prior batches), treat it as an early termination signal and proceed to D4.

### Step D4: Challenge Round

Fires when ANY finding across any batch is marked NEEDS-EVIDENCE.

Spawn one adversarial subagent (model: "sonnet") whose role is to disconfirm the primary hypothesis:

- Provide the primary hypothesis and all supporting evidence collected so far
- Task: find counterevidence — code paths, behaviors, or data that contradict the hypothesis
- Task: assess prior-fix falsifiability — if Step 3.5 found prior fix history, determine whether the prior fix actually addressed this root cause or only a surface symptom

**If counterevidence is found:** Return to D3 for one additional deepening batch focused on reconciling the contradiction.

**If no counterevidence is found:** The hypothesis stands. Proceed to D5.

### Step D5: Solution Convergence

Spawn solution-space subagents to enumerate candidate fixes. For each candidate, spawn one blast radius subagent (model: "sonnet") to assess:

- Which components would be affected by this fix
- What tests would need to be added or modified
- What risk surface is introduced

After blast radius analysis, converge to a single recommendation — the highest-confidence, lowest-blast-radius candidate with direct code evidence. Kill alternative options and document why each was rejected.

### Step D6: Post-Report Validation

After writing the report (Step 4), spawn 2–3 independent validator subagents (model: "sonnet") with distinct roles:

- **Validator 1 — Factual accuracy**: Cross-check every claim in the report against actual code/evidence. Flag any factual inaccuracy.
- **Validator 2 — Recommendation soundness**: Assess whether the single recommendation is implementable, safe, and correctly scoped.
- **Validator 3 — Gap analysis** (optional, spawn if investigation was complex): Identify what the report does not cover that could be relevant.

If any validator identifies errors or gaps, apply in-place corrections to the report before emitting `investigation_path`. The structured output token is emitted **after** all validation and correction is complete.

## Subagent Prompt Template

### Standard Mode Template

Use this template for each Explore subagent in standard mode:

```
Investigate {specific aspect} of {target}.

Focus on:
1. {Specific question 1}
2. {Specific question 2}
3. {Specific question 3}

This is a research task - DO NOT modify any code.
Report your findings in a structured format.
```

### Deep Analysis Mode Template

Use this template for each subagent in deep mode batches:

```
Investigate {specific aspect} of {target}.

Context from prior batches:
{Summary of confirmed findings and open questions from previous batch inter-batch synthesis}

Focus on:
1. {Specific question 1}
2. {Specific question 2}
3. {Specific question 3}

Evidence standards:
- Cite specific file paths and line numbers for all code claims
- Include log timestamps for any log-based findings
- Mark each finding as SUPPORTED (direct evidence), UNSUPPORTED (contradicted), or NEEDS-EVIDENCE (not yet confirmed)

This is a research task - DO NOT modify any code.
Report your findings in a structured format with explicit evidence citations.
```

### Adversarial Subagent Template (Challenge Round)

Use this template for the D4 challenge round subagent:

```
PRIMARY HYPOTHESIS: {primary hypothesis from D3 synthesis}

SUPPORTING EVIDENCE:
{All evidence collected across all batches that supports the hypothesis}

YOUR ROLE: You are an adversarial reviewer. Your task is NOT to confirm the hypothesis.
Your task is to find counterevidence — code paths, behaviors, data, or reasoning that
CONTRADICTS or WEAKENS the primary hypothesis.

Specifically:
1. Search for code paths that would NOT exhibit the described behavior
2. Look for prior fix commits that may have already addressed this root cause
3. Assess prior-fix falsifiability: if prior fixes exist, determine whether they actually
   addressed this root cause or only a surface symptom
4. Identify any assumptions in the hypothesis that are not backed by direct code evidence

If you find counterevidence, report it explicitly with citations.
If you cannot find counterevidence after thorough search, report "No counterevidence found."

This is a research task - DO NOT modify any code.
```

### Validation Subagent Template

Use this template for D6 validator subagents:

```
REPORT PATH: {absolute path to investigation report}

YOUR ROLE: {Factual Accuracy Validator | Recommendation Soundness Validator | Gap Analysis Validator}

Factual Accuracy Validator tasks:
- Read the full report
- Cross-check every factual claim against the actual codebase (use file reads, symbol lookups)
- Flag any claim that is incorrect, imprecise, or not backed by direct code evidence
- Propose corrections for any inaccuracies found

Recommendation Soundness Validator tasks:
- Read the Recommendations section of the report
- Assess whether the recommendation is implementable without introducing new bugs
- Assess whether the scope is correct (not too narrow, not too broad)
- Flag any risk surface the recommendation introduces

Gap Analysis Validator tasks:
- Read the Scope Boundary section of the report
- Identify any areas listed as "not yet explored" that are actually relevant to the root cause
- Identify any areas NOT listed that should have been explored
- Report whether the investigation coverage is sufficient for the stated findings

Report your findings in a structured format. If corrections are needed, state them explicitly.
This is a research task - DO NOT modify any code.
```
