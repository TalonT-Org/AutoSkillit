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

**ALWAYS:**
- Use subagents for parallel exploration
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write findings as a markdown report with unique name to `{{AUTOSKILLIT_TEMP}}/investigate/` directory (relative to the current working directory)
- After writing the investigation report, emit the **absolute path** as a structured output
  token immediately before `%%ORDER_UP%%`. Resolve the relative `{{AUTOSKILLIT_TEMP}}/investigate/...`
  save path to absolute by prepending the full CWD:
  ```
  investigation_path = /absolute/cwd/{{AUTOSKILLIT_TEMP}}/investigate/{filename}.md
  %%ORDER_UP%%
  ```
  This token is MANDATORY — the pipeline cannot proceed without it.
- Identify how tests missed the issue (if applicable)
- Check for similar existing patterns in codebase
- Ensure approaches, solutions, and fixes are the appropriate long-term solutions with proper architecture

## Investigation Workflow

**Path-existence guard:** Before issuing a `Read` call on a path that is not guaranteed to
exist (e.g., plan file arguments, `{{AUTOSKILLIT_TEMP}}/investigate/` reports, external file references), use
`Glob` or `ls` to confirm the path exists first. This prevents ENOENT errors that cascade into
sibling parallel-call cancellations.

### Step 0.5 — Code-Index Initialization (required before any code-index tool call)

Call `set_project_path` with the repo root where this skill was invoked (not a worktree path):

```
mcp__code-index__set_project_path(path="{PROJECT_ROOT}")
```

Code-index tools require **project-relative paths**. Always use paths like:

    src/<your_package>/some_module.py

NOT absolute paths like:

    /absolute/path/to/src/<your_package>/some_module.py

> **Note:** Code-index tools (`find_files`, `search_code_advanced`, `get_file_summary`,
> `get_symbol_body`) are only available when the `code-index` MCP server is configured.
> If `set_project_path` returns an error, fall back to native `Glob` and `Grep` tools
> for the same searches — they provide equivalent results without the code-index server.

Agents launched via `run_skill` inherit no code-index state from the parent session — this
call is mandatory at the start of every headless session that uses code-index tools.

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
9. **Recommendations**: Suggested approaches (NOT implementations)

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
  xargs -0 grep -l '"skill".*"investigate"\|/autoskillit:investigate' 2>/dev/null
```

For each matching log file, extract the investigation topic, root cause conclusion, and affected components by scanning for keywords `"root cause"`, `"Root Cause"`, `"fix"`, and `"summary"` in assistant messages. Compare against the current investigation's root cause and affected components — overlapping components or error patterns indicate a recurrence.

#### Part B: Check Git History for Prior Fix Commits

Using the affected components from Step 3, extract the primary source file paths. Then search bounded recent history (last 100 commits) for commits whose messages signal a prior fix or revert on those files:

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

## Summary
{One paragraph overview}

## Root Cause
{If error investigation - the actual source}

## Affected Components
- {file1}: {role}
- {file2}: {role}

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

## Recommendations
{Suggested approaches - NOT code changes}
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

## Subagent Prompt Template

Use this template for each Explore subagent:

```
Investigate {specific aspect} of {target}.

Focus on:
1. {Specific question 1}
2. {Specific question 2}
3. {Specific question 3}

This is a research task - DO NOT modify any code.
Report your findings in a structured format.
```
