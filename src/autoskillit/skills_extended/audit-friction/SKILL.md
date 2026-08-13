---
name: audit-friction
categories:
- audit
uses_capabilities:
- claude_dir
description: Scan Claude Code project logs for friction patterns — repeated failures, approach loops, tool errors, misunderstanding
  cycles, and stuck workflows. Categorizes and counts friction events to surface what causes the most resistance. Use when
  user says "audit friction", "find friction", "friction audit", or "what keeps going wrong".
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: audit-friction] Scanning logs for friction patterns...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: friction-batch-scanner
    purpose: scan an assigned log batch and return bounded evidence
  - name: friction-category-analyzer
    purpose: independently validate one friction category and return bounded evidence
  child_spawns:
  - role: friction-batch-scanner
    count: 1
  - role: friction-category-analyzer
    count: 1
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
  child_model_policies:
  - role: friction-batch-scanner
    model_class: haiku
  - role: friction-category-analyzer
    model_class: sonnet
---

# Friction Audit Skill

Mine Claude Code conversation logs to identify and categorize friction — repeated failures, stuck loops, tool errors, and misunderstanding cycles that cost the most effort. Surfaces patterns causing the most resistance and recommends concrete mitigations.

## When to Use

- User says "audit friction", "find friction", "friction audit", or "what keeps going wrong"
- User wants to know where workflows repeatedly stall or fail
- User wants to identify systemic tooling or workflow issues across sessions

## Arguments

The user may provide a "since" date (e.g., `2/7`, `2026-02-07`, `last month`). If not specified, use `AskUserQuestion` to ask what the earliest lookback date should be before proceeding. If the resulting window contains no logs, fall back to the last 30 days and note the adjustment.

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
- Create files outside `{{AUTOSKILLIT_TEMP}}/audit-friction/` directory
- Have subagents write files — they return all findings in response text only
- Analyze subagent log subdirectories (`*/subagents/`) — top-level session files only
- Detach child delegations instead of joining them (joining every child is required)
- Start independent child delegations sequentially

**ALWAYS:**
- Use subagents heavily for parallel log analysis
- All output goes under `{{AUTOSKILLIT_TEMP}}/audit-friction/` (create if needed)
- Final report: `{{AUTOSKILLIT_TEMP}}/audit-friction/friction_audit_{YYYY-MM-DD_HHMMSS}.md`
- Report the file and line counts to the terminal before choosing analysis mode
- Start all independent child delegations before awaiting any result to maximize concurrency

## Friction Categories

Friction is any pattern where repeated effort yields no progress:

1. **Tool Failure Loops** — Same tool called 2+ times with repeated errors (file not found, permission denied, command not found, network errors)
2. **Approach Reversals** — Multiple strategy pivots for the same goal: tries approach A, fails, tries B, fails, tries C
3. **Test Fix Cycles** — Repeated test-run → fail → fix → test-run → still fails sequences without resolution
4. **Misunderstanding Cycles** — User corrections indicating the model misread the task; repeated wrong-direction attempts before course correction
5. **Search Failures** — Repeated failed searches for relevant code (wrong file paths, grep returning nothing, symbol not found)
6. **Build/Compile Errors** — Recurring build or compilation failures that block progress across multiple attempts
7. **Permission/Access Blockers** — Tool calls denied, files inaccessible, repeated approval interruptions halting flow
8. **Context Re-exploration** — Model re-investigates already-covered ground within the same session, forgetting earlier findings

## Friction Signal Patterns

Logs are too large to read in full. All analysis uses targeted search commands to surface signal lines, then reads only the surrounding context (a few lines via `-A`/`-B`) to confirm the event. Use this keyword battery against each file:

