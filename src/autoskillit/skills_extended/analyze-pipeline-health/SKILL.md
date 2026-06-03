---
name: analyze-pipeline-health
backend_requirements: [claude-code]
uses_capabilities: [cross_skill_ref, run_skill]
categories: [diagnostics]
description: Analyze pipeline session logs for anomalies and regressions. Spawns parallel Haiku scanner subagents per step group, each investigating its batch of sessions and reporting findings with evidence.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: analyze-pipeline-health] Analyzing pipeline health...'"
          once: true
---

# Pipeline Health Analysis Skill

Coordinator skill that reads session logs from a pipeline run, groups them by step, spawns parallel scanner subagents, and consolidates findings into a report.

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
- Run subagents in the background (run_in_background: true is prohibited)
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Filter sessions.jsonl by kitchen_id to scope to this pipeline run
- Spawn scanner subagents in parallel (one per step group)
- Use model: "haiku" for scanner subagents
- Cap each scanner's investigation budget: set `maxTurns` to the limit in the agent definition and include a wall-clock soft-deadline instruction in the scanner prompt (e.g. "complete your analysis within 15 minutes; report partial findings if you reach the limit")
- Report "no issues found" clearly when the pipeline is clean
- Issue all Task calls in a single message to maximize parallelism

## Workflow

### Step 0: Resolve output directory

Resolve the scratch directory for any intermediate files produced during analysis:

1. Use `{{AUTOSKILLIT_TEMP}}/analyze-pipeline-health/` as the scratch directory
2. Create the directory if it does not exist (use Bash: `mkdir -p`)
3. All intermediate files (partial scanner results, working notes) go here — never in `/tmp` or `/var/tmp`

Note: The final JSON report (Step 5) writes to the diagnostics log directory (`health-reports/`), not to this scratch directory.

### Step 1: Read sessions.jsonl

Read ~/.local/share/autoskillit/logs/sessions.jsonl and filter entries where kitchen_id matches the provided argument.

### Step 2: Group by step_name

Group the filtered entries by step_name. Each group represents one phase of the pipeline (e.g., plan, implement, test, merge).

### Step 2b: Identify Codex sessions

Some sessions may use the Codex backend instead of Claude Code. These are identifiable by:
- `backend` field is `"codex"` in the sessions.jsonl entry
- `codex_log` is non-null while `claude_code_log` is null

When passing session entries to scanner subagents in Step 3, include the `codex_log` path and `backend` field in the JSON array. The scanner agent knows how to handle both backend types — it will use grep-based coarse analysis for Codex sessions and full JSONL analysis for Claude Code sessions.

No special grouping is needed — Codex and Claude Code sessions for the same step can be in the same scanner batch.

### Step 3: Spawn scanner subagents (SINGLE MESSAGE)

**Issue ALL Task tool calls in a single message — one per item — so they execute in parallel. Do NOT iterate across multiple turns.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

For each step group, spawn a scanner subagent via the Agent tool with subagent_type: "autoskillit:pipeline-health-scanner".

Each scanner receives in its prompt:
- The sessions.jsonl entries for its batch (JSON array — includes `claude_code_log`, `codex_log`, and `backend` fields)
- The session directory paths: ~/.local/share/autoskillit/logs/sessions/<dir_name>/
- Context about what the step does (derive from step_name)

Issue ALL Agent calls in a single message for parallel execution.

### Step 4: Validate scanner completion and consolidate findings

Collect results from all scanners. For each scanner result:
1. Check that the result contains a `scan_result:` completion token.
2. If a scanner result is empty or lacks the `scan_result:` token, record it as an anomaly finding with severity "anomaly" and summary "Scanner for step group '<name>' did not complete — results may be missing."
3. Only report "Pipeline health check: no issues found" when ALL scanners emitted `status: "complete"` tokens with `findings_count: 0`.

Produce a consolidated report:
- Group findings by severity (confirmed bugs > regressions > anomalies > informational)
- Include the scanner's evidence and adversarial validation status for each finding

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
