---
name: audit-bugs
categories:
- audit
uses_capabilities:
- claude_dir
description: Analyze historical bug patterns by mining Claude Code project logs for /autoskillit:investigate skill invocations
  since a specified date. Identifies recurring root causes, architectural gaps, and proactive detection strategies. Use when
  user says "audit bugs", "bug patterns", "analyze investigations", or "bug audit".
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: audit-bugs] Mining investigation logs for bug patterns...'''
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
---

# Bug Pattern Audit Skill

Mine Claude Code conversation logs for `/autoskillit:investigate` skill invocations to identify recurring bug patterns, architectural gaps, and proactive detection strategies.

## When to Use

- User says "audit bugs", "bug patterns", "analyze investigations", or "bug audit"
- User wants to find recurring themes across past bug investigations
- User wants proactive strategies to catch bugs before they manifest

## Arguments

The user may provide a "since" date (e.g., `2/7`, `2026-02-07`, `last week`). If not specified, use `AskUserQuestion` to ask what the earliest lookback date should be before proceeding.

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
- Create files outside `{{AUTOSKILLIT_TEMP}}/audit-bugs/` directory
- Detach child delegations instead of joining them (joining every child is required)
- Start independent child delegations sequentially

**ALWAYS:**
- Use subagents heavily for parallel log analysis
- All output goes under `{{AUTOSKILLIT_TEMP}}/audit-bugs/` (create if needed)
- Final report: `{{AUTOSKILLIT_TEMP}}/audit-bugs/bug_pattern_audit_{YYYY-MM-DD_HHMMSS}.md`
- Subagents must NOT create their own files - they return findings in their response text only
- Do not change any code
- Start all independent child delegations before awaiting any result to maximize concurrency

## Workflow

### Step 1: Locate Project Logs

Claude Code stores conversation logs at `~/.claude/projects/` in a folder derived from the project's absolute path with `/` replaced by `-`.

Derive the log directory:
```bash
# Convert current working directory to Claude's folder naming scheme
PROJECT_PATH=$(pwd)
LOG_DIR="$HOME/.claude/projects/-${PROJECT_PATH//\//-}"
# Remove leading double dash if present
LOG_DIR="${LOG_DIR//--/-}"
```

Verify the directory exists and contains `.jsonl` files.

### Step 2: Filter by Date and Investigate Skill

1. Use `find` with `-newermt` to filter `.jsonl` files modified since the target date
2. From those, `grep -l '"skill".*"investigate"'` to find files where the investigate skill was invoked (tool invocation pattern)
3. Also `grep -l '/autoskillit:investigate'` to catch user-typed invocations
4. Combine and deduplicate. Only use top-level files (not subagent logs under `*/subagents/`)

### Step 3: Dispatch Subagents for Parallel Analysis (SINGLE MESSAGE)

**Start ALL independent child delegations before awaiting any result — one per item — and join every child before synthesis.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Split the matching files into batches of ~5 and dispatch general-purpose subagents in parallel. Each subagent should extract from each log file:

- **Error/Symptom**: The error message or failure the user reported
- **Root Cause**: What the investigation identified as the root cause
- **Component**: Which module/system was affected
- **Category**: Bug classification (e.g., "type boundary", "state management", "validation gap")
- **Fix**: What solution was identified or applied

**Subagent instructions for reading logs:**
- JSONL format: each line is a JSON object
- `"type": "human"` entries contain user messages (error reports)
- `"type": "assistant"` entries with text content contain investigation findings
- Look for tool calls writing to `{{AUTOSKILLIT_TEMP}}/investigate/investigation_*.md` or `{{AUTOSKILLIT_TEMP}}/rectify/rectify_*.md` for structured findings
- Search for keywords: "root cause", "Root Cause", "fix", "summary", "finding"
- Read the first ~500 lines for context, then search for conclusions

### Step 4: Synthesize Patterns

After subagents return, group findings into recurring patterns:

1. Identify bugs that share the same root architectural weakness
2. Count frequency of each pattern across sessions
3. For each pattern, identify:
   - Which components it affects
   - Why it keeps recurring
   - What architectural gap enables it
   - Concrete grep/search patterns that could detect latent instances today

### Step 5: Write Report

Ensure `{{AUTOSKILLIT_TEMP}}/audit-bugs/` exists (`mkdir -p`).

Save to: `{{AUTOSKILLIT_TEMP}}/audit-bugs/bug_pattern_audit_{YYYY-MM-DD_HHMMSS}.md`

Structure:
```markdown
# Bug Pattern Audit: Investigations Since {date}

**Analysis Date:** {today}
**Sessions Analyzed:** {count}

## Executive Summary
{2-3 sentences: top patterns, frequency, recommended investments}

## Pattern N: {Name}
**Frequency:** X of Y sessions (Z%)

### Manifestations
| Session | Date | Bug | Component |
{table of affected sessions}

### Root Architectural Gap
{Why this pattern keeps occurring}

### Proactive Detection Strategy
{Concrete scans, tests, or grep patterns to find latent instances}

---

## All Sessions Quick Reference
| # | Session ID | Date | Error Summary | Pattern(s) |
{table of all sessions}

## Recommended Proactive Scans
{Runnable grep/rg commands to find latent bugs today}
```

### Step 6: Terminal Summary

Output a concise summary: pattern count, top 3 patterns by frequency, and report location.