```bash
# Tool errors — direct flag
rg -n -M 500 '"is_error".*true' FILE 2>&1 | head -c 12000

# Error keywords in content
rg -i -M 500 '"not found|permission denied|no such file|command not found|ENOENT|"failed|"cannot|"error"|exit code [1-9]|Traceback|AssertionError' FILE 2>&1 | head -c 12000

# Human correction language (look inside "type":"human" lines)
rg -n -M 500 '"type":"human"' FILE 2>&1 | rg -i 'wrong|"no,|try again|you already|that.s not|incorrect|revert|undo|stop' 2>&1 | head -c 12000

# Test / build failures
rg -i -M 500 'FAILED|test.*failed|build.*failed|compile.*error|syntax error' FILE 2>&1 | head -c 12000

# Consecutive tool call loops — extract tool names in document order, show runs of 2+ back-to-back
rg -o -M 500 '"name":"[^"]*"' FILE 2>&1 | awk -F'"' '{print $4}' 2>&1 | uniq -c 2>&1 | awk '$1>=2' 2>&1 | head -c 12000

# Once you know which tool is looping, get its line numbers to find the range:
rg -n -M 500 '"name":"TOOL_NAME"' FILE 2>&1 | head -c 12000

# Permission / access blockers
rg -i -M 500 'permission denied|access denied|not allowed|forbidden' FILE 2>&1 | head -c 12000

# Confirm a match with bounded surrounding context
rg -n -M 500 -A 10 -B 10 'CONFIRMING_PATTERN' FILE 2>&1 | head -c 12000
```

For each match batch, use the bounded `-A 10 -B 10` command above to confirm it is a real friction event rather than incidental text, then record the line range and category.

## Workflow

### Step 1: Locate Project Logs

Derive the log directory from the current working directory:

```bash
PROJECT_PATH=$(pwd)
LOG_DIR="$HOME/.claude/projects/-${PROJECT_PATH//\//-}"
LOG_DIR="${LOG_DIR//--/-}"
```

Verify the directory exists and contains `.jsonl` files. If the directory is missing or empty, report this to the user before stopping.

### Step 2: Filter by Date and Count

Filter `.jsonl` files (top-level only, no subagent subdirs) modified since the target date:

```bash
# File count and list (portable — Python date comparison, no GNU findutils required)
python3 - "$LOG_DIR" "$SINCE_DATE" <<'EOF'
import os, sys, datetime
log_dir, since_str = sys.argv[1], sys.argv[2]
cutoff_ts = datetime.datetime.fromisoformat(since_str).timestamp()
matched = [
    os.path.join(log_dir, f)
    for f in os.listdir(log_dir)
    if f.endswith(".jsonl")
    and os.path.isfile(os.path.join(log_dir, f))
    and os.path.getmtime(os.path.join(log_dir, f)) > cutoff_ts
]
matched.sort()
total_lines = sum(open(p).read().count("\n") for p in matched)
print(f"File count: {len(matched)}")
print(f"Total lines: {total_lines}")
for p in matched:
    print(p)
EOF
```

Report both counts to the terminal. If zero files match, extend the window by 15 days and retry, noting the adjustment.

### Step 3: Haiku Batch Scan (SINGLE MESSAGE)

**Start ALL independent child delegations before awaiting any result — one per item — and join every child before synthesis.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Split the log file list into batches. Use batches of ~5 files for smaller corpora; reduce to ~3 files per batch when the corpus is large so Each child under the declared `haiku` model-class policy isn't overloaded. Dispatch one **Haiku model** subagent per batch in parallel.

Each child under the declared `haiku` model-class policy should run the full keyword battery from the Friction Signal Patterns section against each assigned file. Do not read files in full — grep only. For each hit, pull 20 lines of context (`-A 10 -B 10`) to confirm the event and assign a category:

- `"is_error": true` hits → Tool Failure Loops
- Error keyword hits → Tool Failure Loops, Build/Compile Errors, or Search Failures depending on context
- Human correction hits → Misunderstanding Cycles
- Test/build failure hits → Test Fix Cycles or Build/Compile Errors
- Consecutive tool loop hits → Tool Failure Loops or Search Failures
- Permission hits → Permission/Access Blockers

