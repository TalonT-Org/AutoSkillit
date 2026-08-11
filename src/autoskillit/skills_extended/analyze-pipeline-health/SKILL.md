---
name: analyze-pipeline-health
uses_capabilities: []
categories:
- diagnostics
description: Analyze pipeline session logs for anomalies and regressions. Spawns parallel terminal reader agents for bounded
  step-group packets, then diagnoses and consolidates their cited evidence.
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: analyze-pipeline-health] Analyzing pipeline health...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: autoskillit:session-log-reader
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: autoskillit:session-log-reader
    for_each: reader_packets
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
---

# Pipeline Health Analysis Skill

Coordinator skill that scopes a pipeline run, groups retained session identities by step,
spawns no more than six parallel terminal readers, then diagnoses and consolidates their
cited evidence.

## Arguments

`/autoskillit:analyze-pipeline-health <kitchen_id> [--dispatch-id=<id>] [--diagnostics-log-dir=<path>]`

- **kitchen_id** (required) — The kitchen session identifier to scope log queries.
- **--dispatch-id** (optional) — Fleet dispatch identifier. When non-empty, write a structured JSON report file in addition to text output.
- **--diagnostics-log-dir** (optional) — Resolved path to the diagnostics log directory. Defaults to ~/.local/share/autoskillit/logs/ if not provided.

## Critical Constraints

**NEVER:**
- Fabricate, embellish, or invent findings not supported by evidence in session data
- Modify any source code files
- Create issues or PRs (findings are reported to the calling session only)
- Write to `/tmp`, `/var/tmp`, or any system scratch directory — all intermediate and scratch files belong in `{{AUTOSKILLIT_TEMP}}/analyze-pipeline-health/`
- Detach child delegations instead of joining them (joining every child is required)
- Start independent child delegations sequentially

**ALWAYS:**
- Filter sessions.jsonl by kitchen_id to scope to this pipeline run
- Route every step group through an `autoskillit:session-log-reader` subagent
- Spawn no more than six reader subagents in one run
- Leave child model, effort, sandbox, feature, and tool policy to the loaded AgentDef
- Include a wall-clock soft-deadline instruction in each reader packet
- Report "no issues found" clearly when the pipeline is clean
- Start all independent child delegations before awaiting any result to maximize concurrency

## Workflow

### Step 0: Resolve output directory

Resolve the scratch directory for any intermediate files produced during analysis:

1. Use `{{AUTOSKILLIT_TEMP}}/analyze-pipeline-health/` as the scratch directory
2. Create the directory if it does not exist (use Bash: `mkdir -p`)
3. All intermediate files (partial reader results, working notes) go here — never in `/tmp` or `/var/tmp`

Note: The final JSON report (Step 5) writes to the diagnostics log directory (`health-reports/`), not to this scratch directory.

### Step 1: Read sessions.jsonl

Read ~/.local/share/autoskillit/logs/sessions.jsonl and filter entries where kitchen_id matches the provided argument.
Count the filtered rows programmatically. Reconcile that count with the ordered session
IDs and, later, the sum of all reader-packet session counts; a mismatch is a blocked
coverage result, never a prose estimate.

### Step 2: Group by step_name

Group the filtered entries by step_name. Each group represents one phase of the pipeline (e.g., plan, implement, test, merge).

### Step 2b: Build reader packets

Assign every step group a stable batch ID. If there are at most six groups, build one
reader packet per group. If there are more than six, combine adjacent whole step groups
into no more than six packets while preserving group and session order. Never split a
step group or omit one. Each reader packet contains:
- kitchen ID and one or more step-name/batch-ID identities
- the ordered session IDs and count for each batch, plus the packet total
- requested anomaly classes: failure subtype, retry pattern, timing outlier, and error signature
- the exact per-batch `Verdict`, `Batch`, `Evidence`, `Searched scope`, `Unsupported classes`,
  and `Unknowns` return envelope

Do not include `claude_code_log`, `codex_log`, a session directory, a diagnostics
root, or any other filesystem path. Do not copy numeric tool caps into packets;
the server is the sole bounds authority.

### Step 3: Spawn reader subagents (SINGLE MESSAGE)

**Start ALL independent child delegations before awaiting any result — one per reader packet,
at most six total — and join every child before synthesis.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

For each reader packet, spawn a terminal reader via the child delegation with
subagent_type: `autoskillit:session-log-reader`.

Each reader receives only its packet and this instruction: complete within 15
minutes and return `partial` or `blocked` with searched-scope evidence if the
bounded tool cannot support an anomaly class.

Issue ALL Agent calls in a single message for parallel execution.

### Step 4: Validate reader completion, diagnose, and consolidate findings

Collect results from all readers. For each result:
1. Require exactly one complete `Verdict: answered | partial | blocked` envelope for
   every batch in the packet and its exact batch/session identity.
2. Reject uncited claims, citations not returned by the tool, missing searched
   scope, or evidence for an unassigned session.
3. Treat `partial`, `blocked`, empty, or malformed results as coverage gaps; never
   silently promote them to complete evidence.

After every join, the parent alone interprets the evidence, correlates retry and
error patterns across batches, determines anomalies, and writes the report. Group
parent findings by severity and include citations plus coverage limitations. Report
"Pipeline health check: no issues found" only when every requested class has
adequate evidence and parent diagnosis finds no issue.

### Step 5: Write report file (fleet dispatches only)

If --dispatch-id was provided and is non-empty:

1. Build a JSON report object with these fields:
   - kitchen_id: the kitchen_id argument value
   - dispatch_id: the --dispatch-id value
   - timestamp: current UTC ISO 8601 timestamp
   - findings: array of finding objects from Step 4, each with severity, step_group, summary, evidence
   - summary: the human-readable consolidated report text from Step 4

2. Determine the report directory:
   - If --diagnostics-log-dir was provided and non-empty, use {diagnostics-log-dir}/health-reports/
   - Otherwise use ~/.local/share/autoskillit/logs/health-reports/

3. Write the JSON to {report_dir}/{dispatch_id}_health_report.json using the Write tool.
   Create the health-reports/ directory first if it doesn't exist (use Bash: mkdir -p {report_dir}).

If --dispatch-id was not provided or is empty, skip this step entirely.

### Step 6: Output

Present the consolidated report as your final output text. After the report body, emit the completion delimiter as the final line:

```
---pipeline-health-result---
```

The calling orchestrator session will receive this as the run_skill result.

## Backend-adapted semantic execution contract

- Claude maps the logical role to `autoskillit:session-log-reader`.
- Codex maps it to `session-log-reader` and dispatches once per runtime item in
  `reader_packets` with `fork_turns="none"`.
- Do not pass a spawn-time `model` or `reasoning_effort`; the loaded AgentDef is
  the sole Luna/xhigh policy authority.
- Start every independent child before joining any child, join every child, and
  deliver every successful terminal result before parent diagnosis.