For Approach Reversals and Context Re-exploration (harder to grep directly): look for the same search query or file path appearing repeatedly across the file, or `"not found"` returns immediately followed by a different search for the same thing.

For each confirmed friction event record: `{file, line_start, line_end, category, one-line description}`. Widen the context window further if 20 lines isn't enough to understand the event.

Return all findings as structured text. Do not write any files.

### Step 4: Sonnet Deep Analysis per Category (SINGLE MESSAGE)

**Start ALL independent child delegations before awaiting any result — one per item — and join every child before synthesis.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

After all children under the declared `haiku` model-class policy return, group all indicators by category. Dispatch **Sonnet model** subagents to analyze the grouped indicators in parallel. Any category with at least one indicator warrants a subagent. Spawn one subagent per category when there are many; batch smaller related groups when overall volume is low. The orchestrator decides the grouping.

Each child under the declared `sonnet` model-class policy should:

- Receive the list of `(file, line_start, line_end)` pointers for its assigned category
- Read those specific line ranges with the bounded `rg -n -M 500 -A 10 -B 10 ... 2>&1 | head -c 12000` form to confirm or reclassify each indicator (Haiku may misfire on edge cases)
- Expand the line range further if context is insufficient to understand the event
- For confirmed instances: extract session date, the specific sequence of events, and what was blocking progress
- Count confirmed occurrences and distinct sessions affected
- Identify what confirmed instances share — a specific tool, file path, task type, or workflow pattern
- Determine the most likely root cause (tooling gap, codebase structure, workflow design, model limitation)
- Propose concrete mitigations

Return a structured category report in response text. Do not write any files.

### Step 5: Synthesize Findings

After all subagents return:

1. Merge all category findings; deduplicate any events flagged by multiple subagents
2. Rank categories by confirmed occurrence count (highest first)
3. Cross-reference sessions: those appearing in 3+ categories are high-friction hotspots
4. For each category, assign severity:
   - **HIGH** — ≥ 10 occurrences or directly blocks task completion
   - **MEDIUM** — 5–9 occurrences
   - **LOW** — 1–4 occurrences

### Step 6: Write Report

Ensure `{{AUTOSKILLIT_TEMP}}/audit-friction/` exists (`mkdir -p`).

Save to: `{{AUTOSKILLIT_TEMP}}/audit-friction/friction_audit_{YYYY-MM-DD_HHMMSS}.md`

Structure:

```markdown
# Friction Audit: {Project} Since {date}

**Analysis Date:** {today}
**Log Files Analyzed:** {count}
**Total Lines Scanned:** {count}
**Analysis Mode:** Direct / Two-Round (Haiku triage + deep analysis)

## Executive Summary
{2-3 sentences: top friction types, total events, single highest-leverage mitigation}

## Friction Rankings

| Rank | Category | Occurrences | Sessions Affected | Severity |
|------|----------|-------------|-------------------|----------|

## Category N: {Name}

**Severity:** HIGH / MEDIUM / LOW
**Occurrences:** {count} across {n} sessions

### What It Looks Like
{How this friction manifests in the logs — specific signals and sequences}

### Example
{A concrete excerpt or paraphrase from an actual log showing this friction in action — what the tool call was, what error it got, what happened next}

### Instances
| Session | Date | Context | Friction Description |
|---------|------|---------|----------------------|

### Root Cause
{Why this keeps happening — tooling gap, codebase structure, workflow design, or model limitation}

### Recommended Mitigations
{Concrete, actionable suggestions ordered by impact}

---

## Session Overview

| Session ID | Date | Friction Types | Event Count |
|------------|------|----------------|-------------|
{All sessions with friction, sorted by event count descending}

## High-Friction Hotspots
{Sessions or task types appearing in 3+ categories — best candidates for targeted improvement}
```

### Step 7: Terminal Summary

Output a concise summary:

- Analysis mode used and corpus size (files, lines, date range)
- Total friction events found across all categories
- Top 3 categories by occurrence count with severity
- Report file location
